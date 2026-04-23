# Тесты контроля доступа:
#   - менеджер не может открыть чужой запрос;
#   - админ может;
#   - /admin недоступен менеджеру, доступен админу.

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_web.conftest import _login, extract_csrf


def _create_query(client, *, text="тест"):
    r = client.get("/")
    token = extract_csrf(r.text)
    r = client.post(
        "/query",
        data={"project_name": "", "raw_text": text, "csrf_token": token},
    )
    return int(r.headers["location"].rsplit("/", 1)[1])


def test_owner_can_view_own_query(manager_client, mock_process_query):
    qid = _create_query(manager_client)
    r = manager_client.get(f"/query/{qid}")
    assert r.status_code == 200


def test_other_manager_cannot_view_foreign_query(
    app_client, manager_user, manager2_user, mock_process_query
):
    # Первый менеджер создаёт запрос
    from app.main import app
    with TestClient(app, follow_redirects=False) as c1:
        _login(c1, manager_user["login"], manager_user["password"])
        qid = _create_query(c1)

    # Второй менеджер пытается посмотреть
    _login(app_client, manager2_user["login"], manager2_user["password"])
    r = app_client.get(f"/query/{qid}")
    assert r.status_code == 403


def test_admin_can_view_foreign_query(
    app_client, manager_user, admin_user, mock_process_query
):
    from app.main import app
    with TestClient(app, follow_redirects=False) as c1:
        _login(c1, manager_user["login"], manager_user["password"])
        qid = _create_query(c1)

    _login(app_client, admin_user["login"], admin_user["password"])
    r = app_client.get(f"/query/{qid}")
    assert r.status_code == 200


def test_admin_pages_forbidden_for_manager(manager_client):
    assert manager_client.get("/admin").status_code == 403
    assert manager_client.get("/admin/users").status_code == 403
    assert manager_client.get("/admin/budget").status_code == 403
    assert manager_client.get("/admin/queries").status_code == 403


def test_admin_pages_available_for_admin(admin_client):
    assert admin_client.get("/admin").status_code == 200
    assert admin_client.get("/admin/users").status_code == 200
    assert admin_client.get("/admin/budget").status_code == 200
    assert admin_client.get("/admin/queries").status_code == 200


def test_nonexistent_query_returns_404(manager_client):
    r = manager_client.get("/query/999999")
    assert r.status_code == 404


def test_history_shows_only_own_queries(
    app_client, manager_user, manager2_user, mock_process_query, db_session
):
    from app.main import app
    with TestClient(app, follow_redirects=False) as c1:
        _login(c1, manager_user["login"], manager_user["password"])
        _create_query(c1, text="запрос от первого менеджера")

    with TestClient(app, follow_redirects=False) as c2:
        _login(c2, manager2_user["login"], manager2_user["password"])
        _create_query(c2, text="запрос от второго менеджера")
        r = c2.get("/history")
        assert r.status_code == 200
        assert "запрос от второго менеджера" in r.text
        assert "запрос от первого менеджера" not in r.text
