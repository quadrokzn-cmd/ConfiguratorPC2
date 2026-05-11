# UI-3 (Путь B, 2026-05-11): тесты внутренних 301-редиректов портала
# со старых URL /admin/{users,backups,audit} на новые URL раздела
# «Настройки» — /settings/{users,backups,audit-log}.
#
# Старые роутеры жили в portal под префиксом /admin (admin_users,
# admin_backups, admin_audit). На UI-3 они переехали в
# portal/routers/settings/*. Внутри portal/main.py стоят три пары
# тонких catch-all'ов (root + {rest:path}) для редиректа со старых URL.
#
# Проверяем:
#   - корневой URL отдаёт 301;
#   - sub-routes сохраняют путь (/admin/users/123/edit → /settings/users/123/edit);
#   - export сохраняется (/admin/audit/export → /settings/audit-log/export);
#   - редиректы публичные (без логина) — это catch-all GET без require_admin;
#   - соседние /admin/* (price-uploads, auto-price-loads, diagnostics)
#     НЕ задеваются этим catch-all'ом.

from __future__ import annotations


def _assert_301(client, src_path: str, dst_path: str) -> None:
    r = client.get(src_path)
    assert r.status_code == 301, (
        f"{src_path}: ожидался 301, получен {r.status_code}"
    )
    assert r.headers["location"] == dst_path, (
        f"{src_path}: location={r.headers['location']!r}, "
        f"ожидался {dst_path!r}"
    )


# ----- /admin/users → /settings/users -----------------------------------

def test_admin_users_root_redirects_301(portal_client):
    _assert_301(portal_client, "/admin/users", "/settings/users")


def test_admin_users_subroute_redirects_301(portal_client):
    _assert_301(
        portal_client,
        "/admin/users/15/edit",
        "/settings/users/15/edit",
    )


def test_admin_users_permissions_subroute_redirects_301(portal_client):
    _assert_301(
        portal_client,
        "/admin/users/42/permissions",
        "/settings/users/42/permissions",
    )


# ----- /admin/backups → /settings/backups -------------------------------

def test_admin_backups_root_redirects_301(portal_client):
    _assert_301(portal_client, "/admin/backups", "/settings/backups")


def test_admin_backups_create_subroute_redirects_301(portal_client):
    _assert_301(
        portal_client, "/admin/backups/create", "/settings/backups/create",
    )


def test_admin_backups_download_deep_subroute_redirects_301(portal_client):
    _assert_301(
        portal_client,
        "/admin/backups/download/daily/kvadro_tech_2026-04-28T03-00-00.dump",
        "/settings/backups/download/daily/kvadro_tech_2026-04-28T03-00-00.dump",
    )


# ----- /admin/audit → /settings/audit-log -------------------------------

def test_admin_audit_root_redirects_301(portal_client):
    _assert_301(portal_client, "/admin/audit", "/settings/audit-log")


def test_admin_audit_export_subroute_redirects_301(portal_client):
    _assert_301(
        portal_client, "/admin/audit/export", "/settings/audit-log/export",
    )


# ----- Соседние /admin/* НЕ должны редиректиться ------------------------

def test_admin_price_uploads_is_not_redirected(admin_portal_client):
    """«Прайс-листы» остаются на /admin/price-uploads — переедут на
    UI-5, отдельным этапом. Сейчас тут должен быть 200, не 301."""
    r = admin_portal_client.get("/admin/price-uploads")
    assert r.status_code == 200, (
        f"/admin/price-uploads не должен отдавать редирект "
        f"(сейчас status={r.status_code})"
    )


def test_admin_auto_price_loads_is_not_redirected(admin_portal_client):
    """«Автозагрузка» — те же правила, что у price-uploads."""
    # Тест автозагрузки в кодовой базе ждёт seed-данные. Здесь нам
    # важна не сама страница, а отсутствие 301-редиректа: страница
    # либо 200, либо 500 при пустой БД, но НЕ 301 на /settings/*.
    r = admin_portal_client.get("/admin/auto-price-loads")
    assert r.status_code != 301, (
        f"/admin/auto-price-loads не должен 301-редиректиться: "
        f"{r.status_code} location={r.headers.get('location', '')}"
    )


def test_admin_diagnostics_is_not_redirected(admin_portal_client):
    """Диагностика тоже остаётся на /admin/diagnostics — раздел
    «Настройки» в UI её пока не охватывает."""
    r = admin_portal_client.get("/admin/diagnostics")
    assert r.status_code != 301, (
        f"/admin/diagnostics не должен 301-редиректиться: {r.status_code}"
    )
