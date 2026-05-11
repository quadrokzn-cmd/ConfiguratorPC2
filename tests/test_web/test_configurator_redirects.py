# UI-4 (Путь B, 2026-05-11): тесты catch-all 301-редиректов из app/main.py
# на portal/configurator/*. После переноса конфигуратора в portal на
# config.quadro.tatar остаются только admin-страницы (/admin dashboard,
# /admin/budget, /admin/queries) и 301-редиректы старых конфигуратор-URL'ов
# на новый портал-префикс.

from __future__ import annotations

from app.config import settings


_PORTAL = settings.portal_url


def _assert_301(client, src_path: str, dst_path: str) -> None:
    r = client.get(src_path)
    assert r.status_code == 301, (
        f"{src_path}: ожидался 301, получен {r.status_code}"
    )
    assert r.headers["location"] == f"{_PORTAL}{dst_path}", (
        f"{src_path}: location={r.headers['location']!r}, "
        f"ожидался {_PORTAL}{dst_path!r}"
    )


# ----- Корень и основные пути конфигуратора → portal/configurator/* ------


def test_root_redirects_to_portal_configurator(app_client_legacy):
    _assert_301(app_client_legacy, "/", "/configurator/")


def test_projects_redirects_to_portal_configurator(app_client_legacy):
    _assert_301(app_client_legacy, "/projects", "/configurator/projects")


def test_history_redirects_to_portal_configurator(app_client_legacy):
    _assert_301(app_client_legacy, "/history", "/configurator/history")


def test_query_subroute_redirects_to_portal_configurator(app_client_legacy):
    _assert_301(
        app_client_legacy, "/query/42", "/configurator/query/42",
    )


def test_project_detail_redirects_to_portal_configurator(app_client_legacy):
    _assert_301(
        app_client_legacy, "/project/123", "/configurator/project/123",
    )


def test_project_subroute_preserves_path(app_client_legacy):
    _assert_301(
        app_client_legacy,
        "/project/123/export/excel",
        "/configurator/project/123/export/excel",
    )


# ----- /admin/* НЕ должен редиректиться на portal/configurator/admin -----


def test_admin_root_not_redirected_to_configurator(app_client_legacy):
    """GET /admin → admin_router в app/ (Login redirect или 200, но НЕ 301
    на portal/configurator/admin)."""
    r = app_client_legacy.get("/admin")
    # admin_router требует логин — без него: 302 на portal/login.
    # Главное: НЕ 301 на portal/configurator/admin.
    assert r.status_code != 301
    loc = r.headers.get("location", "")
    assert "/configurator/admin" not in loc


def test_admin_unknown_subpath_returns_404_not_redirect(app_client_legacy):
    """Любой /admin/* без явного handler'а в admin_router и без точечного
    301 (suppliers/components/mapping) — 404, не редирект."""
    r = app_client_legacy.get("/admin/some-future-section")
    assert r.status_code == 404


# ----- /healthz и /static/* — не задеты catch-all'ом ---------------------


def test_healthz_is_not_redirected(app_client_legacy):
    r = app_client_legacy.get("/healthz")
    # /healthz возвращает 200/503 — не редирект.
    assert r.status_code in (200, 503), r.status_code


def test_static_path_is_not_redirected(app_client_legacy):
    """app.mount('/static', ...) монтируется до catch-all'а, поэтому
    статика отдаётся 200/304/404 (от ZIP-handler), но не 301."""
    r = app_client_legacy.get("/static/dist/main.css")
    assert r.status_code != 301
