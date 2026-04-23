# Тесты контроля дневного бюджета OpenAI.

from __future__ import annotations

from sqlalchemy import text as _t

from tests.test_web.conftest import extract_csrf


def _insert_api_usage(session, *, cost_rub: float) -> None:
    """Вставляет одну фиктивную запись в api_usage_log за сегодня."""
    session.execute(
        _t(
            "INSERT INTO api_usage_log "
            "    (provider, model, tokens_in, tokens_out, cost_usd, cost_rub, status) "
            "VALUES ('openai', 'gpt-4o-mini', 100, 50, 0.001, :cr, 'ok')"
        ),
        {"cr": cost_rub},
    )
    session.commit()


def test_blocked_budget_prevents_openai_call(
    manager_client, mock_process_query, db_session
):
    """Если расход >= лимит — process_query НЕ вызывается, запрос
    сохраняется со status='error' и понятным error_msg."""
    # Лимит в settings — 100 ₽. Забиваем на 105 ₽.
    _insert_api_usage(db_session, cost_rub=105.0)

    r = manager_client.get("/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/query",
        data={"project_name": "", "raw_text": "любой запрос", "csrf_token": token},
    )
    assert r.status_code == 302
    qid = int(r.headers["location"].rsplit("/", 1)[1])

    # process_query НЕ вызывался
    mock_process_query.assert_not_called()

    row = db_session.execute(
        _t("SELECT status, error_msg FROM queries WHERE id = :id"),
        {"id": qid},
    ).first()
    assert row.status == "error"
    assert row.error_msg is not None
    assert "Дневной бюджет" in row.error_msg


def test_warning_budget_still_allows_requests(
    manager_client, mock_process_query, db_session
):
    """На 80-99% — предупреждение, но запросы проходят."""
    _insert_api_usage(db_session, cost_rub=85.0)   # 85%
    r = manager_client.get("/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/query",
        data={"project_name": "", "raw_text": "любой", "csrf_token": token},
    )
    assert r.status_code == 302
    mock_process_query.assert_called_once()


def test_admin_dashboard_shows_warning_when_above_80(
    admin_client, db_session
):
    _insert_api_usage(db_session, cost_rub=85.0)
    r = admin_client.get("/admin")
    assert r.status_code == 200
    # На 85% у нас state='warning' — проверяем по проценту в тексте
    assert "85%" in r.text


def test_admin_dashboard_shows_blocked_state(admin_client, db_session):
    _insert_api_usage(db_session, cost_rub=150.0)
    r = admin_client.get("/admin")
    assert r.status_code == 200
    assert "Лимит исчерпан" in r.text


def test_budget_page_lists_days(admin_client, db_session):
    _insert_api_usage(db_session, cost_rub=12.5)
    r = admin_client.get("/admin/budget")
    assert r.status_code == 200
    assert "12.50" in r.text
