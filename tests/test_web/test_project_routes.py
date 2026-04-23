# Интеграционные тесты роутов проекта и спецификации (этап 6.2).
#
# Используют TestClient с залогиненным менеджером/админом и
# мок-фикстуру mock_process_query.

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text as _t

from tests.test_web.conftest import (
    _login,
    extract_csrf,
    parse_query_submit_redirect,
    qid_from_submit_redirect,
)


# --------------------------- helpers ------------------------------------

def _get_csrf(client: TestClient, url: str = "/projects") -> str:
    r = client.get(url)
    assert r.status_code == 200
    return extract_csrf(r.text)


def _create_project(client: TestClient) -> int:
    """POST /projects → возвращает id свежего проекта."""
    token = _get_csrf(client, "/projects")
    r = client.post("/projects", data={"csrf_token": token})
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/project/")
    return int(loc.rsplit("/", 1)[1])


def _submit_query_to(client: TestClient, project_id: int) -> int:
    """POST /project/{pid}/new_query → возвращает query_id."""
    r = client.get(f"/project/{project_id}/new_query")
    assert r.status_code == 200
    token = extract_csrf(r.text)
    r = client.post(
        f"/project/{project_id}/new_query",
        data={"raw_text": "тест", "csrf_token": token},
    )
    assert r.status_code == 302, r.text[:400]
    loc = r.headers["location"]
    # /project/{pid}?highlight={qid}
    pid2, qid = parse_query_submit_redirect(loc)
    assert pid2 == project_id
    return qid


def _ajax(client: TestClient, url: str, payload: dict, *, csrf: str):
    return client.post(url, json=payload, headers={"X-CSRF-Token": csrf})


@pytest.fixture()
def both_variants_response(monkeypatch):
    """Мок process_query, возвращающий BuildResult с двумя вариантами (Intel + AMD)."""
    from app.routers import main_router, project_router
    from app.services.configurator.schema import (
        BuildRequest, BuildResult, ComponentChoice, SupplierOffer, Variant,
    )
    from app.services.nlu.schema import FinalResponse, ParsedRequest

    def _variant(mfg: str, cpu_model: str, total_usd: float, total_rub: float) -> Variant:
        return Variant(
            manufacturer=mfg,
            components=[
                ComponentChoice(
                    category="cpu", component_id=1, model=cpu_model, sku=None,
                    manufacturer=mfg,
                    chosen=SupplierOffer(supplier="S", price_usd=100,
                                         price_rub=9000, stock=1),
                ),
            ],
            total_usd=total_usd, total_rub=total_rub,
        )

    result = BuildResult(
        status="ok",
        variants=[
            _variant("Intel", "Intel Core i5-12400F", 200, 18000),
            _variant("AMD",   "AMD Ryzen 5 7600",    220, 19800),
        ],
        refusal_reason=None,
        usd_rub_rate=90.0, fx_source="fallback",
    )
    resp = FinalResponse(
        kind="ok", interpretation="Тест",
        formatted_text="", build_request=BuildRequest(),
        build_result=result, parsed=ParsedRequest(is_empty=False, purpose="office"),
        resolved=[], warnings=[], cost_usd=0.0,
    )
    mock = MagicMock(return_value=resp)
    monkeypatch.setattr(main_router, "process_query", mock)
    monkeypatch.setattr(project_router, "process_query", mock)
    return mock


# ==========================================================================
# /projects — список и создание
# ==========================================================================

def test_projects_list_empty_for_new_user(manager_client):
    r = manager_client.get("/projects")
    assert r.status_code == 200
    assert "У вас ещё нет проектов" in r.text


def test_create_empty_project_redirects_to_detail(manager_client):
    pid = _create_project(manager_client)
    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200
    # На странице нет конфигураций — приглашение добавить.
    assert "Добавить первую" in r.text or "Добавить конфигурацию" in r.text


