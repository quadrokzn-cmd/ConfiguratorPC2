# Корневой conftest всех тестов проекта.
#
# Делает две вещи:
#
# 1. До любого импорта shared.db переключает DATABASE_URL на
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
from urllib.parse import urlsplit, urlunsplit

from dotenv import load_dotenv

# Сначала читаем .env (если есть), чтобы достать TEST_DATABASE_URL.
load_dotenv()

_BASE_TEST_DB = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/configurator_pc_test",
)


def _worker_database_url(base_url: str) -> str:
    """Этап 11.7: при параллельном прогоне через pytest-xdist каждый
    worker (gw0, gw1, …) работает со своей БД, чтобы не конфликтовать
    на TRUNCATE/INSERT/DROP. PYTEST_XDIST_WORKER ставится дочерним
    процессам самим xdist; в master-процессе и при последовательном
    прогоне (-p no:xdist / -n0) переменной нет — тогда оставляем имя
    БД как было, ради совместимости с уже созданной БД разработчика.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    if not worker:
        return base_url
    parts = urlsplit(base_url)
    db_name = parts.path.lstrip("/") or "configurator_pc_test"
    new_path = "/" + f"{db_name}_{worker}"
    return urlunsplit(
        (parts.scheme, parts.netloc, new_path, parts.query, parts.fragment)
    )


_TEST_DB = _worker_database_url(_BASE_TEST_DB)
# Сохраняем worker-aware значение, чтобы любой код, который позже прочитает
# TEST_DATABASE_URL (например, shared.config.settings), увидел уже правильное.
os.environ["TEST_DATABASE_URL"] = _TEST_DB
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

# Этап 11.7: ускоряем bcrypt в тестах. По умолчанию shared/auth.py
# использует rounds=12 (~150 мс на хеш) — нормальная цена для прода,
# но в каждом тесте через manager_client/admin_client идёт hash + verify,
# а в некоторых тестах ещё и каскад из 2-3 пользователей. Понижение до
# rounds=4 (~5 мс) снижает setup-время на 0.3-0.5 сек на тест без потери
# смысла теста: hash/verify-пара и так корректно работает на любых
# rounds (число rounds зашито в сам хеш). Прод не задевается — модуль
# shared/auth читает _BCRYPT_ROUNDS из своих globals при каждом вызове
# hash_password, и в pytest-процессе мы переписываем именно эту глобаль.
import shared.auth as _shared_auth

_shared_auth._BCRYPT_ROUNDS = 4


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
    "019_add_new_suppliers.sql",
    "020_supplier_emails.sql",
    "021_price_uploads_report_json.sql",
    "022_supplier_prices_raw_name.sql",
    "023_component_field_sources_source_detail.sql",
    "028_auto_price_loads.sql",
    "029_auto_price_load_runs_source_ref.sql",
    # 030_auctions_tables.sql — нужна тестам портала /auctions* (этап 9a).
    "030_auctions_tables.sql",
    "031_printers_mfu.sql",
    # 032_matches_fk.sql — FK matches.nomenclature_id → printers_mfu(id);
    # требует обе таблицы (030+031), теперь применяем.
    "032_matches_fk.sql",
    # 033_users_auctions_permissions.sql — дефолты прав auctions для
    # admin/manager (этап 7 слияния).
    "033_users_auctions_permissions.sql",
    # 034_auctions_ingest_settings.sql — добавляет ключ
    # auctions_ingest_enabled в settings (этап 8 слияния).
    "034_auctions_ingest_settings.sql",
    # 0036_resurs_media_notifications.sql — таблица для уведомлений
    # SOAP-операции Notification «Ресурс Медиа» (мини-этап 2026-05-12).
    # 0035 пропущен намеренно — она создаёт PG-роль ingest_writer
    # (этап 9e.1) и не нужна для тестов.
    "0036_resurs_media_notifications.sql",
    # 0037_resurs_media_catalog.sql — локальный образ каталога РМ
    # для инкрементальной дельты GetMaterialData (мини-этап 2026-05-12).
    "0037_resurs_media_catalog.sql",
    # 0039_auctions_smart_ingest.sql — smart-ingest аукционов:
    # tenders.content_hash + last_modified_at, FK matches/tender_items
    # на NO ACTION (мини-этап 2026-05-16, блокер Волны 3 Telegram-уведомлений).
    # 0038 пропущена — она backfill для supplier_prices_mfu, не нужна для тестов.
    "0039_auctions_smart_ingest.sql",
]

# Все таблицы, которые могут быть созданы любой миграцией. DROP CASCADE
# идёт по этому списку перед накатом миграций. Системные таблицы Postgres
# здесь специально нет — `DROP TABLE IF EXISTS` на каждое имя в этом списке
# по дизайну безопасно.
_ALL_TABLES = [
    "auto_price_load_runs",     # 028
    "auto_price_loads",         # 028
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
    "printers_mfu",             # 031 (Этап 6 слияния QT↔C-PC2)
    "resurs_media_notifications",  # 0036 (мини-этап 2026-05-12 Resurs Media Notification)
    "resurs_media_catalog",        # 0037 (мини-этап 2026-05-12 Resurs Media catalog delta)
    # Аукционные таблицы (миграция 030, Этап 5 слияния).
    "matches",                  # 030
    "tender_status",            # 030
    "tender_items",             # 030
    "tenders",                  # 030
    "settings",                 # 030
    "excluded_regions",         # 030
    "ktru_watchlist",           # 030
    "ktru_catalog",             # 030
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


def _ensure_worker_database_exists(db_url: str) -> None:
    """Этап 11.7: гарантируем, что worker-aware БД (configurator_pc_test_gwN)
    существует. CREATE DATABASE нельзя выполнить внутри транзакции — открываем
    отдельное соединение в режиме AUTOCOMMIT к служебной БД 'postgres'.
    Если БД уже есть — не трогаем (повторные прогоны должны быть быстрыми)."""
    parts = urlsplit(db_url)
    target_db = parts.path.lstrip("/")
    if not target_db:
        return
    admin_url = urlunsplit(
        (parts.scheme, parts.netloc, "/postgres", parts.query, parts.fragment)
    )
    admin_engine = create_engine(
        admin_url,
        future=True,
        isolation_level="AUTOCOMMIT",
        connect_args={"client_encoding": "utf8"},
    )
    try:
        with admin_engine.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :n"),
                {"n": target_db},
            ).first()
            if exists:
                return
            # LC_COLLATE/LC_CTYPE='C' — как в README, чтобы тестовая БД
            # не зависела от локали Windows.
            conn.execute(
                text(
                    f'CREATE DATABASE "{target_db}" '
                    f"ENCODING 'UTF8' LC_COLLATE 'C' LC_CTYPE 'C' "
                    f"TEMPLATE template0"
                )
            )
    finally:
        admin_engine.dispose()


@pytest.fixture(scope="session")
def db_engine():
    """Единый SQLAlchemy-engine на тестовую БД для всего прогона.

    Один раз за сессию pytest (то есть — один раз на каждого xdist-worker'а):
    DROP всех таблиц + накат всех миграций. Все подкаталоги тестов используют
    именно эту фикстуру.
    """
    from shared.config import settings

    # При параллельном прогоне у каждого worker'а своя БД — её нужно создать
    # на лету при первом запуске; повторные прогоны находят БД и пропускают
    # CREATE.
    _ensure_worker_database_exists(settings.test_database_url)

    # client_encoding=utf8 — защита от UnicodeDecodeError на русской Windows
    # (аналогичный фикс в shared/db.py).
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
