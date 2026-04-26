"""Тесты этапа 9А.2.3.

Покрывают:
  - Блок A: миграция exchange_rates, fetch+store, плашка курса в sidebar,
    рендер RUB на лету (usd × current_rate);
  - Блок B: reoptimize, rollback;
  - Блок C: фиксированный sidebar (CSS-токены в собранном main.css);
  - Блок D: пагинация по номерам страниц;
  - Блок E: toggle text update (data-toggle-text атрибуты);
  - Блок F: позиция toast'ов (CSS-класс kt-toast-container c bottom/right).
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text as _t

from app.services import spec_recalc, spec_service
from app.services.export import exchange_rate


# ---------------------------------------------------------------------
# Утилиты
# ---------------------------------------------------------------------

def _seed_user(db, *, login="r3-user") -> int:
    from app.auth import hash_password
    row = db.execute(
        _t(
            "INSERT INTO users (login, password_hash, role, name) "
            "VALUES (:l, :p, 'manager', :n) RETURNING id"
        ),
        {"l": login, "p": hash_password("x"), "n": login},
    ).first()
    db.commit()
    return int(row.id)


def _seed_supplier(db, *, name="Sup3", is_active=True) -> int:
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


def _seed_cpu(db, *, model, price_usd, supplier_id, hidden=False, stock=5):
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
    cid = int(row.id)
    db.execute(
        _t(
            "INSERT INTO supplier_prices "
            "(category, component_id, supplier_id, supplier_sku, price, currency, "
            " stock_qty, transit_qty) "
            "VALUES ('cpu', :cid, :sid, :ssku, :p, 'USD', :st, 0)"
        ),
        {"cid": cid, "sid": supplier_id, "ssku": f"SP-{model}", "p": price_usd, "st": stock},
    )
    db.commit()
    return cid


def _make_query_with_one_cpu(db, *, project_id, user_id, cpu_id, cpu_price_usd,
                             manufacturer="Intel", usd_rub=90.0):
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
                        "supplier":     "Sup3",
                        "supplier_sku": "SP-1",
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
        {"pid": project_id, "uid": user_id,
         "br": json.dumps(build_result, ensure_ascii=False)},
    ).first()
    db.commit()
    return int(row.id)


def _select_into_spec(db, *, project_id, query_id, manufacturer="Intel", qty=1):
    items = spec_service.select_variant(
        db, project_id=project_id, query_id=query_id,
        manufacturer=manufacturer, quantity=qty,
    )
    return items[-1]


# =====================================================================
# A. Курс ЦБ
# =====================================================================

def test_exchange_rate_table_created(db_session):
    """Миграция 015: таблица exchange_rates существует."""
    row = db_session.execute(_t(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_name = 'exchange_rates'"
    )).first()
    assert row is not None


def test_get_current_rate_reads_from_db(db_session):
    """get_current_rate возвращает запись из БД."""
    db_session.execute(_t(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source, fetched_at) "
        "VALUES (:d, 95.27, 'cbr', NOW())"
    ), {"d": date.today()})
    db_session.commit()
    info = exchange_rate.get_current_rate(db_session)
    assert float(info.rate) == 95.27


def test_currency_widget_renders_in_sidebar(manager_client, db_session):
    """В шаблоне sidebar появляется блок с курсом, когда в БД есть запись."""
    db_session.execute(_t(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source, fetched_at) "
        "VALUES (:d, 95.27, 'cbr', NOW())"
    ), {"d": date.today()})
    db_session.commit()
    r = manager_client.get("/")
    assert r.status_code == 200
    # Плашка содержит «$ = 95.27 ₽» и класс kt-fx-widget.
    assert "kt-fx-widget" in r.text
    assert "95.27" in r.text


def test_rub_calculated_on_fly(manager_client, db_session, manager_user):
    """Цена в RUB на странице проекта = unit_usd × current_rate из БД."""
    # Курс 100 ₽/$ — простые числа для проверки.
    db_session.execute(_t(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source, fetched_at) "
        "VALUES (:d, 100.00, 'cbr', NOW())"
    ), {"d": date.today()})
    db_session.commit()

    uid = manager_user["id"]
    pid = spec_service.create_empty_project(db_session, user_id=uid, name="P-fx")
    sid = _seed_supplier(db_session, name="SupFX")
    cpu_id = _seed_cpu(db_session, model="CPU-FX", price_usd=200.0, supplier_id=sid)
    qid = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu_id, cpu_price_usd=200.0, usd_rub=90.0,
    )
    _select_into_spec(db_session, project_id=pid, query_id=qid, qty=1)

    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200
    # 200 USD × 100 = 20 000 ₽ — должна быть в HTML (ИТОГО спецификации).
    assert "20 000 ₽" in r.text


# =====================================================================
# B. Reoptimize / rollback
# =====================================================================

def test_reoptimize_unavailable_when_no_parsed_query_snapshot(db_session, manager_user):
    """Если у позиции нет parsed_query_snapshot и build_request_json — status='unavailable'."""
    uid = manager_user["id"]
    pid = spec_service.create_empty_project(db_session, user_id=uid, name="PR1")
    sid = _seed_supplier(db_session, name="SupR1")
    cpu_id = _seed_cpu(db_session, model="CPU-R1", price_usd=200.0, supplier_id=sid)
    qid = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu_id, cpu_price_usd=200.0,
    )
    _select_into_spec(db_session, project_id=pid, query_id=qid, qty=1)
    # Гарантируем отсутствие snapshot'а.
    db_session.execute(_t(
        "UPDATE specification_items SET parsed_query_snapshot = NULL"
    ))
    db_session.execute(_t(
        "UPDATE queries SET build_request_json = NULL WHERE id = :qid"
    ), {"qid": qid})
    db_session.commit()

    res = spec_recalc.reoptimize_specification(db_session, project_id=pid)
    assert res.total_count == 1
    delta = res.items[0]
    assert delta.status == "unavailable"


def test_reoptimize_per_item_works(db_session, manager_user):
    """reoptimize_specification_item возвращает дельту по одной позиции."""
    uid = manager_user["id"]
    pid = spec_service.create_empty_project(db_session, user_id=uid, name="PR2")
    sid = _seed_supplier(db_session, name="SupR2")
    cpu_id = _seed_cpu(db_session, model="CPU-R2", price_usd=150.0, supplier_id=sid)
    qid = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu_id, cpu_price_usd=150.0,
    )
    item = _select_into_spec(db_session, project_id=pid, query_id=qid, qty=1)

    delta = spec_recalc.reoptimize_specification_item(db_session, item_id=item["id"])
    assert delta is not None
    assert delta.spec_item_id == item["id"]
    # При наличии только CPU build_config не сможет собрать конфигурацию —
    # ожидаем status='unavailable'.
    assert delta.status in ("unavailable", "no_changes", "reoptimized")


def test_rollback_returns_false_when_nothing_to_rollback(db_session, manager_user):
    """rollback_specification_item возвращает False, если previous_* пуст."""
    uid = manager_user["id"]
    pid = spec_service.create_empty_project(db_session, user_id=uid, name="PR3")
    sid = _seed_supplier(db_session, name="SupR3")
    cpu_id = _seed_cpu(db_session, model="CPU-R3", price_usd=100.0, supplier_id=sid)
    qid = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu_id, cpu_price_usd=100.0,
    )
    item = _select_into_spec(db_session, project_id=pid, query_id=qid, qty=1)
    ok = spec_recalc.rollback_specification_item(db_session, item_id=item["id"])
    assert ok is False


def test_rollback_restores_previous_snapshot(db_session, manager_user):
    """Если в БД есть previous_*, rollback восстанавливает цены."""
    uid = manager_user["id"]
    pid = spec_service.create_empty_project(db_session, user_id=uid, name="PR4")
    sid = _seed_supplier(db_session, name="SupR4")
    cpu_id = _seed_cpu(db_session, model="CPU-R4", price_usd=100.0, supplier_id=sid)
    qid = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu_id, cpu_price_usd=100.0,
    )
    item = _select_into_spec(db_session, project_id=pid, query_id=qid, qty=2)

    # Подделываем «было» — кладём previous_*.
    db_session.execute(_t(
        "UPDATE specification_items SET "
        "  unit_usd = 80.00, total_usd = 160.00, "
        "  previous_unit_usd  = 100.00, "
        "  previous_total_usd = 200.00, "
        "  previous_build_result_json = CAST(:prev AS JSONB), "
        "  reoptimized_at = NOW() "
        "WHERE id = :id"
    ), {
        "id":   item["id"],
        "prev": json.dumps({"manufacturer": "Intel", "components": [], "total_usd": 100.0}),
    })
    db_session.commit()

    ok = spec_recalc.rollback_specification_item(db_session, item_id=item["id"])
    assert ok is True

    row = db_session.execute(_t(
        "SELECT unit_usd, total_usd, previous_build_result_json "
        "FROM specification_items WHERE id = :id"
    ), {"id": item["id"]}).first()
    assert float(row.unit_usd) == 100.0
    assert float(row.total_usd) == 200.0
    assert row.previous_build_result_json is None


def test_reoptimize_routes_exist(manager_client, db_session, manager_user):
    """POST /spec/reoptimize и /spec/{item_id}/reoptimize отдают JSON."""
    uid = manager_user["id"]
    pid = spec_service.create_empty_project(db_session, user_id=uid, name="P-Routes")
    sid = _seed_supplier(db_session, name="SupRoutes")
    cpu_id = _seed_cpu(db_session, model="CPU-Routes", price_usd=100.0, supplier_id=sid)
    qid = _make_query_with_one_cpu(
        db_session, project_id=pid, user_id=uid,
        cpu_id=cpu_id, cpu_price_usd=100.0,
    )
    _select_into_spec(db_session, project_id=pid, query_id=qid, qty=1)

    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200
    m = re.search(r'name="csrf-token" content="([^"]+)"', r.text)
    token = m.group(1)

    r = manager_client.post(
        f"/project/{pid}/spec/reoptimize",
        json={}, headers={"X-CSRF-Token": token},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert "recalc" in data
    assert data["recalc"]["total_count"] == 1


# =====================================================================
# C. Фиксированный sidebar — проверяем, что в собранном CSS появились токены
# =====================================================================

@pytest.fixture(scope="session")
def main_css() -> str:
    return Path("static/dist/main.css").read_text(encoding="utf-8")


def test_sidebar_has_fixed_height(main_css):
    """В собранном CSS у .kt-sidebar есть height:100vh."""
    assert ".kt-sidebar" in main_css
    # Минификатор может писать height:100vh без пробелов.
    assert re.search(r"\.kt-sidebar\s*\{[^}]*height:\s*100vh", main_css)


def test_main_has_overflow_auto(main_css):
    """У .kt-main есть overflow-y:auto."""
    assert ".kt-main" in main_css
    assert re.search(r"\.kt-main\s*\{[^}]*overflow-y:\s*auto", main_css)


# =====================================================================
# D. Пагинация по номерам
# =====================================================================

def _seed_many_components(db, n=200):
    """Создаём поставщика и N CPU с прайсами, чтобы получить ≥ страниц."""
    sid = _seed_supplier(db, name="SupPag")
    for i in range(n):
        _seed_cpu(db, model=f"CPU-Pag-{i:03}", price_usd=100.0 + i, supplier_id=sid)


def test_pagination_renders_page_numbers(admin_client, db_session):
    """На странице компонентов (большой выдаче) видны кнопки страниц."""
    _seed_many_components(db_session, n=200)  # 200 / 30 (default) = 7 страниц
    r = admin_client.get("/admin/components")
    assert r.status_code == 200
    # Должна быть хотя бы одна кнопка с номером страницы — класс kt-pagination-page.
    assert "kt-pagination-page" in r.text


def test_pagination_active_page_marked(admin_client, db_session):
    """Текущая страница имеет класс kt-pagination-page-active."""
    _seed_many_components(db_session, n=200)
    r = admin_client.get("/admin/components?page=2")
    assert r.status_code == 200
    assert "kt-pagination-page-active" in r.text


def test_pagination_dots_for_long_ranges(admin_client, db_session):
    """На большой выдаче в пагинации появляется «…» между страницами."""
    _seed_many_components(db_session, n=600)  # 600/30 = 20 страниц
    r = admin_client.get("/admin/components?page=10")
    assert r.status_code == 200
    assert "kt-pagination-ellipsis" in r.text or "…" in r.text


# =====================================================================
# E. Toggle text update — наличие data-атрибутов и class kt-toggle-text
# =====================================================================

def test_toggle_text_attributes_in_supplier_form(admin_client):
    """Форма поставщика содержит data-toggle-text и class kt-toggle-text."""
    r = admin_client.get("/admin/suppliers/new")
    assert r.status_code == 200
    assert 'class="toggle kt-toggle"' in r.text
    assert 'data-toggle-text="s-is-active"' in r.text
    assert 'data-toggle-on=' in r.text
    assert 'data-toggle-off=' in r.text


def test_common_js_referenced_from_base(admin_client):
    """common.js подключён в base.html — без него toggle-text не работает."""
    r = admin_client.get("/admin")
    assert r.status_code == 200
    assert "/static/js/common.js" in r.text


# =====================================================================
# F. Toast position — CSS-токены (правый нижний угол)
# =====================================================================

def test_toast_container_in_bottom_right(main_css):
    """В CSS у .kt-toast-container есть bottom и right."""
    assert ".kt-toast-container" in main_css
    # минификатор может удалить пробелы — проверим без них.
    block = re.search(r"\.kt-toast-container\s*\{[^}]*\}", main_css)
    assert block is not None
    body = block.group(0)
    assert "bottom:" in body
    assert "right:" in body
    assert "position:fixed" in body
