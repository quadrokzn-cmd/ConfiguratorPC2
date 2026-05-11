# Тесты контроля дневного бюджета OpenAI в потоке submit-запроса.
#
# UI-4 (Путь B, 2026-05-11): тесты admin/dashboard-частей бюджета
# переехали в tests/test_web/test_admin_budget.py — они проверяют
# страницы admin_router в app/, которые после UI-4 пока остаются на
# config.quadro.tatar (уйдут в UI-5).

from __future__ import annotations

from sqlalchemy import text as _t

from tests.test_portal.conftest import extract_csrf, qid_from_submit_redirect


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

    r = manager_client.get("/configurator/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/configurator/query",
        data={"project_name": "", "raw_text": "любой запрос", "csrf_token": token},
    )
    assert r.status_code == 302
    qid = qid_from_submit_redirect(r.headers["location"])

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
    r = manager_client.get("/configurator/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/configurator/query",
        data={"project_name": "", "raw_text": "любой", "csrf_token": token},
    )
    assert r.status_code == 302
    mock_process_query.assert_called_once()