def test_projects_list_shows_only_mine_for_manager(
    app_client, manager_user, manager2_user
):
    from app.main import app
    with TestClient(app, follow_redirects=False) as c1:
        _login(c1, manager_user["login"], manager_user["password"])
        _create_project(c1)

    with TestClient(app, follow_redirects=False) as c2:
        _login(c2, manager2_user["login"], manager2_user["password"])
        # У второго — свой проект, чужой не виден.
        r = c2.get("/projects")
        assert r.status_code == 200
        # Название проекта автосгенерировано, у обоих вид «Запрос от ...»
        # поэтому проверим число строк в таблице (если она есть).
        assert "У вас ещё нет проектов" in r.text


def test_projects_list_shows_all_for_admin(
    app_client, manager_user, manager2_user, admin_user
):
    from app.main import app
    with TestClient(app, follow_redirects=False) as c1:
        _login(c1, manager_user["login"], manager_user["password"])
        _create_project(c1)
    with TestClient(app, follow_redirects=False) as c2:
        _login(c2, manager2_user["login"], manager2_user["password"])
        _create_project(c2)

    _login(app_client, admin_user["login"], admin_user["password"])
    r = app_client.get("/projects")
    assert r.status_code == 200
    # Админ видит имена авторов у обоих
    assert "manager1" in r.text
    assert "manager2" in r.text


# ==========================================================================
# Редирект с главной — через /project/{pid}?highlight={qid}
# ==========================================================================

def test_submit_from_index_redirects_to_project_with_highlight(
    manager_client, mock_process_query
):
    r = manager_client.get("/")
    token = extract_csrf(r.text)
    r = manager_client.post(
        "/query",
        data={"project_name": "Через главную", "raw_text": "тест",
              "csrf_token": token},
    )
    assert r.status_code == 302
    pid, qid = parse_query_submit_redirect(r.headers["location"])
    # Страница проекта открывается и содержит секцию query-{qid}.
    r = manager_client.get(f"/project/{pid}?highlight={qid}")
    assert r.status_code == 200
    assert f'id="query-{qid}"' in r.text


# ==========================================================================
# Новая конфигурация в существующий проект
# ==========================================================================

def test_add_query_to_project_redirects_with_highlight(
    manager_client, mock_process_query
):
    pid = _create_project(manager_client)
    qid = _submit_query_to(manager_client, pid)
    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200
    assert f'id="query-{qid}"' in r.text


# ==========================================================================
# Select / deselect / update_quantity — через AJAX
# ==========================================================================

def test_select_variant_creates_spec_item(
    manager_client, mock_process_query, db_session
):
    pid = _create_project(manager_client)
    qid = _submit_query_to(manager_client, pid)

    # Достаём CSRF со страницы проекта.
    r = manager_client.get(f"/project/{pid}")
    csrf = extract_csrf(r.text)

    r = _ajax(manager_client, f"/project/{pid}/select",
              {"query_id": qid, "variant_manufacturer": "Intel", "quantity": 1},
              csrf=csrf)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["quantity"] == 1
    assert item["variant_manufacturer"] == "Intel"
    assert item["position"] == 1
    assert item["display_name"]  # непустое автоназвание

    # В БД строка есть.
    row = db_session.execute(_t(
        "SELECT query_id, variant_manufacturer, quantity, position, auto_name "
        "FROM specification_items WHERE project_id = :pid"
    ), {"pid": pid}).first()
    assert row is not None
    assert int(row.query_id) == qid
    assert row.variant_manufacturer == "Intel"
    assert int(row.quantity) == 1
    assert row.auto_name  # непустое


def test_deselect_variant_removes_item(manager_client, mock_process_query):
    pid = _create_project(manager_client)
    qid = _submit_query_to(manager_client, pid)
    csrf = extract_csrf(manager_client.get(f"/project/{pid}").text)
    _ajax(manager_client, f"/project/{pid}/select",
          {"query_id": qid, "variant_manufacturer": "Intel", "quantity": 2},
          csrf=csrf)

    r = _ajax(manager_client, f"/project/{pid}/deselect",
              {"query_id": qid, "variant_manufacturer": "Intel"},
              csrf=csrf)
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["total_usd"] == 0


