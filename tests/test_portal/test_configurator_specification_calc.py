# Интеграционные unit-тесты вычислений спецификации (этап 6.2).
#
# Проверяют арифметику: unit_usd/rub как снимок варианта, total = unit × qty,
# пересчёт total при update_quantity, обработку некорректного количества.
# Использует реальную тестовую БД (фикстуры из test_web/conftest.py).

from __future__ import annotations

import json

import pytest
from sqlalchemy import text as _t

from portal.services.configurator import spec_service


# --------------------------- helpers ------------------------------------

def _make_project(session, user_id: int, name: str = "Тестовый проект") -> int:
    return spec_service.create_empty_project(session, user_id=user_id, name=name)


def _make_query_with_variant(
    session,
    *,
    project_id: int,
    user_id: int,
    manufacturer: str = "Intel",
    total_usd: float = 500.0,
    total_rub: float = 45000.0,
) -> int:
    """Кладёт в queries минимальный build_result с одним вариантом."""
    build_result = {
        "status": "ok",
        "variants": [
            {
                "manufacturer": manufacturer,
                "path_used":    "default",
                "used_transit": False,
                "total_usd":    total_usd,
                "total_rub":    total_rub,
                "components":   [],
                "warnings":     [],
            }
        ],
        "refusal_reason": None,
        "usd_rub_rate":   90.0,
        "fx_source":      "fallback",
    }
    row = session.execute(
        _t(
            "INSERT INTO queries "
            "  (project_id, user_id, raw_text, build_result_json, status, "
            "   cost_usd, cost_rub) "
            "VALUES (:pid, :uid, :rt, CAST(:br AS JSONB), 'ok', 0, 0) "
            "RETURNING id"
        ),
        {
            "pid": project_id, "uid": user_id,
            "rt": "тест",
            "br": json.dumps(build_result, ensure_ascii=False),
        },
    ).first()
    session.commit()
    return int(row.id)


def _seed_user(session) -> int:
    from shared.auth import hash_password
    row = session.execute(
        _t(
            "INSERT INTO users (login, password_hash, role, name) "
            "VALUES ('calc-user', :p, 'manager', 'Calc Tester') RETURNING id"
        ),
        {"p": hash_password("x")},
    ).first()
    session.commit()
    return int(row.id)


# --------------------------- тесты --------------------------------------

def test_select_stores_unit_and_total_from_variant(db_session):
    uid = _seed_user(db_session)
    pid = _make_project(db_session, uid)
    qid = _make_query_with_variant(
        db_session, project_id=pid, user_id=uid,
        manufacturer="Intel", total_usd=220.0, total_rub=19800.0,
    )

    items = spec_service.select_variant(
        db_session, project_id=pid, query_id=qid,
        manufacturer="Intel", quantity=3,
    )

    assert len(items) == 1
    it = items[0]
    # unit — цена одного ПК; total — ×3.
    assert it["unit_usd"] == 220.0
    assert it["unit_rub"] == 19800.0
    assert it["total_usd"] == 660.0
    assert it["total_rub"] == 59400.0
    assert it["quantity"] == 3
    assert it["position"] == 1
    assert it["variant_manufacturer"] == "Intel"


def test_update_quantity_recalculates_total_not_unit(db_session):
    uid = _seed_user(db_session)
    pid = _make_project(db_session, uid)
    qid = _make_query_with_variant(
        db_session, project_id=pid, user_id=uid,
        total_usd=100.0, total_rub=9000.0,
    )
    spec_service.select_variant(
        db_session, project_id=pid, query_id=qid,
        manufacturer="Intel", quantity=1,
    )

    items = spec_service.update_quantity(
        db_session, project_id=pid, query_id=qid,
        manufacturer="Intel", quantity=5,
    )
    it = items[0]
    assert it["quantity"] == 5
    assert it["unit_usd"] == 100.0           # unit не поменялся
    assert it["unit_rub"] == 9000.0
    assert it["total_usd"] == 500.0          # total пересчитан
    assert it["total_rub"] == 45000.0


def test_zero_or_negative_quantity_rejected(db_session):
    uid = _seed_user(db_session)
    pid = _make_project(db_session, uid)
    qid = _make_query_with_variant(db_session, project_id=pid, user_id=uid)

    with pytest.raises(spec_service.SpecError):
        spec_service.select_variant(
            db_session, project_id=pid, query_id=qid,
            manufacturer="Intel", quantity=0,
        )
    with pytest.raises(spec_service.SpecError):
        spec_service.select_variant(
            db_session, project_id=pid, query_id=qid,
            manufacturer="Intel", quantity=-3,
        )


def test_update_quantity_missing_item_raises(db_session):
    uid = _seed_user(db_session)
    pid = _make_project(db_session, uid)
    qid = _make_query_with_variant(db_session, project_id=pid, user_id=uid)
    # Не выбирали — обновлять нечего.
    with pytest.raises(spec_service.SpecError):
        spec_service.update_quantity(
            db_session, project_id=pid, query_id=qid,
            manufacturer="Intel", quantity=2,
        )


def test_spec_totals_sums_all_rows(db_session):
    uid = _seed_user(db_session)
    pid = _make_project(db_session, uid)
    q1 = _make_query_with_variant(db_session, project_id=pid, user_id=uid,
                                  total_usd=100, total_rub=9000)
    q2 = _make_query_with_variant(db_session, project_id=pid, user_id=uid,
                                  total_usd=250, total_rub=22500)

    spec_service.select_variant(db_session, project_id=pid, query_id=q1,
                                manufacturer="Intel", quantity=2)
    spec_service.select_variant(db_session, project_id=pid, query_id=q2,
                                manufacturer="Intel", quantity=3)

    items = spec_service.list_spec_items(db_session, project_id=pid)
    totals = spec_service.spec_totals(items)
    assert totals["total_usd"] == 100 * 2 + 250 * 3
    assert totals["total_rub"] == 9000 * 2 + 22500 * 3


def test_select_idempotent_same_pair_twice(db_session):
    """Повторный select той же пары (query, manufacturer) не создаёт дубль."""
    uid = _seed_user(db_session)
    pid = _make_project(db_session, uid)
    qid = _make_query_with_variant(db_session, project_id=pid, user_id=uid)

    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=2)
    items = spec_service.select_variant(
        db_session, project_id=pid, query_id=qid,
        manufacturer="Intel", quantity=5,  # второй раз — игнорируется
    )
    assert len(items) == 1
    # Осталось первое quantity.
    assert items[0]["quantity"] == 2


def test_deselect_renumbers_positions(db_session):
    uid = _seed_user(db_session)
    pid = _make_project(db_session, uid)
    q1 = _make_query_with_variant(db_session, project_id=pid, user_id=uid)
    q2 = _make_query_with_variant(db_session, project_id=pid, user_id=uid)
    q3 = _make_query_with_variant(db_session, project_id=pid, user_id=uid)

    spec_service.select_variant(db_session, project_id=pid, query_id=q1,
                                manufacturer="Intel", quantity=1)
    spec_service.select_variant(db_session, project_id=pid, query_id=q2,
                                manufacturer="Intel", quantity=1)
    spec_service.select_variant(db_session, project_id=pid, query_id=q3,
                                manufacturer="Intel", quantity=1)

    # Убираем среднюю — позиции должны стать 1, 2 без разрыва.
    items = spec_service.deselect_variant(
        db_session, project_id=pid, query_id=q2,
        manufacturer="Intel",
    )
    positions = [it["position"] for it in items]
    assert positions == [1, 2]
    assert items[0]["query_id"] == q1
    assert items[1]["query_id"] == q3
