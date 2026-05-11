# Тесты доступа к admin-страницам app/main (config.quadro.tatar).
#
# UI-4 (Путь B, 2026-05-11): после переноса конфигуратора в portal/configurator
# в app/ остались только /admin (dashboard), /admin/budget, /admin/queries
# и legacy-редирект /admin/users. Это и тестируется здесь.
#
# Тесты query/history/project access переехали в
# tests/test_portal/test_configurator_access.py.

from __future__ import annotations


def test_admin_pages_forbidden_for_manager(manager_client_app):
    """Менеджер без admin-роли не должен открывать admin-страницы app/."""
    assert manager_client_app.get("/admin").status_code == 403
    assert manager_client_app.get("/admin/budget").status_code == 403
    assert manager_client_app.get("/admin/queries").status_code == 403


def test_admin_pages_available_for_admin(admin_client_app):
    assert admin_client_app.get("/admin").status_code == 200
    assert admin_client_app.get("/admin/budget").status_code == 200
    assert admin_client_app.get("/admin/queries").status_code == 200


def test_admin_users_redirects_to_portal(admin_client_app):
    """Этап 9Б.1: /admin/users переехал в портал. Конфигуратор отдаёт
    302 на ${PORTAL_URL}/settings/users (UI-3 обновил target с
    /admin/users на /settings/users, чтобы избежать двойного hop)."""
    r = admin_client_app.get("/admin/users", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].endswith("/settings/users")
    assert "://" in r.headers["location"]  # абсолютный URL на портал
