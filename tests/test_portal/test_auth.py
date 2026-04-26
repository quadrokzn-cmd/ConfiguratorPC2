# Тесты login/logout портала и защиты ?next= от open redirect (этап 9Б.1).

from __future__ import annotations

from urllib.parse import quote

from tests.test_portal.conftest import extract_csrf


def test_login_form_returns_200(portal_client):
    r = portal_client.get("/login")
    assert r.status_code == 200
    assert "csrf_token" in r.text


def test_login_success_redirects_to_root(portal_client, manager_user):
    r = portal_client.get("/login")
    token = extract_csrf(r.text)
    r = portal_client.post(
        "/login",
        data={
            "login":      "manager1",
            "password":   "manager-pass",
            "csrf_token": token,
        },
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_login_wrong_password_shows_error(portal_client, manager_user):
    r = portal_client.get("/login")
    token = extract_csrf(r.text)
    r = portal_client.post(
        "/login",
        data={"login": "manager1", "password": "WRONG", "csrf_token": token},
    )
    assert r.status_code == 401
    assert "Неверный логин или пароль" in r.text


def test_login_redirects_to_allowed_host(portal_client, manager_user):
    """?next=http://localhost:8080/ — разрешённый хост, редирект уходит туда."""
    r = portal_client.get("/login")
    token = extract_csrf(r.text)
    target = "http://localhost:8080/"
    r = portal_client.post(
        "/login",
        data={
            "login":      "manager1",
            "password":   "manager-pass",
            "csrf_token": token,
            "next":       target,
        },
    )
    assert r.status_code == 302
    assert r.headers["location"] == target


def test_login_rejects_foreign_host(portal_client, manager_user):
    """?next=http://evil.com/ — не в whitelist, редирект на /."""
    r = portal_client.get("/login")
    token = extract_csrf(r.text)
    r = portal_client.post(
        "/login",
        data={
            "login":      "manager1",
            "password":   "manager-pass",
            "csrf_token": token,
            "next":       "http://evil.com/x",
        },
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_login_protocol_relative_url_blocked(portal_client, manager_user):
    """next='//evil.com' — protocol-relative URL: тоже не должно
    утаскивать пользователя на чужой хост."""
    r = portal_client.get("/login")
    token = extract_csrf(r.text)
    r = portal_client.post(
        "/login",
        data={
            "login":      "manager1",
            "password":   "manager-pass",
            "csrf_token": token,
            "next":       "//evil.com/path",
        },
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_logout_clears_session(manager_portal_client):
    """GET /logout (по 9Б.1 разрешён) сбрасывает сессию."""
    r = manager_portal_client.get("/logout")
    assert r.status_code == 302
    assert r.headers["location"] == "/login"
    # После logout / снова требует логина → 302 на /login?next=...
    r = manager_portal_client.get("/")
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login")


def test_logout_post_with_csrf(manager_portal_client):
    """POST /logout — старая совместимая форма с CSRF."""
    r = manager_portal_client.get("/")
    token = extract_csrf(r.text)
    r = manager_portal_client.post("/logout", data={"csrf_token": token})
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_unauthenticated_root_redirects_to_login(portal_client):
    r = portal_client.get("/")
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login?next=")
    # next указывает на http://testserver/ — это сам портал
    assert quote("http://testserver/", safe="") in r.headers["location"]


def test_deactivated_user_cannot_login(portal_client, db_session, manager_user):
    from sqlalchemy import text as _t
    db_session.execute(
        _t("UPDATE users SET is_active = FALSE WHERE id = :id"),
        {"id": manager_user["id"]},
    )
    db_session.commit()
    r = portal_client.get("/login")
    token = extract_csrf(r.text)
    r = portal_client.post(
        "/login",
        data={"login": "manager1", "password": "manager-pass", "csrf_token": token},
    )
    assert r.status_code == 401
