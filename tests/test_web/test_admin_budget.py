# Тесты admin-dashboard-частей бюджета (/admin, /admin/budget) на app/.
#
# UI-4 (Путь B, 2026-05-11): после переноса конфигуратора в portal/configurator
# страницы /admin (dashboard), /admin/budget остаются в app/ через admin_router.
# Эти тесты проверяют их рендер.

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


def test_admin_dashboard_shows_warning_when_above_80(
    admin_client_app, db_session
):
    _insert_api_usage(db_session, cost_rub=85.0)
    r = admin_client_app.get("/admin")
    assert r.status_code == 200
    assert "85%" in r.text


def test_admin_dashboard_shows_blocked_state(admin_client_app, db_session):
    _insert_api_usage(db_session, cost_rub=150.0)
    r = admin_client_app.get("/admin")
    assert r.status_code == 200
    assert "Лимит исчерпан" in r.text


def test_budget_page_lists_days(admin_client_app, db_session):
    _insert_api_usage(db_session, cost_rub=12.5)
    r = admin_client_app.get("/admin/budget")
    assert r.status_code == 200
    assert "12.50" in r.text