def test_update_quantity_recalcs_totals(manager_client, mock_process_query):
    pid = _create_project(manager_client)
    qid = _submit_query_to(manager_client, pid)
    csrf = extract_csrf(manager_client.get(f"/project/{pid}").text)

    _ajax(manager_client, f"/project/{pid}/select",
          {"query_id": qid, "variant_manufacturer": "Intel", "quantity": 1},
          csrf=csrf)
    r = _ajax(manager_client, f"/project/{pid}/update_quantity",
              {"query_id": qid, "variant_manufacturer": "Intel", "quantity": 4},
              csrf=csrf)
    assert r.status_code == 200
    data = r.json()
    it = data["items"][0]
    assert it["quantity"] == 4
    # total = unit × quantity
    assert it["total_usd"] == round(it["unit_usd"] * 4, 2)
    assert it["total_rub"] == round(it["unit_rub"] * 4, 2)
    assert data["total_usd"] == it["total_usd"]


def test_select_both_variants_from_one_query(
    manager_client, both_variants_response
):
    pid = _create_project(manager_client)
    qid = _submit_query_to(manager_client, pid)
    csrf = extract_csrf(manager_client.get(f"/project/{pid}").text)

    _ajax(manager_client, f"/project/{pid}/select",
          {"query_id": qid, "variant_manufacturer": "Intel", "quantity": 1},
          csrf=csrf)
    r = _ajax(manager_client, f"/project/{pid}/select",
              {"query_id": qid, "variant_manufacturer": "AMD", "quantity": 1},
              csrf=csrf)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 2
    mfgs = {it["variant_manufacturer"] for it in items}
    assert mfgs == {"Intel", "AMD"}


def test_position_increments_in_click_order(
    manager_client, both_variants_response
):
    pid = _create_project(manager_client)
    q1 = _submit_query_to(manager_client, pid)
    q2 = _submit_query_to(manager_client, pid)
    csrf = extract_csrf(manager_client.get(f"/project/{pid}").text)

    # Порядок: q2.Intel, q1.AMD, q2.AMD → позиции 1, 2, 3.
    for q, mfg in [(q2, "Intel"), (q1, "AMD"), (q2, "AMD")]:
        _ajax(manager_client, f"/project/{pid}/select",
              {"query_id": q, "variant_manufacturer": mfg, "quantity": 1},
              csrf=csrf)
    r = _ajax(manager_client, f"/project/{pid}/select",
              {"query_id": q1, "variant_manufacturer": "Intel", "quantity": 1},
              csrf=csrf)
    items = r.json()["items"]
    # Проверяем порядок: q2/Intel (1), q1/AMD (2), q2/AMD (3), q1/Intel (4).
    order = [(it["query_id"], it["variant_manufacturer"]) for it in items]
    assert order == [
        (q2, "Intel"), (q1, "AMD"), (q2, "AMD"), (q1, "Intel"),
    ]


def test_select_idempotent_same_click_twice(manager_client, mock_process_query):
    pid = _create_project(manager_client)
    qid = _submit_query_to(manager_client, pid)
    csrf = extract_csrf(manager_client.get(f"/project/{pid}").text)

    _ajax(manager_client, f"/project/{pid}/select",
          {"query_id": qid, "variant_manufacturer": "Intel", "quantity": 2},
          csrf=csrf)
    r = _ajax(manager_client, f"/project/{pid}/select",
              {"query_id": qid, "variant_manufacturer": "Intel", "quantity": 7},
              csrf=csrf)
    items = r.json()["items"]
    assert len(items) == 1
    # Первое quantity (2) сохранилось — повторный select игнорируется.
    assert items[0]["quantity"] == 2


# ==========================================================================
# Доступ: чужой/админ
# ==========================================================================

