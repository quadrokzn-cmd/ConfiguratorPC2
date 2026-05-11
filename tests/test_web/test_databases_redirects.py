# UI-2 (Путь B, 2026-05-11): тесты 301-редиректов со старых URL
# /admin/{suppliers,components,mapping} конфигуратора на новые URL
# /databases/{suppliers,components,mapping} портала.
#
# Проверяем:
#   - корневой URL отдаёт 301;
#   - sub-routes сохраняют путь (/admin/suppliers/15/edit
#     → /databases/suppliers/15/edit);
#   - редиректы публичные (работают без логина) — не зависят от прав;
#   - /admin/auto-price (и другие /admin/* в конфигураторе) НЕ задеваются
#     этим catch-all'ом — остаются страницами конфигуратора.

from __future__ import annotations

from app.config import settings


_PORTAL = settings.portal_url


def _assert_301(client, src_path: str, dst_path: str) -> None:
    """Помощник: проверяет, что GET src_path → 301 на portal_url + dst_path."""
    r = client.get(src_path)
    assert r.status_code == 301, (
        f"{src_path}: ожидался 301, получен {r.status_code}"
    )
    assert r.headers["location"] == f"{_PORTAL}{dst_path}", (
        f"{src_path}: location={r.headers['location']!r}, "
        f"ожидался {_PORTAL}{dst_path!r}"
    )


# ----- /admin/suppliers ---------------------------------------------------


def test_suppliers_root_redirects_301(app_client):
    _assert_301(app_client, "/admin/suppliers", "/databases/suppliers")


def test_suppliers_new_form_redirects_301(app_client):
    _assert_301(
        app_client, "/admin/suppliers/new", "/databases/suppliers/new",
    )


def test_suppliers_edit_subroute_redirects_301(app_client):
    _assert_301(
        app_client,
        "/admin/suppliers/15/edit",
        "/databases/suppliers/15/edit",
    )


# ----- /admin/components --------------------------------------------------


def test_components_root_redirects_301(app_client):
    _assert_301(app_client, "/admin/components", "/databases/components")


def test_components_category_subroute_redirects_301(app_client):
    _assert_301(
        app_client, "/admin/components/cpu", "/databases/components/cpu",
    )


def test_components_detail_subroute_redirects_301(app_client):
    _assert_301(
        app_client,
        "/admin/components/cpu/15",
        "/databases/components/cpu/15",
    )


# ----- /admin/mapping -----------------------------------------------------


def test_mapping_root_redirects_301(app_client):
    _assert_301(app_client, "/admin/mapping", "/databases/mapping")


def test_mapping_detail_subroute_redirects_301(app_client):
    _assert_301(
        app_client, "/admin/mapping/42", "/databases/mapping/42",
    )


# ----- Соседние /admin/* НЕ должны редиректиться --------------------------


def test_admin_dashboard_is_not_redirected(admin_client_app):
    """/admin (дашборд конфигуратора) остаётся в конфигураторе. После
    логина админ открывает его и получает 200."""
    r = admin_client_app.get("/admin")
    assert r.status_code == 200, r.status_code
    # Минимальный smoke на содержимое.
    assert "<html" in r.text.lower()


def test_admin_auto_price_loads_is_not_in_configurator():
    """/admin/auto-price-loads живёт в портале и в конфигураторе его нет.

    Регресс UI-2: проверяем, что наши catch-all-редиректы случайно
    не зацепили /admin/auto-price-loads (FastAPI не найдёт роут и вернёт
    404 — это ОК, главное не 301 на portal_url, иначе бы catch-all был
    слишком широкий)."""
    from fastapi.testclient import TestClient
    from app.main import app as app_main
    with TestClient(app_main, follow_redirects=False) as c:
        r = c.get("/admin/auto-price-loads")
        # Это публичный путь в конфигураторе, обработчика для него нет —
        # либо 404, либо exception_handler даст 302 на login. Главное:
        # это НЕ 301-редирект на portal/databases/auto-price-loads.
        assert r.status_code != 301, (
            f"/admin/auto-price-loads не должен 301-редиректиться: {r.status_code}"
        )
        if r.status_code == 301:  # pragma: no cover
            assert "/databases/" not in r.headers.get("location", "")
