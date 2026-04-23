# Тесты потока «отправил запрос → сохранили → редирект на /query/{id}».
#
# process_query мокается фикстурой mock_process_query.

from __future__ import annotations

from sqlalchemy import text as _t

from tests.test_web.conftest import extract_csrf


def test_submit_query_creates_project_and_redirects(
    manager_client, mock_process_query, db_session, manager_user
):
    r = manager_client.get("/")
    token = extract_csrf(r.text)

    r = manager_client.post(
        "/query",
        data={
            "project_name": "Проект бухгалтерии",
            "raw_text":     "Офисный ПК для бухгалтера до 50000",
            "csrf_token":   token,
        },
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/query/")
    qid = int(loc.rsplit("/", 1)[1])

    # В БД появились project + query
    row = db_session.execute(
        _t(
            "SELECT q.id, q.raw_text, q.status, p.name AS pname, q.user_id "
            "FROM queries q JOIN projects p ON p.id=q.project_id WHERE q.id=:id"
        ),
        {"id": qid},
    ).first()
    assert row is not None
    assert row.raw_text == "Офисный ПК для бухгалтера до 50000"
    assert row.status == "ok"
    # Название должно содержать «Проект бухгалтерии» и дату в скобках
    assert row.pname.startswith("Проект бухгалтерии (")
    assert row.user_id == manager_user["id"]

    # process_query действительно вызван
    mock_process_query.assert_called_once()


def test_empty_project_name_autogenerates(
    manager_client, mock_process_query, db_session
):
    r = manager_client.get("/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/query",
        data={"project_name": "", "raw_text": "любой запрос", "csrf_token": token},
    )
    assert r.status_code == 302
    qid = int(r.headers["location"].rsplit("/", 1)[1])
    row = db_session.execute(
        _t("SELECT p.name FROM queries q JOIN projects p ON p.id=q.project_id "
           "WHERE q.id=:id"),
        {"id": qid},
    ).first()
    assert row.name.startswith("Запрос от ")


def test_empty_raw_text_redirects_back_with_error(manager_client):
    r = manager_client.get("/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/query",
        data={"project_name": "", "raw_text": "   ", "csrf_token": token},
    )
    # Редирект на /, с flash_error в сессии.
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    # Следом /-страница показывает ошибку
    r = manager_client.get("/")
    assert "Введите текст запроса" in r.text


def test_view_query_result_page(manager_client, mock_process_query):
    r = manager_client.get("/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/query",
        data={"project_name": "Test", "raw_text": "любой запрос", "csrf_token": token},
    )
    qid = int(r.headers["location"].rsplit("/", 1)[1])

    r = manager_client.get(f"/query/{qid}")
    assert r.status_code == 200
    assert "Intel Core i5-12400F" in r.text
    assert "Kingston 16GB DDR4" in r.text
    assert "Поставщик А" in r.text
