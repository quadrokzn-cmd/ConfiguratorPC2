# Фикстуры для тестов портала (этап 9Б.1).
#
# DB-инфраструктура (db_engine, db_session, миграции 001..018) живёт
# в корневом `tests/conftest.py`. Здесь только portal-специфичные
# фикстуры: чистка таблиц перед каждым тестом, создание пользователей
# и TestClient портала.

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _clean_tables(db_engine):
    """Перед каждым тестом — пустые таблицы, чтобы предыдущий тест
    не мешал."""
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE audit_log, sent_emails, specification_items, queries, "
            "projects, daily_budget_log, users, api_usage_log, exchange_rates "
            "RESTART IDENTITY CASCADE"
        ))
    yield


# ---- Создание пользователей --------------------------------------------


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