def test_foreign_manager_cannot_access_project(
    app_client, manager_user, manager2_user
):
    from app.main import app
    with TestClient(app, follow_redirects=False) as c1:
        _login(c1, manager_user["login"], manager_user["password"])
        pid = _create_project(c1)

    _login(app_client, manager2_user["login"], manager2_user["password"])
    r = app_client.get(f"/project/{pid}")
    assert r.status_code == 403


def test_admin_can_access_any_project(
    app_client, manager_user, admin_user
):
    from app.main import app
    with TestClient(app, follow_redirects=False) as c1:
        _login(c1, manager_user["login"], manager_user["password"])
        pid = _create_project(c1)

    _login(app_client, admin_user["login"], admin_user["password"])
    r = app_client.get(f"/project/{pid}")
    assert r.status_code == 200


# ==========================================================================
# Переименование, удаление
# ==========================================================================

def test_rename_project_changes_name(manager_client, db_session):
    pid = _create_project(manager_client)
    csrf = extract_csrf(manager_client.get(f"/project/{pid}").text)
    r = manager_client.post(
        f"/project/{pid}/rename",
        data={"name": "Новое имя проекта", "csrf_token": csrf},
    )
    assert r.status_code == 302
    name = db_session.execute(
        _t("SELECT name FROM projects WHERE id = :pid"), {"pid": pid},
    ).scalar()
    assert name == "Новое имя проекта"


def test_delete_project_removes_spec_items(
    manager_client, mock_process_query, db_session
):
    pid = _create_project(manager_client)
    qid = _submit_query_to(manager_client, pid)
    csrf = extract_csrf(manager_client.get(f"/project/{pid}").text)
    _ajax(manager_client, f"/project/{pid}/select",
          {"query_id": qid, "variant_manufacturer": "Intel", "quantity": 1},
          csrf=csrf)

    r = manager_client.post(
        f"/project/{pid}/delete",
        data={"csrf_token": csrf},
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/projects"

    cnt_p = db_session.execute(
        _t("SELECT COUNT(*) FROM projects WHERE id = :pid"), {"pid": pid},
    ).scalar()
    cnt_s = db_session.execute(
        _t("SELECT COUNT(*) FROM specification_items WHERE project_id = :pid"),
        {"pid": pid},
    ).scalar()
    assert cnt_p == 0
    assert cnt_s == 0


def test_delete_query_removes_its_spec_items(
    manager_client, mock_process_query, db_session
):
    pid = _create_project(manager_client)
    qid = _submit_query_to(manager_client, pid)
    csrf = extract_csrf(manager_client.get(f"/project/{pid}").text)
    _ajax(manager_client, f"/project/{pid}/select",
          {"query_id": qid, "variant_manufacturer": "Intel", "quantity": 1},
          csrf=csrf)

    r = manager_client.post(
        f"/project/{pid}/query/{qid}/delete",
        data={"csrf_token": csrf},
    )
    assert r.status_code == 302

    cnt_q = db_session.execute(
        _t("SELECT COUNT(*) FROM queries WHERE id = :qid"), {"qid": qid},
    ).scalar()
    cnt_s = db_session.execute(
        _t("SELECT COUNT(*) FROM specification_items WHERE project_id = :pid"),
        {"pid": pid},
    ).scalar()
    assert cnt_q == 0
    assert cnt_s == 0


# ==========================================================================
# CSRF на AJAX
# ==========================================================================

def test_csrf_required_on_ajax_select(manager_client, mock_process_query):
    pid = _create_project(manager_client)
    qid = _submit_query_to(manager_client, pid)
    # Без заголовка X-CSRF-Token.
    r = manager_client.post(
        f"/project/{pid}/select",
        json={"query_id": qid, "variant_manufacturer": "Intel", "quantity": 1},
    )
    assert r.status_code == 400


def test_csrf_required_on_ajax_deselect(manager_client, mock_process_query):
    pid = _create_project(manager_client)
    qid = _submit_query_to(manager_client, pid)
    r = manager_client.post(
        f"/project/{pid}/deselect",
        json={"query_id": qid, "variant_manufacturer": "Intel"},
    )
    assert r.status_code == 400
