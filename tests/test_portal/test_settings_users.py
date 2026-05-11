# Тесты /settings/users портала: создание, активация, permissions
# (бывший /admin/users; перенос на этапе UI-3 Пути B, 2026-05-11).

from __future__ import annotations

import json

from sqlalchemy import text as _t

from tests.test_portal.conftest import extract_csrf


def test_admin_can_open_admin_users(admin_portal_client):
    r = admin_portal_client.get("/settings/users")
    assert r.status_code == 200
    # 9Б.5: заголовок формы — «Создать пользователя» (вместо «Создать менеджера»),
    # т.к. в форме появился селект роли (admin/manager).
    assert "Создать пользователя" in r.text


def test_manager_cannot_open_admin_users(manager_portal_client):
    r = manager_portal_client.get("/settings/users")
    assert r.status_code == 403


def test_admin_creates_manager_with_default_permissions(
    admin_portal_client, db_session
):
    r = admin_portal_client.get("/settings/users")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        "/settings/users",
        data={
            "login":      "newmanager",
            "name":       "Новый менеджер",
            "password":   "secure-pass",
            "csrf_token": token,
        },
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/settings/users"

    row = db_session.execute(
        _t(
            "SELECT login, role, name, is_active, permissions "
            "FROM users WHERE login = 'newmanager'"
        )
    ).first()
    assert row is not None
    assert row.role == "manager"
    assert row.name == "Новый менеджер"
    assert row.is_active is True
    perms = row.permissions if isinstance(row.permissions, dict) else json.loads(row.permissions)
    assert perms == {"configurator": True}


def test_admin_updates_permissions(
    admin_portal_client, manager_user, db_session
):
    """Снимаем единственный чекбокс — у конфигуратора false, остальные
    модули тоже сохраняются как false (на стороне репо записываются
    все известные ключи)."""
    r = admin_portal_client.get("/settings/users")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        f"/settings/users/{manager_user['id']}/permissions",
        # Не передаём permissions[configurator] — значит снят.
        data={"csrf_token": token},
    )
    assert r.status_code == 302

    row = db_session.execute(
        _t("SELECT permissions FROM users WHERE id = :id"),
        {"id": manager_user["id"]},
    ).first()
    perms = row.permissions if isinstance(row.permissions, dict) else json.loads(row.permissions)
    assert perms.get("configurator") is False


def test_admin_grants_permission(
    admin_portal_client, manager_user_no_perms, db_session
):
    """Менеджер без прав → отмечаем чекбокс → permissions[configurator]=True."""
    r = admin_portal_client.get("/settings/users")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        f"/settings/users/{manager_user_no_perms['id']}/permissions",
        data={
            "csrf_token":              token,
            "permissions[configurator]": "1",
        },
    )
    assert r.status_code == 302

    row = db_session.execute(
        _t("SELECT permissions FROM users WHERE id = :id"),
        {"id": manager_user_no_perms["id"]},
    ).first()
    perms = row.permissions if isinstance(row.permissions, dict) else json.loads(row.permissions)
    assert perms.get("configurator") is True


def test_duplicate_login_rejected(admin_portal_client, manager_user):
    r = admin_portal_client.get("/settings/users")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        "/settings/users",
        data={
            "login":      "manager1",
            "name":       "Дубликат",
            "password":   "secure-pass",
            "csrf_token": token,
        },
    )
    assert r.status_code == 302
    r = admin_portal_client.get("/settings/users")
    assert "уже занят" in r.text


def test_short_password_rejected(admin_portal_client):
    r = admin_portal_client.get("/settings/users")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        "/settings/users",
        data={
            "login":      "shortpw",
            "name":       "Тест",
            "password":   "abc",  # < 6
            "csrf_token": token,
        },
    )
    assert r.status_code == 302
    r = admin_portal_client.get("/settings/users")
    assert "не короче 6" in r.text


def test_toggle_user_active(admin_portal_client, manager_user, db_session):
    r = admin_portal_client.get("/settings/users")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        f"/settings/users/{manager_user['id']}/toggle",
        data={"csrf_token": token},
    )
    assert r.status_code == 302
    row = db_session.execute(
        _t("SELECT is_active FROM users WHERE id = :id"),
        {"id": manager_user["id"]},
    ).first()
    assert row.is_active is False


def test_cannot_deactivate_self(admin_portal_client, admin_user, db_session):
    r = admin_portal_client.get("/settings/users")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        f"/settings/users/{admin_user['id']}/toggle",
        data={"csrf_token": token},
    )
    assert r.status_code == 302
    row = db_session.execute(
        _t("SELECT is_active FROM users WHERE id = :id"),
        {"id": admin_user["id"]},
    ).first()
    assert row.is_active is True


def test_home_shows_tile_when_permission(manager_portal_client):
    """У manager_user есть configurator: True — плитка должна быть."""
    r = manager_portal_client.get("/")
    assert r.status_code == 200
    assert "Конфигуратор ПК" in r.text


def test_home_empty_state_when_no_permissions(portal_client, manager_user_no_perms):
    """Менеджер без прав — на главной плашки модулей нет, видна подсказка
    про администратора.

    UI-1 (Путь B): пункт «Конфигуратор ПК» в самом sidebar виден всем
    (RBAC-фильтрация меню отложена на этап после UI-5) — но плашка
    модуля на главной по-прежнему скрыта без права."""
    from tests.test_portal.conftest import _login_via_portal
    _login_via_portal(
        portal_client,
        manager_user_no_perms["login"], manager_user_no_perms["password"],
    )
    r = portal_client.get("/")
    assert r.status_code == 200
    assert "обратитесь к администратору" in r.text
    # Плашки модулей в основной части страницы нет.
    assert 'data-testid="tile-configurator"' not in r.text
    assert 'data-testid="tile-auctions"' not in r.text
