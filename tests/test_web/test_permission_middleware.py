# Тесты middleware-проверки permission "configurator" в конфигураторе
# (этап 9Б.4).
#
# Бриф 9Б.4: менеджер без permissions["configurator"]=true не должен
# открывать страницы конфигуратора, даже если зашёл прямо по URL
# config.quadro.tatar/. Middleware ловит залогиненную сессию без права
# и редиректит на ${PORTAL_URL}/?denied=configurator (или 403 для JSON).

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_web.conftest import _login


def test_manager_without_perm_redirects_to_portal_with_denied(
    app_client, manager_no_perms
):
    """Менеджер без права configurator → 302 на ${PORTAL_URL}/?denied=configurator."""
    _login(app_client, manager_no_perms["login"], manager_no_perms["password"])
    r = app_client.get("/")
    assert r.status_code == 302, r.text[:200]
    location = r.headers["location"]
    assert location.endswith("/?denied=configurator"), location
    # PORTAL_URL = http://localhost:8081 (см. tests/test_web/conftest.py)
    assert "://" in location, "ожидался абсолютный URL на портал"


def test_manager_with_perm_can_open_configurator(manager_client):
    """Менеджер с дефолтными permissions ({"configurator": True}) — 200."""
    r = manager_client.get("/")
    assert r.status_code == 200


def test_admin_without_perms_can_open_configurator(app_client, admin_user):
    """Admin всегда проходит, даже если permissions пустые
    (has_permission(admin, ...) → True). Берём admin_user из
    test_web/conftest.py — у него по умолчанию permissions={}."""
    _login(app_client, admin_user["login"], admin_user["password"])
    r = app_client.get("/")
    assert r.status_code == 200


def test_manager_without_perm_blocked_on_protected_subpaths(
    app_client, manager_no_perms
):
    """Middleware применяется ко всем не-служебным путям, не только к /.
    Любая страница конфигуратора отдаёт redirect на портал."""
    _login(app_client, manager_no_perms["login"], manager_no_perms["password"])
    for path in ("/projects", "/history", "/admin", "/admin/components"):
        r = app_client.get(path)
        assert r.status_code == 302, f"{path}: {r.status_code}"
        assert r.headers["location"].endswith("/?denied=configurator"), path


def test_unauthenticated_request_falls_through_to_login_redirect(app_client):
    """Без сессии middleware пропускает запрос дальше — обычный
    LoginRequiredRedirect ловит его и редиректит на портал/login.
    Это поведение НЕ должно регрессить из-за нового middleware."""
    r = app_client.get("/")
    assert r.status_code == 302
    location = r.headers["location"]
    assert "/login?next=" in location, location


def test_healthz_bypasses_permission_middleware(app_client):
    """/healthz — служебный, должен отвечать 200 и не уходить в редирект,
    даже если бы был залогинен пользователь без права."""
    r = app_client.get("/healthz")
    assert r.status_code in (200, 503)  # 200 если БД ок, 503 если упала


def test_static_path_bypasses_permission_middleware(app_client, manager_no_perms):
    """/static/* — служебный путь. Редиректить на портал статику нельзя:
    CSS/JS должны грузиться даже у пользователей без прав (например,
    на самой странице 302-редиректа браузер всё равно дёрнет /static)."""
    _login(app_client, manager_no_perms["login"], manager_no_perms["password"])
    # Файл, который точно есть в static/dist/
    r = app_client.get("/static/dist/main.css")
    # 200 (файл найден) или 304 — лишь бы не 302 на портал.
    assert r.status_code in (200, 304), r.status_code


def test_json_request_gets_403_not_redirect(app_client, manager_no_perms):
    """API-клиенты, ждущие JSON (Accept: application/json), получают
    403 JSON — редирект для них бессмысленен."""
    _login(app_client, manager_no_perms["login"], manager_no_perms["password"])
    r = app_client.get("/", headers={"Accept": "application/json"})
    assert r.status_code == 403
    body = r.json()
    assert "Конфигуратор" in body.get("detail", "")
