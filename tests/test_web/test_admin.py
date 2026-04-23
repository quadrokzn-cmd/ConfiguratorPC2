# Тесты админки: создание пользователей, деактивация.

from __future__ import annotations

from sqlalchemy import text as _t

from tests.test_web.conftest import extract_csrf


def test_admin_dashboard_returns_200(admin_client):
    """Регрессия Этапа 7: /admin/dashboard был 404 — сейчас 301 на /admin."""
    # Без follow_redirects: проверяем сам редирект.
    r = admin_client.get("/admin/dashboard", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == "/admin"

    # С follow_redirects: финальная страница отдаёт 200.
    r = admin_client.get("/admin/dashboard", follow_redirects=True)
    assert r.status_code == 200


def test_admin_can_create_manager(admin_client, db_session):
    r = admin_client.get("/admin/users")
    token = extract_csrf(r.text)
    r = admin_client.post(
        "/admin/users",
        data={
            "login":      "newmanager",
            "name":       "Новый Менеджер",
            "password":   "secure-pass",
            "csrf_token": token,
        },
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/admin/users"

    row = db_session.execute(
        _t("SELECT login, role, name, is_active FROM users WHERE login='newmanager'")
    ).first()
    assert row is not None
    assert row.role == "manager"
    assert row.name == "Новый Менеджер"
    assert row.is_active is True


def test_duplicate_login_rejected(admin_client, manager_user):
    r = admin_client.get("/admin/users")
    token = extract_csrf(r.text)
    r = admin_client.post(
        "/admin/users",
        data={
            "login":      "manager1",      # уже есть
            "name":       "Другое имя",
            "password":   "anypass",
            "csrf_token": token,
        },
    )
    assert r.status_code == 302
    # На следующем GET-е должен показаться flash_error
    r = admin_client.get("/admin/users")
    assert "уже занят" in r.text


def test_short_password_rejected(admin_client):
    r = admin_client.get("/admin/users")
    token = extract_csrf(r.text)
    r = admin_client.post(
        "/admin/users",
        data={
            "login":      "shortpw",
            "name":       "Тест",
            "password":   "abc",  # < 6
            "csrf_token": token,
        },
    )
    assert r.status_code == 302
    r = admin_client.get("/admin/users")
    assert "не короче 6" in r.text


def test_toggle_user_active(admin_client, manager_user, db_session):
    r = admin_client.get("/admin/users")
    token = extract_csrf(r.text)
    r = admin_client.post(
        f"/admin/users/{manager_user['id']}/toggle",
        data={"csrf_token": token},
    )
    assert r.status_code == 302
    row = db_session.execute(
        _t("SELECT is_active FROM users WHERE id = :id"),
        {"id": manager_user["id"]},
    ).first()
    assert row.is_active is False


def test_cannot_deactivate_self(admin_client, admin_user, db_session):
    r = admin_client.get("/admin/users")
    token = extract_csrf(r.text)
    r = admin_client.post(
        f"/admin/users/{admin_user['id']}/toggle",
        data={"csrf_token": token},
    )
    assert r.status_code == 302
    row = db_session.execute(
        _t("SELECT is_active FROM users WHERE id = :id"),
        {"id": admin_user["id"]},
    ).first()
    # Остался активен
    assert row.is_active is True


def test_manager_cannot_create_user(manager_client):
    r = manager_client.post(
        "/admin/users",
        data={
            "login":      "hacker",
            "name":       "Взломщик",
            "password":   "secure-pass",
            "csrf_token": "whatever",
        },
    )
    assert r.status_code == 403
