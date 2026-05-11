# Тесты потока «отправил запрос → сохранили → редирект на /query/{id}».
#
# process_query мокается фикстурой mock_process_query.

from __future__ import annotations

from sqlalchemy import text as _t

from tests.test_portal.conftest import extract_csrf


def _parse_redirect(loc: str) -> tuple[int, int]:
    """/configurator/project/{pid}?highlight={qid} → (pid, qid)."""
    assert loc.startswith("/configurator/project/"), f"Неожиданный редирект: {loc}"
    path, _, qs = loc.partition("?")
    pid = int(path.rsplit("/", 1)[1])
    params = dict(p.split("=") for p in qs.split("&")) if qs else {}
    qid = int(params["highlight"])
    return pid, qid


def test_submit_query_creates_project_and_redirects(
    manager_client, mock_process_query, db_session, manager_user
):
    r = manager_client.get("/configurator/")
    token = extract_csrf(r.text)

    r = manager_client.post(
        "/configurator/query",
        data={
            "project_name": "Проект бухгалтерии",
            "raw_text":     "Офисный ПК для бухгалтера до 50000",
            "csrf_token":   token,
        },
    )
    assert r.status_code == 302
    # Этап 6.2: теперь редирект ведёт в карточку проекта с якорем.
    pid, qid = _parse_redirect(r.headers["location"])

    # В БД появились project + query
    row = db_session.execute(
        _t(
            "SELECT q.id, q.raw_text, q.status, p.id AS pid, p.name AS pname, q.user_id "
            "FROM queries q JOIN projects p ON p.id=q.project_id WHERE q.id=:id"
        ),
        {"id": qid},
    ).first()
    assert row is not None
    assert row.raw_text == "Офисный ПК для бухгалтера до 50000"
    assert row.status == "ok"
    assert row.pid == pid
    # Название должно содержать «Проект бухгалтерии» и дату в скобках
    assert row.pname.startswith("Проект бухгалтерии (")
    assert row.user_id == manager_user["id"]

    # process_query действительно вызван
    mock_process_query.assert_called_once()


def test_empty_project_name_autogenerates(
    manager_client, mock_process_query, db_session
):
    r = manager_client.get("/configurator/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/configurator/query",
        data={"project_name": "", "raw_text": "любой запрос", "csrf_token": token},
    )
    assert r.status_code == 302
    _, qid = _parse_redirect(r.headers["location"])
    row = db_session.execute(
        _t("SELECT p.name FROM queries q JOIN projects p ON p.id=q.project_id "
           "WHERE q.id=:id"),
        {"id": qid},
    ).first()
    assert row.name.startswith("Запрос от ")


def test_empty_raw_text_redirects_back_with_error(manager_client):
    r = manager_client.get("/configurator/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/configurator/query",
        data={"project_name": "", "raw_text": "   ", "csrf_token": token},
    )
    # Редирект на /, с flash_error в сессии.
    assert r.status_code == 302
    assert r.headers["location"] == "/configurator/"
    # Следом /-страница показывает ошибку
    r = manager_client.get("/configurator/")
    assert "Введите текст запроса" in r.text


def test_submit_query_rate_limit_saves_friendly_error(
    manager_client, mock_process_query, db_session, monkeypatch
):
    """Этап 9Г.2: ловим openai.RateLimitError через isinstance, а не по имени класса.
    Проверяем, что когда process_query кидает RateLimitError, в БД пишется
    user-friendly сообщение про rate-limit, а не «Внутренняя ошибка»."""
    from openai import RateLimitError
    import httpx

    # Минимальный конструктор RateLimitError из openai 1.x.
    rl_exc = RateLimitError(
        message="rate limit",
        response=httpx.Response(429, request=httpx.Request("POST", "https://api.openai.com/x")),
        body=None,
    )
    mock_process_query.side_effect = rl_exc

    r = manager_client.get("/configurator/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/configurator/query",
        data={"project_name": "RL", "raw_text": "любой запрос", "csrf_token": token},
    )
    assert r.status_code == 302
    _, qid = _parse_redirect(r.headers["location"])

    row = db_session.execute(
        _t("SELECT status, error_msg FROM queries WHERE id=:id"),
        {"id": qid},
    ).first()
    assert row.status == "error"
    assert "rate-limit" in (row.error_msg or "")


def test_submit_query_other_error_saves_generic_error(
    manager_client, mock_process_query, db_session
):
    """Этап 9Г.2: для не-RateLimitError исключений сообщение должно быть
    «Внутренняя ошибка ...», без подмены ветки rate-limit."""
    mock_process_query.side_effect = RuntimeError("boom")

    r = manager_client.get("/configurator/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/configurator/query",
        data={"project_name": "RT", "raw_text": "любой запрос", "csrf_token": token},
    )
    assert r.status_code == 302
    _, qid = _parse_redirect(r.headers["location"])

    row = db_session.execute(
        _t("SELECT status, error_msg FROM queries WHERE id=:id"),
        {"id": qid},
    ).first()
    assert row.status == "error"
    assert "rate-limit" not in (row.error_msg or "")
    assert "RuntimeError" in (row.error_msg or "")


def test_view_query_result_page(manager_client, mock_process_query):
    r = manager_client.get("/configurator/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/configurator/query",
        data={"project_name": "Test", "raw_text": "любой запрос", "csrf_token": token},
    )
    _, qid = _parse_redirect(r.headers["location"])

    r = manager_client.get(f"/configurator/query/{qid}")
    assert r.status_code == 200
    assert "Intel Core i5-12400F" in r.text
    assert "Kingston 16GB DDR4" in r.text
    assert "Поставщик А" in r.text
