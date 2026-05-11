# UI-4 (Путь B, 2026-05-11): проверка scoped-доступа к /configurator/* через
# require_configurator_access Depends (заменяет бывшую middleware
# _enforce_configurator_permission из app/main.py).
#
# Логика:
#   - не залогинен                              → 302 на /login (LoginRequiredRedirect);
#   - залогинен, без permissions['configurator'] → 302 на /?denied=configurator;
#   - залогинен, без права + Accept: application/json → 403 JSON;
#   - залогинен, есть право (или admin)         → 200.

from __future__ import annotations

from tests.test_portal.conftest import _login_via_portal


def test_manager_without_perm_redirects_to_root_with_denied(
    portal_client, manager_user_no_perms
):
    """Менеджер без права configurator → 302 на /?denied=configurator (portal-internal)."""
    _login_via_portal(
        portal_client, manager_user_no_perms["login"], manager_user_no_perms["password"],
    )
    r = portal_client.get("/configurator/")
    assert r.status_code == 302, r.text[:200]
    assert r.headers["location"] == "/?denied=configurator", r.headers["location"]


def test_manager_with_perm_can_open_configurator(manager_portal_client):
    """Менеджер с дефолтными permissions ({"configurator": True}) — 200."""
    r = manager_portal_client.get("/configurator/")
    assert r.status_code == 200


def test_admin_without_perms_can_open_configurator(portal_client, admin_user):
    """Admin всегда проходит, даже если permissions пустые
    (has_permission(admin, ...) → True)."""
    _login_via_portal(portal_client, admin_user["login"], admin_user["password"])
    r = portal_client.get("/configurator/")
    assert r.status_code == 200


def test_manager_without_perm_blocked_on_protected_subpaths(
    portal_client, manager_user_no_perms
):
    """require_configurator_access применяется ко всем /configurator/*."""
    _login_via_portal(
        portal_client, manager_user_no_perms["login"], manager_user_no_perms["password"],
    )
    for path in ("/configurator/projects", "/configurator/history"):
        r = portal_client.get(path)
        assert r.status_code == 302, f"{path}: {r.status_code}"
        assert r.headers["location"] == "/?denied=configurator", path


def test_unauthenticated_request_falls_through_to_login_redirect(portal_client):
    """Без сессии require_configurator_access поднимает LoginRequiredRedirect
    через require_login → 302 на /login. Никакого 403 быть не должно."""
    r = portal_client.get("/configurator/")
    assert r.status_code == 302
    assert "/login?next=" in r.headers["location"]


def test_json_request_gets_403_not_redirect(portal_client, manager_user_no_perms):
    """API-клиенты, ждущие JSON (Accept: application/json без text/html),
    получают 403 JSON — редирект для них бессмысленен."""
    _login_via_portal(
        portal_client, manager_user_no_perms["login"], manager_user_no_perms["password"],
    )
    r = portal_client.get(
        "/configurator/",
        headers={"Accept": "application/json"},
    )
    assert r.status_code == 403
    body = r.json()
    assert "Конфигуратор" in body.get("detail", "")
