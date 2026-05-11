"""Тесты пересчёта цен в спецификации проекта (этап 9А.2.1)."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text as _t

from portal.services.configurator import spec_recalc, spec_service


# ---------------------------------------------------------------------
#  Утилиты подготовки данных
# ---------------------------------------------------------------------

def _seed_user(db, *, login="recalc-user") -> int:
    from shared.auth import hash_password
    row = db.execute(
        _t(
            "INSERT INTO users (login, password_hash, role, name) "
            "VALUES (:l, :p, 'manager', :n) RETURNING id"
        ),
        {"l": login, "p": hash_password("x"), "n": login},
    ).first()
    db.commit()
    return int(row.id)


def _seed_supplier(db, *, name="SupRecalc", is_active=True) -> int:
    row = db.execute(
        _t(
            "INSERT INTO suppliers (name, is_active) VALUES (:n, :a) "
            "ON CONFLICT (name) DO UPDATE SET is_active = EXCLUDED.is_active "
            "RETURNING id"
        ),
        {"n": name, "a": is_active},
    ).first()
    db.commit()
    return int(row.id)


def _seed_cpu(db, *, model="CPU R", price_usd, supplier_id, hidden=False,
              stock=5, supplier_name=None) -> int:
    """CPU + supplier_prices в USD."""
    row = db.execute(
        _t(
            "INSERT INTO cpus (model, manufacturer, sku, socket, cores, threads, "
            "                  base_clock_ghz, tdp_watts, has_integrated_graphics, "
            "                  memory_type, package_type, is_hidden) "
            "VALUES (:m, 'Intel', :sku, 'LGA1700', 6, 12, 3.0, 65, FALSE, 'DDR5', "
            "        'BOX', :h) RETURNING id"
        ),
        {"m": model, "sku": f"SKU-{model}", "h": hidden},
    ).first()
    cpu_id = int(row.id)
    db.execute(
        _t(
            "INSERT INTO supplier_prices "
            "(category, component_id, supplier_id, supplier_sku, price, currency, "
            " stock_qty, transit_qty) "
            "VALUES ('cpu', :cid, :sid, :ssku, :p, 'USD', :st, 0)"
        ),
        {
            "cid": cpu_id, "sid": supplier_id,
            "ssku": f"SUP-{model}", "p": price_usd, "st": stock,
        },
    )
    db.commit()
    return cpu_id


def _add_supplier_price(db, *, component_id, supplier_id, price_usd, stock=5):
    db.execute(
        _t(
            "INSERT INTO supplier_prices "
            "(category, component_id, supplier_id, supplier_sku, price, currency, "
            " stock_qty, transit_qty) "
            "VALUES ('cpu', :cid, :sid, 'SUP-X', :p, 'USD', :st, 0)"
        ),
        {"cid": component_id, "sid": supplier_id, "p": price_usd, "st": stock},
    )
    db.commit()


def _make_query_with_one_cpu(
    db, *, project_id, user_id, cpu_id, cpu_price_usd,
    manufacturer="Intel", usd_rub=90.0,
) -> int:
    """Создаёт queries-запись с build_result, в котором один компонент — CPU."""
    build_result = {
        "status": "ok",
        "variants": [
            {
                "manufacturer": manufacturer,
                "path_used":    "default",
                "used_transit": False,
                "total_usd":    cpu_price_usd,
                "total_rub":    cpu_price_usd * usd_rub,
                "components": [
                    {
                        "category":     "cpu",
                        "component_id": cpu_id,
                        "model":        "Test CPU",
                        "sku":          "TST-CPU",
                        "manufacturer": "Intel",
                        "quantity":     1,
                        "supplier":     "SupRecalc",
                        "supplier_sku": "SUP-1",
                        "price_usd":    cpu_price_usd,
                        "price_rub":    cpu_price_usd * usd_rub,
                        "stock":        5,
                        "in_transit":   False,
                        "also_available_at": [],
                    }
                ],
                "warnings": [],
            }
        ],
        "refusal_reason": None,
        "usd_rub_rate":   usd_rub,
        "fx_source":      "fallback",
    }
    row = db.execute(
        _t(
            "INSERT INTO queries "
            "  (project_id, user_id, raw_text, build_result_json, status, "
            "   cost_usd, cost_rub) "
            "VALUES (:pid, :uid, 'тест', CAST(:br AS JSONB), 'ok', 0, 0) "
            "RETURNING id"
        ),
        {
            "pid": project_id, "uid": user_id,
            "br": json.dumps(build_result, ensure_ascii=False),
        },
    ).first()
    db.commit()
    return int(row.id)


def _select_into_spec(db, *, project_id, query_id, manufacturer="Intel",
                      qty=1) -> dict:
    items = spec_service.select_variant(
        db, project_id=project_id, query_id=query_id,
        manufacturer=manufacturer, quantity=qty,
    )
    return items[-1]


# ---------------------------------------------------------------------
#  Тесты
# ---------------------------------------------------------------------

def test_recalc_full_no_changes(db_session):
    """Пересчёт без изменений в supplier_prices — total_count > 0,
    changed_count = 0."""
    uid = _seed_user(db_session)
    pid = spec_service.create_empty_project(db_session, user_id=uid, name="P1")
    sid = _seed_supplier(db_session, name="SupNoChg")
    cpu_id = _seed_cpu(db_session, model="CPU-A", price_usd=200.0, supplier_id=sid)
    qid = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu_id, cpu_price_usd=200.0,
    )
    _select_into_spec(db_session, project_id=pid, query_id=qid, qty=2)

    result = spec_recalc.recalc_specification(db_session, project_id=pid)

    assert result.total_count == 1
    assert result.changed_count == 0
    delta = result.items[0]
    assert delta.changed is False
    assert delta.status == "ok"
    assert delta.old_unit_usd == 200.0
    assert delta.new_unit_usd == 200.0


def test_recalc_full_with_price_drop(db_session):
    """После снижения цены в supplier_prices пересчёт обновляет total
    и проставляет recalculated_at."""
    uid = _seed_user(db_session)
    pid = spec_service.create_empty_project(db_session, user_id=uid, name="P2")
    sid = _seed_supplier(db_session, name="SupDrop")
    cpu_id = _seed_cpu(db_session, model="CPU-B", price_usd=200.0, supplier_id=sid)
    qid = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu_id, cpu_price_usd=200.0,
    )
    _select_into_spec(db_session, project_id=pid, query_id=qid, qty=3)

    # Цена упала.
    db_session.execute(
        _t(
            "UPDATE supplier_prices SET price = 150.0 "
            "WHERE category = 'cpu' AND component_id = :cid AND supplier_id = :sid"
        ),
        {"cid": cpu_id, "sid": sid},
    )
    db_session.commit()

    result = spec_recalc.recalc_specification(db_session, project_id=pid)
    assert result.changed_count == 1
    delta = result.items[0]
    assert delta.changed is True
    assert delta.status == "ok"
    assert delta.new_unit_usd == 150.0
    assert delta.new_total_usd == 450.0  # 150 * 3

    # БД действительно обновилась.
    row = db_session.execute(
        _t(
            "SELECT unit_usd, total_usd, recalculated_at "
            "FROM specification_items WHERE id = :id"
        ),
        {"id": delta.spec_item_id},
    ).first()
    assert float(row.unit_usd) == 150.0
    assert float(row.total_usd) == 450.0
    assert row.recalculated_at is not None


def test_recalc_single_item(db_session):
    """Пересчёт одного item не трогает остальные."""
    uid = _seed_user(db_session)
    pid = spec_service.create_empty_project(db_session, user_id=uid, name="P3")
    sid = _seed_supplier(db_session, name="SupSingle")
    cpu1 = _seed_cpu(db_session, model="CPU-1", price_usd=100.0, supplier_id=sid)
    cpu2 = _seed_cpu(db_session, model="CPU-2", price_usd=200.0, supplier_id=sid)
    q1 = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu1, cpu_price_usd=100.0,
    )
    q2 = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu2, cpu_price_usd=200.0,
    )
    item1 = _select_into_spec(db_session, project_id=pid, query_id=q1, qty=1)
    item2 = _select_into_spec(db_session, project_id=pid, query_id=q2, qty=1)

    # Меняем цены на оба, пересчитываем только item1.
    db_session.execute(
        _t("UPDATE supplier_prices SET price = 80.0 WHERE component_id = :cid"),
        {"cid": cpu1},
    )
    db_session.execute(
        _t("UPDATE supplier_prices SET price = 250.0 WHERE component_id = :cid"),
        {"cid": cpu2},
    )
    db_session.commit()

    delta = spec_recalc.recalc_specification_item(
        db_session, item_id=item1["id"]
    )
    assert delta is not None
    assert delta.spec_item_id == item1["id"]
    assert delta.changed is True
    assert delta.new_unit_usd == 80.0

    # item2 — не пересчитывался: цена должна быть 200, recalculated_at = NULL.
    row = db_session.execute(
        _t(
            "SELECT unit_usd, recalculated_at FROM specification_items WHERE id = :id"
        ),
        {"id": item2["id"]},
    ).first()
    assert float(row.unit_usd) == 200.0
    assert row.recalculated_at is None


def test_recalc_unavailable_supplier(db_session):
    """Если активный поставщик с минимальной ценой деактивирован,
    выбирается следующий по цене из активных."""
    uid = _seed_user(db_session)
    pid = spec_service.create_empty_project(db_session, user_id=uid, name="P4")
    sid_off = _seed_supplier(db_session, name="SupOff", is_active=True)
    sid_on = _seed_supplier(db_session, name="SupOn", is_active=True)

    cpu_id = _seed_cpu(db_session, model="CPU-X", price_usd=100.0,
                       supplier_id=sid_off)
    # Альтернативный поставщик с ценой 130.
    _add_supplier_price(db_session, component_id=cpu_id,
                        supplier_id=sid_on, price_usd=130.0)

    qid = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu_id, cpu_price_usd=100.0,
    )
    _select_into_spec(db_session, project_id=pid, query_id=qid, qty=1)

    # Деактивируем «дешёвого» поставщика.
    db_session.execute(
        _t("UPDATE suppliers SET is_active = FALSE WHERE id = :id"),
        {"id": sid_off},
    )
    db_session.commit()

    result = spec_recalc.recalc_specification(db_session, project_id=pid)
    delta = result.items[0]
    # Должна быть актуальная цена альтернативного поставщика.
    assert delta.status == "ok"
    assert delta.changed is True
    assert delta.new_unit_usd == 130.0


def test_recalc_no_candidates(db_session):
    """Если все поставщики деактивированы или компонент скрыт —
    возвращается status='unavailable'."""
    uid = _seed_user(db_session)
    pid = spec_service.create_empty_project(db_session, user_id=uid, name="P5")
    sid = _seed_supplier(db_session, name="SupOnly", is_active=True)
    cpu_id = _seed_cpu(db_session, model="CPU-Z", price_usd=100.0, supplier_id=sid)
    qid = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu_id, cpu_price_usd=100.0,
    )
    item = _select_into_spec(db_session, project_id=pid, query_id=qid, qty=2)

    # Скрываем компонент.
    db_session.execute(
        _t("UPDATE cpus SET is_hidden = TRUE WHERE id = :id"),
        {"id": cpu_id},
    )
    db_session.commit()

    result = spec_recalc.recalc_specification(db_session, project_id=pid)
    delta = result.items[0]
    assert delta.status == "unavailable"
    assert delta.changed is False
    assert delta.unavailable_components, "должны вернуться имена компонентов"

    # Цена в БД не изменилась, recalculated_at не проставлен.
    row = db_session.execute(
        _t(
            "SELECT unit_usd, total_usd, recalculated_at "
            "FROM specification_items WHERE id = :id"
        ),
        {"id": item["id"]},
    ).first()
    assert float(row.unit_usd) == 100.0
    assert float(row.total_usd) == 200.0
    assert row.recalculated_at is None


def test_recalc_route_full_returns_json(manager_client, db_session, manager_user):
    """Эндпоинт POST /project/{id}/spec/recalc отдаёт JSON со списком дельт."""
    uid = manager_user["id"]
    pid = spec_service.create_empty_project(db_session, user_id=uid, name="P-R")
    sid = _seed_supplier(db_session, name="SupRoute")
    cpu_id = _seed_cpu(db_session, model="CPU-Route", price_usd=100.0,
                       supplier_id=sid)
    qid = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu_id, cpu_price_usd=100.0,
    )
    _select_into_spec(db_session, project_id=pid, query_id=qid, qty=1)

    # Достаём CSRF — он лежит в meta-теге project_detail.
    r = manager_client.get(f"/configurator/project/{pid}")
    assert r.status_code == 200
    import re
    m = re.search(r'name="csrf-token" content="([^"]+)"', r.text)
    assert m, "csrf-token meta не найден"
    token = m.group(1)

    r = manager_client.post(
        f"/configurator/project/{pid}/spec/recalc",
        json={},
        headers={"X-CSRF-Token": token},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert "recalc" in data
    assert data["recalc"]["total_count"] == 1


def test_recalc_route_other_user_forbidden(manager_client, db_session,
                                           manager2_user):
    """Менеджер не может пересчитать чужой проект."""
    other_uid = manager2_user["id"]
    pid = spec_service.create_empty_project(
        db_session, user_id=other_uid, name="P-other"
    )
    # Берём CSRF с любой страницы текущего пользователя.
    r = manager_client.get("/configurator/")
    assert r.status_code == 200
    import re
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    assert m
    token = m.group(1)

    r = manager_client.post(
        f"/configurator/project/{pid}/spec/recalc",
        json={},
        headers={"X-CSRF-Token": token},
    )
    assert r.status_code == 403
