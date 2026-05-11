# UI-4 (Путь B, 2026-05-11): фикстуры для тестов app/ (legacy-конфигуратор).
#
# После UI-4 в app/ остаётся только admin_router (/admin dashboard,
# /admin/budget, /admin/queries, /admin/users → 302 на portal) и
# catch-all 301-редиректы на portal/configurator. Эти тесты используют
# app_client_legacy — TestClient(app/main.py), чтобы проверять реальные
# admin-страницы конфигуратора и редиректы старых URL.
#
# Тесты самих /configurator/* URL переехали в tests/test_portal/test_configurator_*.py.

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _clean_tables(db_engine):
    """Перед каждым тестом — пустые таблицы, в которые тесты app/ пишут.
    Дублирует логику test_portal/conftest.py:_clean_tables — нужно,
    чтобы test_web/ работал независимо от test_portal/."""
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE audit_log, sent_emails, specification_items, queries, "
            "projects, daily_budget_log, users, api_usage_log, exchange_rates "
            "RESTART IDENTITY CASCADE"
        ))
    yield


# ---- Создание пользователей --------------------------------------------

def _create_user(
    session, *, login: str, password: str, role: str, name: str,
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
            "l": login, "p": hash_password(password), "r": role, "n": name,
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
    uid = _create_user(db_session, login="manager1", password="manager-pass",
                       role="manager", name="Менеджер 1")
    return {"id": uid, "login": "manager1", "password": "manager-pass"}


# ---- TestClient'ы ------------------------------------------------------

@pytest.fixture()
def app_client_legacy():
    """TestClient app-сервиса (config.quadro.tatar): админка + 301-редиректы."""
    from app.main import app
    with TestClient(app, follow_redirects=False) as c:
        yield c


@pytest.fixture()
def app_client(app_client_legacy):
    """Алиас под старое имя из bывшего conftest. UI-4 (Путь B):
    app_client теперь = app_client_legacy (только app/, без логина)."""
    return app_client_legacy


def _login_via_portal(client: TestClient, login: str, password: str) -> None:
    """Логин в portal-клиенте; портал ставит cookie на .quadro.tatar,
    которая шарится с app/main.py (тот же secret_key и cookie name)."""
    r = client.get("/login")
    assert r.status_code == 200, r.status_code
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    assert m, "csrf_token не найден"
    token = m.group(1)
    r = client.post(
        "/login",
        data={"login": login, "password": password, "csrf_token": token},
    )
    assert r.status_code in (302, 303), f"Логин не прошёл: {r.status_code}"


@pytest.fixture()
def admin_client_app(app_client_legacy, admin_user):
    from portal.main import app as portal_app
    with TestClient(portal_app, follow_redirects=False) as portal_client:
        _login_via_portal(portal_client, admin_user["login"], admin_user["password"])
        for k, v in portal_client.cookies.items():
            app_client_legacy.cookies.set(k, v)
    return app_client_legacy


@pytest.fixture()
def manager_client_app(app_client_legacy, manager_user):
    from portal.main import app as portal_app
    with TestClient(portal_app, follow_redirects=False) as portal_client:
        _login_via_portal(portal_client, manager_user["login"], manager_user["password"])
        for k, v in portal_client.cookies.items():
            app_client_legacy.cookies.set(k, v)
    return app_client_legacy
