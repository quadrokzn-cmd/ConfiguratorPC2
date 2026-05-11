# UI-5 (Путь B, 2026-05-11): тесты admin-страниц конфигуратора
# (/admin dashboard, /admin/budget, /admin/queries), переехавших из
# app/routers/admin_router.py в portal/routers/admin.py.
#
# URL'ы сохранены — собственник подтвердил, что менеджеров с закладками
# нет, редиректы не нужны. Тесты заменяют бывшие
# tests/test_web/test_access.py и tests/test_web/test_admin_budget.py,
# которые работали через TestClient(app/main.py).

from __future__ import annotations

from sqlalchemy import text as _t


def _insert_api_usage(session, *, cost_rub: float) -> None:
    session.execute(
        _t(
            "INSERT INTO api_usage_log "
            "    (provider, model, tokens_in, tokens_out, cost_usd, cost_rub, status) "
            "VALUES ('openai', 'gpt-4o-mini', 100, 50, 0.001, :cr, 'ok')"
        ),
        {"cr": cost_rub},
    )
    session.commit()


# ---- Доступ: только admin -------------------------------------------------


def test_admin_pages_forbidden_for_manager(manager_portal_client):
    """Менеджер без admin-роли не должен открывать admin-страницы."""
    assert manager_portal_client.get("/admin").status_code == 403
    assert manager_portal_client.get("/admin/budget").status_code == 403
    assert manager_portal_client.get("/admin/queries").status_code == 403


def test_admin_pages_available_for_admin(admin_portal_client):
    assert admin_portal_client.get("/admin").status_code == 200
    assert admin_portal_client.get("/admin/budget").status_code == 200
    assert admin_portal_client.get("/admin/queries").status_code == 200


# ---- Dashboard: рендер бюджета ------------------------------------------


def test_admin_dashboard_shows_warning_when_above_80(
    admin_portal_client, db_session
):
    _insert_api_usage(db_session, cost_rub=85.0)
    r = admin_portal_client.get("/admin")
    assert r.status_code == 200
    assert "85%" in r.text


def test_admin_dashboard_shows_blocked_state(admin_portal_client, db_session):
    _insert_api_usage(db_session, cost_rub=150.0)
    r = admin_portal_client.get("/admin")
    assert r.status_code == 200
    assert "Лимит исчерпан" in r.text


# ---- Budget detail ------------------------------------------------------


def test_budget_page_lists_days(admin_portal_client, db_session):
    _insert_api_usage(db_session, cost_rub=12.5)
    r = admin_portal_client.get("/admin/budget")
    assert r.status_code == 200
    assert "12.50" in r.text
