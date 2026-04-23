# Тесты авторизации: вход, выход, редирект неавторизованных.

from __future__ import annotations

from tests.test_web.conftest import extract_csrf


def test_unauthenticated_root_redirects_to_login(app_client):
    r = app_client.get("/")
    assert r.status_code == 302
    assert r.headers["location"] == "/login"


def test_login_wrong_password(app_client, manager_user):
    r = app_client.get("/login")
    token = extract_csrf(r.text)
    r = app_client.post(
        "/login",
        data={"login": "manager1", "password": "WRONG", "csrf_token": token},
    )
    assert r.status_code == 401
    assert "Неверный логин или пароль" in r.text


def test_login_success(app_client, manager_user):
    r = app_client.get("/login")
    token = extract_csrf(r.text)
    r = app_client.post(
        "/login",
        data={
            "login":      "manager1",
            "password":   "manager-pass",
            "csrf_token": token,
        },
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    # После логина / доступна
    r = app_client.get("/")
    assert r.status_code == 200
    assert "Новый запрос" in r.text


def test_logout_clears_session(manager_client):
    r = manager_client.get("/")
    assert r.status_code == 200
    token = extract_csrf(r.text)
    r = manager_client.post("/logout", data={"csrf_token": token})
    assert r.status_code == 302
    assert r.headers["location"] == "/login"
    r = manager_client.get("/")
    assert r.status_code == 302


def test_login_without_csrf_token_fails(app_client, manager_user):
    r = app_client.post(
        "/login",
        data={"login": "manager1", "password": "manager-pass", "csrf_token": "bad"},
    )
    assert r.status_code == 400


def test_deactivated_user_cannot_login(app_client, db_session, manager_user):
    from sqlalchemy import text as _t
    db_session.execute(
        _t("UPDATE users SET is_active = FALSE WHERE id = :id"),
        {"id": manager_user["id"]},
    )
    db_session.commit()
    r = app_client.get("/login")
    token = extract_csrf(r.text)
    r = app_client.post(
        "/login",
        data={"login": "manager1", "password": "manager-pass", "csrf_token": token},
    )
    assert r.status_code == 401
