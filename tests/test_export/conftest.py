# Фикстуры для тестов пакета export (этап 8).
#
# Дублируют по форме test_web/conftest.py — у pytest нет способа
# подтянуть фикстуры из другого conftest, а переносить их в корневой
# conftest слишком инвазивно (ломает существующие unit-тесты NLU,
# которые БД не трогают).
#
# Схема создаётся один раз на сессию, миграции включают 011_email_support.sql
# (этап 8.3). Перед каждым тестом чистятся таблицы, в которые тесты пишут.

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


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
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _drop_all_known_tables(engine) -> None:
    tables = [
        "sent_emails",
        "unmapped_supplier_items",
        "specification_items",
        "queries", "projects", "daily_budget_log", "users",
        "api_usage_log", "component_field_sources",
        "price_uploads", "supplier_prices", "suppliers",
        "cpus", "motherboards", "rams", "gpus", "storages",
        "cases", "psus", "coolers",
    ]
    with engine.begin() as conn:
        for t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))


def _apply_migrations(engine) -> None:
    root = _project_root() / "migrations"
    for name in _MIGRATIONS:
        sql = (root / name).read_text(encoding="utf-8")
        with engine.begin() as conn:
            conn.execute(text(sql))


@pytest.fixture(scope="session")
def db_engine():
    from app.config import settings
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
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _clean_tables_export(db_engine):
    """Чистые таблицы перед каждым тестом пакета export.

    Компоненты, поставщики и цены тоже — потому что тесты email_composer
    создают своих поставщиков под именами "A-Sup"/"MPN-Sup" и т.п., и
    было бы неудобно ловить конфликты из предыдущих запусков.
    """
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE sent_emails, specification_items, queries, "
            "projects, daily_budget_log, users, api_usage_log, "
            "supplier_prices, unmapped_supplier_items, "
            "cpus, motherboards, rams, gpus, storages, cases, psus, coolers "
            "RESTART IDENTITY CASCADE"
        ))
        # suppliers чистим отдельно: миграция 009 заливает Merlion/Treolan,
        # нам в тестах это не мешает, но удобнее иметь пустую таблицу, чтобы
        # _insert_supplier(name='A-Sup') не натыкался на чужие строки.
        conn.execute(text(
            "TRUNCATE TABLE suppliers RESTART IDENTITY CASCADE"
        ))
    yield
