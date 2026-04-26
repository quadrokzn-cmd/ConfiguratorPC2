# Фикстуры для тестов портала (этап 9Б.1).
#
# Структурно повторяют tests/test_web/conftest.py, но:
#   - проливают миграции 001..017 (включая users.permissions);
#   - стартуют TestClient на portal.main:app, а не app.main:app;
#   - выставляют env-переменные ALLOWED_REDIRECT_HOSTS и PORTAL_URL/
#     CONFIGURATOR_URL так, чтобы тесты редиректов давали стабильные
#     значения.
#
# Тестовая БД configurator_pc_test пересоздаётся один раз на сессию.
# Состояние таблиц очищается перед каждым тестом — TRUNCATE с RESTART
# IDENTITY CASCADE.

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


# Локальные дефолты env (тестовый раннер). Переключение DATABASE_URL на
# TEST_DATABASE_URL делается в корневом tests/conftest.py.
os.environ.setdefault("PORTAL_URL", "http://localhost:8081")
os.environ.setdefault("CONFIGURATOR_URL", "http://localhost:8080")
os.environ.setdefault("ALLOWED_REDIRECT_HOSTS", "localhost:8080,localhost:8081")


# Все миграции 001..017. Список повторяется в tests/test_web/conftest.py —
# это намеренно: каждая тест-папка может быть прогнана независимо, и
# обе используют одну тестовую БД (configurator_pc_test).
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
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _drop_all(engine) -> None:
    """Чистый снос — те же таблицы, что в test_web/conftest.py
    (плюс ничего нового от миграции 017: 017 только ALTER TABLE)."""
    tables = [
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
    """Движок тестовой БД, миграции 001..017 один раз на сессию pytest."""
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
def _clean_tables(db_engine):
    """Перед каждым тестом — пустые таблицы, чтобы предыдущий тест
    не мешал."""
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE sent_emails, specification_items, queries, "
            "projects, daily_budget_log, users, api_usage_log, exchange_rates "
            "RESTART IDENTITY CASCADE"
        ))
    yield


@pytest.fixture()
def db_session(db_engine):
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()


# ---- Создание пользователей --------------------------------------------

import json


def _create_user(
    session,
    *,
    login: str,
    password: str,
    role: str,
    name: str,
    permissions: dict | None = None,
) -> int:
    from shared.auth import hash_password
    perms = permissions if permissions is not None else (
        {} if role == "admin" else {"configurator": True}
    )
    row = session.execute(
        text(
            "INSERT INTO users (login, password_hash, role, name, permissions) "
            "VALUES (:l, :p, :r, :n, CAST(:perms AS JSONB)) RETURNING id"
        ),
        {
            "l":     login,
            "p":     hash_password(password),
            "r":     role,
            "n":     name,
            "perms": json.dumps(perms, ensure_ascii=False),
        },
    ).first()
    session.commit()
    return int(row.id)


@pytest.fixture()
def admin_user(db_session):
    uid = _create_user(db_session, login="admin", password="admin-pass",
                       role="admin", name="Администратор")
    return {"id": uid, "login": "admin", "password": "admin-pass"}


@pytest.fixture()
def manager_user(db_session):
    """Менеджер с дефолтными правами (configurator: True)."""
    uid = _create_user(db_session, login="manager1", password="manager-pass",
                       role="manager", name="Менеджер 1")
    return {"id": uid, "login": "manager1", "password": "manager-pass"}


@pytest.fixture()
def manager_user_no_perms(db_session):
    """Менеджер без единого разрешённого модуля."""
    uid = _create_user(
        db_session, login="manager_empty", password="manager-pass",
        role="manager", name="Менеджер без прав", permissions={},
    )
    return {"id": uid, "login": "manager_empty", "password": "manager-pass"}


# ---- TestClient'ы портала ---------------------------------------------

@pytest.fixture()
def portal_client():
    """TestClient портала без залогиненной сессии."""
    from portal.main import app
    with TestClient(app, follow_redirects=False) as c:
        yield c


def extract_csrf(html: str) -> str:
    import re
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "csrf_token не найден на странице"
    return m.group(1)


def _login_via_portal(client: TestClient, login: str, password: str) -> None:
    r = client.get("/login")
    assert r.status_code == 200, r.status_code
    token = extract_csrf(r.text)
    r = client.post(
        "/login",
        data={"login": login, "password": password, "csrf_token": token},
    )
    assert r.status_code in (302, 303), f"login failed: {r.status_code} {r.text[:200]}"


@pytest.fixture()
def admin_portal_client(portal_client, admin_user):
    _login_via_portal(portal_client, admin_user["login"], admin_user["password"])
    return portal_client


@pytest.fixture()
def manager_portal_client(portal_client, manager_user):
    _login_via_portal(portal_client, manager_user["login"], manager_user["password"])
    return portal_client
