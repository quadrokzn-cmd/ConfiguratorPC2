# Минимальные тесты CSRF-защиты POST-форм.

from __future__ import annotations


def test_query_without_csrf_rejected(manager_client):
    r = manager_client.post(
        "/query",
        data={"project_name": "", "raw_text": "любой", "csrf_token": "wrong"},
    )
    assert r.status_code == 400


def test_admin_user_create_without_csrf_rejected(admin_client):
    r = admin_client.post(
        "/admin/users",
        data={
            "login": "x", "name": "y", "password": "123456",
            "csrf_token": "wrong",
        },
    )
    assert r.status_code == 400


def test_logout_without_csrf_rejected(manager_client):
    r = manager_client.post("/logout", data={"csrf_token": "wrong"})
    assert r.status_code == 400
