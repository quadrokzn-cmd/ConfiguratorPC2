# Тесты контроля доступа к /configurator/* в портале.
#
# UI-4 (Путь B, 2026-05-11): query/history/project переехали из app/
# на portal/configurator. Конфигуратор-часть бывшего tests/test_web/test_access.py
# живёт здесь.
#
# Проверки:
#   - менеджер не может открыть чужой запрос (403);
#   - админ может;
#   - /configurator/query/{id} → 404 для несуществующих;
#   - /configurator/history показывает только свои.

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_portal.conftest import (
    _login_via_portal,
    extract_csrf,
    qid_from_submit_redirect,
)


def _create_query(client, *, text="тест"):
    r = client.get("/configurator/")
    token = extract_csrf(r.text)
    r = client.post(
        "/configurator/query",
        data={"project_name": "", "raw_text": text, "csrf_token": token},
    )
    return qid_from_submit_redirect(r.headers["location"])


def test_owner_can_view_own_query(manager_portal_client, mock_process_query):
    qid = _create_query(manager_portal_client)
    r = manager_portal_client.get(f"/configurator/query/{qid}")
    assert r.status_code == 200


def test_other_manager_cannot_view_foreign_query(
    portal_client, manager_user, manager2_user, mock_process_query
):
    from portal.main import app as portal_app
    with TestClient(portal_app, follow_redirects=False) as c1:
        _login_via_portal(c1, manager_user["login"], manager_user["password"])
        qid = _create_query(c1)

    _login_via_portal(portal_client, manager2_user["login"], manager2_user["password"])
    r = portal_client.get(f"/configurator/query/{qid}")
    assert r.status_code == 403


def test_admin_can_view_foreign_query(
    portal_client, manager_user, admin_user, mock_process_query
):
    from portal.main import app as portal_app
    with TestClient(portal_app, follow_redirects=False) as c1:
        _login_via_portal(c1, manager_user["login"], manager_user["password"])
        qid = _create_query(c1)

    _login_via_portal(portal_client, admin_user["login"], admin_user["password"])
    r = portal_client.get(f"/configurator/query/{qid}")
    assert r.status_code == 200


def test_nonexistent_query_returns_404(manager_portal_client):
    r = manager_portal_client.get("/configurator/query/999999")
    assert r.status_code == 404


def test_history_shows_only_own_queries(
    portal_client, manager_user, manager2_user, mock_process_query
):
    from portal.main import app as portal_app
    with TestClient(portal_app, follow_redirects=False) as c1:
        _login_via_portal(c1, manager_user["login"], manager_user["password"])
        _create_query(c1, text="запрос от первого менеджера")

    with TestClient(portal_app, follow_redirects=False) as c2:
        _login_via_portal(c2, manager2_user["login"], manager2_user["password"])
        _create_query(c2, text="запрос от второго менеджера")
        r = c2.get("/configurator/history")
        assert r.status_code == 200
        assert "запрос от второго менеджера" in r.text
        assert "запрос от первого менеджера" not in r.text
