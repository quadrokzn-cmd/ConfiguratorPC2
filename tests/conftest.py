# Корневой conftest всех тестов проекта.
#
# Делает две вещи:
#
# 1. До любого импорта app.database переключает DATABASE_URL на
#    TEST_DATABASE_URL — чтобы код не открыл движок на боевую БД.
#
# 2. Поднимает единую тестовую БД-инфраструктуру:
#       - session-scoped `db_engine` — один раз за прогон pytest:
#         DROP всех известных таблиц + CREATE через ВСЕ миграции 001..018.
#       - function-scoped `db_session` — открывает SQLAlchemy-сессию,
#         тест использует её для подготовки/проверки данных.
#
#    Подкаталоги test_web/test_portal/test_export/test_shared/
#    test_price_loaders больше НЕ создают свои `db_engine` / `db_session` —
#    они используют этот, единый. Таким образом полный прогон
#    `pytest tests/` и любые комбинации директорий
#    (`pytest tests/test_export/ tests/test_web/`) работают
#    одинаково: миграции применяются один раз, и наборы таблиц/
#    схема согласованы.
#
#    Этап 9Г.2: до унификации каждый conftest имел собственный engine
#    с разным списком миграций (test_export — 001..014; test_web /
#    test_portal / test_shared — 001..018; test_price_loaders —
#    001..010 + 013) и разным набором DROP. При прогоне нескольких
#    папок подряд второй conftest пытался применить часть миграций
#    повторно поверх таблиц, оставшихся от первого, и часть тестов
#    падала. Теперь источник истины один — этот файл.
#
#    Локальные conftest'ы оставляют только специфичные для своего
#    домена фикстуры (TestClient, mock_process_query, фабрики Excel
#    и т.п.) и autouse-функции, чистящие свои таблицы перед каждым
#    тестом (TRUNCATE).

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Сначала читаем .env (если есть), чтобы достать TEST_DATABASE_URL.
load_dotenv()

_TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/configurator_pc_test",
)
os.environ["DATABASE_URL"] = _TEST_DB

# Тестовый OPENAI_API_KEY: гарантируем, что ни один тест не уходит в сеть.
os.environ.setdefault("OPENAI_API_KEY", "sk-test-stub")

# Тестовый секрет сессии.
os.environ.setdefault("SESSION_SECRET_KEY", "test-session-secret")

# Эти env-переменные нужны и порталу (build_session_cookie_kwargs
# одинаковый), и для редиректа неавторизованных в конфигураторе.
os.environ.setdefault("PORTAL_URL", "http://localhost:8081")
os.environ.setdefault("CONFIGURATOR_URL", "http://localhost:8080")
os.environ.setdefault("ALLOWED_REDIRECT_HOSTS", "localhost:8080,localhost:8081")


import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


# Все миграции, в порядке применения. Один источник истины для всех
# подкаталогов тестов.
_MIGRATIONS = [
    "001_init.sql",
    "002_add_currency_and_relax_nullability.sql",
    "003_widen_model_column.sql",
    "004_add_component_field_sources.sql",
    "005_add_source_url_to_component_field_sources.sql",
    "006_add_api_usage_log.sql",
    "007_web_service.sql",
    "008_project_specification.sql",
    "009_multi_supplier_and_gtin.sql",
    "010_unmapped_score.sql",
    "011_email_support.sql",
    "012_supplier_contact_person.sql",
    "013_components_is_hidden.sql",
    "014_specification_recalculated_at.sql",
    "015_exchange_rates_table.sql",
    "016_specification_items_parsed_query.sql",
    "017_add_user_permissions.sql",
    "018_audit_log.sql",
]

# Все таблицы, которые могут быть созданы любой миграцией. DROP CASCADE
# идёт по этому списку перед накатом миграций. Системные таблицы Postgres
# здесь специально нет — `DROP TABLE IF EXISTS` на каждое имя в этом списке
# по дизайну безопасно.
_ALL_TABLES = [
    "audit_log",                # 018
    "exchange_rates",           # 015
    "sent_emails",              # 011
    "unmapped_supplier_items",  # 009
    "specification_items",      # 008
    "queries",                  # 007
    "projects",                 # 007
    "daily_budget_log",         # 007
    "users",                    # 007
    "api_usage_log",            # 006
    "component_field_sources",  # 004
    "price_uploads",            # 001
    "supplier_prices",          # 001
    "suppliers",                # 001
    "cpus",                     # 001
    "motherboards",             # 001
    "rams",                     # 001
    "gpus",                     # 001
    "storages",                 # 001
    "cases",                    # 001
    "psus",                     # 001
    "coolers",                  # 001
    # Журнал применённых миграций (миграционный раннер на проде, в тестах
    # не нужен) — дропаем для подстраховки от чистого CREATE TABLE.
    "schema_migrations",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _drop_all_known_tables(engine) -> None:
    """DROP TABLE IF EXISTS … CASCADE для всех известных таблиц."""
    with engine.begin() as conn:
        for t in _ALL_TABLES:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))


def _apply_migrations(engine) -> None:
    """Применяет все миграции 001..018 в порядке."""
    root = _project_root() / "migrations"
    for name in _MIGRATIONS:
        sql = (root / name).read_text(encoding="utf-8")
        with engine.begin() as conn:
            conn.execute(text(sql))


@pytest.fixture(scope="session")
def db_engine():
    """Единый SQLAlchemy-engine на тестовую БД для всего прогона.

    Один раз за сессию pytest: DROP всех таблиц + накат миграций 001..018.
    Все подкаталоги тестов используют именно эту фикстуру.
    """
    from app.config import settings

    # client_encoding=utf8 — защита от UnicodeDecodeError на русской Windows
    # (аналогичный фикс в app/database.py).
    engine = create_engine(
        settings.test_database_url,
        future=True,
        connect_args={"client_encoding": "utf8"},
    )
    try:
        _drop_all_known_tables(engine)
        _apply_migrations(engine)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    """SQLAlchemy-сессия для теста. Чистоту таблиц обеспечивают
    autouse-фикстуры локальных conftest'ов (по своему набору таблиц)."""
    Session = sessionmaker(
        bind=db_engine, autoflush=False, autocommit=False, future=True,
    )
    s = Session()
    try:
        yield s
    finally:
        s.close()
