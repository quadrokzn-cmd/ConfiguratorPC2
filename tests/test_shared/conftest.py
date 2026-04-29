# Локальный conftest для tests/test_shared.
#
# Тесты shared/audit.py пишут реальные строки в audit_log, поэтому им
# нужна та же тестовая БД, что и портальным/веб-тестам. Чтобы не
# дублировать большой conftest, переиспользуем фикстуры из test_portal.

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


# Те же env, что в test_portal/conftest.py — нужны для импорта portal.main
# в smoke-тестах, и не мешают тестам shared/audit.
os.environ.setdefault("PORTAL_URL", "http://localhost:8081")
os.environ.setdefault("CONFIGURATOR_URL", "http://localhost:8080")
os.environ.setdefault("ALLOWED_REDIRECT_HOSTS", "localhost:8080,localhost:8081")


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


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _drop_all(engine) -> None:
    tables = [
        "audit_log",
        "exchange_rates",
        "sent_emails",
        "unmapped_supplier_items",
        "specification_items",
        "queries", "projects", "daily_budget_log", "users",
        "api_usage_log",
        "component_field_sources",
        "price_uploads", "supplier_prices", "suppliers",
        "cpus", "motherboards", "rams", "gpus", "storages",
        "cases", "psus", "coolers",
        "schema_migrations",
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
        _drop_all(engine)
        _apply_migrations(engine)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean_audit_log(db_engine):
    """Чистим audit_log перед каждым тестом, остальные таблицы трогать
    не нужно — этот conftest изолирован от test_portal/test_web."""
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY"))
    yield


@pytest.fixture()
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
