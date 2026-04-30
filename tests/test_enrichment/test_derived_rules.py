# Тесты derived-правил обогащения (этап 11.6.2.0).
#
# Тесты идут через реальную тестовую БД (миграции 001..023): создаём
# скелеты компонентов с нужным состоянием полей, прогоняем
# derived_rules.run(...) и проверяем результат — и в самой таблице
# компонента, и в component_field_sources.

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.enrichment.derived_rules import run


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _create_supplier(db_session, name: str = "OCS") -> int:
    return db_session.execute(
        text("INSERT INTO suppliers (name) VALUES (:n) RETURNING id"),
        {"n": name},
    ).scalar_one()


def _insert(db_session, table: str, *, model: str = "Test", manufacturer: str = "Test", **fields) -> int:
    cols = ["model", "manufacturer"] + list(fields.keys())
    placeholders = [f":{c}" for c in cols]
    params = {"model": model, "manufacturer": manufacturer, **fields}
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join(placeholders)}) RETURNING id"
    )
    return db_session.execute(text(sql), params).scalar_one()


def _attach_supplier_price(
    db_session,
    *,
    supplier_id: int,
    category: str,
    component_id: int,
    raw_name: str,
    supplier_sku: str = "SKU",
) -> None:
    db_session.execute(
        text(
            "INSERT INTO supplier_prices "
            "    (supplier_id, category, component_id, supplier_sku, "
            "     price, stock_qty, transit_qty, raw_name) "
            "VALUES "
            "    (:supplier_id, :category, :component_id, :supplier_sku, "
            "     100.0, 1, 0, :raw_name)"
        ),
        {
            "supplier_id":  supplier_id,
            "category":     category,
            "component_id": component_id,
            "supplier_sku": supplier_sku,
            "raw_name":     raw_name,
        },
    )


def _read(db_session, table: str, comp_id: int, field: str):
    return db_session.execute(
        text(f"SELECT {field} FROM {table} WHERE id = :id"),
        {"id": comp_id},
    ).scalar()


def _read_cfs(db_session, category: str, comp_id: int, field_name: str) -> dict | None:
    row = db_session.execute(
        text(
            "SELECT source, source_detail, confidence "
            "  FROM component_field_sources "
            " WHERE category = :c "
            "   AND component_id = :id "
            "   AND field_name = :f"
        ),
        {"c": category, "id": comp_id, "f": field_name},
    ).mappings().first()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Правило 1: cases.has_psu_included = FALSE по маркеру «без БП»
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw_name", [
    "Powerman ATX корпус без БП mid-tower",
    "Foxline FL-301 без блока питания",
    "Chieftec X-Rage III no PSU 2 fans",
    "ATX Case w/o PSU 4 USB",
    "DEXP без бп",
])
def test_rule_1_marks_no_psu_in_case(db_session, raw_name):
    sup = _create_supplier(db_session)
    comp = _insert(db_session, "cases", model="Some Case")
    _attach_supplier_price(
        db_session, supplier_id=sup, category="case",
        component_id=comp, raw_name=raw_name,
    )
    db_session.commit()

    run(rules=["1"], dry_run=False)
    db_session.commit()

    assert _read(db_session, "cases", comp, "has_psu_included") is False
    src = _read_cfs(db_session, "case", comp, "has_psu_included")
    assert src is not None, "ожидаем запись в component_field_sources"
    assert src["source"] == "derived"
    assert src["source_detail"] == "from_no_psu_marker"


def test_rule_1_does_not_set_when_no_marker(db_session):
    sup = _create_supplier(db_session)
    comp = _insert(db_session, "cases", model="Some Case")
    _attach_supplier_price(
        db_session, supplier_id=sup, category="case",
        component_id=comp, raw_name="Generic ATX mid-tower 4 USB",
    )
    db_session.commit()

    run(rules=["1"], dry_run=False)
    db_session.commit()

    assert _read(db_session, "cases", comp, "has_psu_included") is None
    assert _read_cfs(db_session, "case", comp, "has_psu_included") is None


def test_rule_1_does_not_overwrite_existing_value(db_session):
    sup = _create_supplier(db_session)
    # Корпус уже имеет has_psu_included=TRUE (например, из manual)
    comp = _insert(db_session, "cases", model="Powerman", has_psu_included=True)
    _attach_supplier_price(
        db_session, supplier_id=sup, category="case",
        component_id=comp, raw_name="Powerman без БП",
    )
    db_session.commit()

    run(rules=["1"], dry_run=False)
    db_session.commit()

    # has_psu_included НЕ перезаписан
    assert _read(db_session, "cases", comp, "has_psu_included") is True


# ---------------------------------------------------------------------------
# Правило 2: not_applicable_no_psu для cases без БП
# ---------------------------------------------------------------------------


def test_rule_2_marks_not_applicable_for_no_psu_cases(db_session):
    comp = _insert(db_session, "cases", model="Case", has_psu_included=False)
    db_session.commit()

    run(rules=["2"], dry_run=False)
    db_session.commit()

    # Само поле остаётся NULL
    assert _read(db_session, "cases", comp, "included_psu_watts") is None
    # Но в CFS появилась пометка
    src = _read_cfs(db_session, "case", comp, "included_psu_watts")
    assert src is not None
    assert src["source"] == "derived"
    assert src["source_detail"] == "not_applicable_no_psu"


def test_rule_2_skips_cases_with_psu_included(db_session):
    comp = _insert(db_session, "cases", model="Case", has_psu_included=True)
    db_session.commit()

    run(rules=["2"], dry_run=False)
    db_session.commit()

    assert _read_cfs(db_session, "case", comp, "included_psu_watts") is None


def test_rule_2_skips_cases_with_unknown_psu_status(db_session):
    # has_psu_included IS NULL — правило не должно срабатывать
    comp = _insert(db_session, "cases", model="Case")
    db_session.commit()

    run(rules=["2"], dry_run=False)
    db_session.commit()

    assert _read_cfs(db_session, "case", comp, "included_psu_watts") is None


def test_rule_2_skips_when_value_already_set(db_session):
    # included_psu_watts известен — он не NULL, правило неприменимо
    comp = _insert(
        db_session, "cases",
        model="Case", has_psu_included=True, included_psu_watts=500,
    )
    # has_psu_included=TRUE по сути выводит из выборки, но проверим и
    # отдельно: даже если has_psu_included=FALSE и watts случайно стоит,
    # правило 2 не пишет в CFS (поле уже не NULL).
    db_session.commit()

    run(rules=["2"], dry_run=False)
    db_session.commit()

    assert _read_cfs(db_session, "case", comp, "included_psu_watts") is None


def test_rule_1_then_2_chain(db_session):
    """Правила 1 и 2 вместе: rule_1 ставит has_psu_included=FALSE по
    маркеру, rule_2 затем помечает included_psu_watts not_applicable."""
    sup = _create_supplier(db_session)
    comp = _insert(db_session, "cases", model="Case")
    _attach_supplier_price(
        db_session, supplier_id=sup, category="case",
        component_id=comp, raw_name="Case без БП ATX",
    )
    db_session.commit()

    run(rules=["1", "2"], dry_run=False)
    db_session.commit()

    assert _read(db_session, "cases", comp, "has_psu_included") is False
    assert _read(db_session, "cases", comp, "included_psu_watts") is None
    src = _read_cfs(db_session, "case", comp, "included_psu_watts")
    assert src is not None
    assert src["source_detail"] == "not_applicable_no_psu"


# ---------------------------------------------------------------------------
# Правило 4: gpus.needs_extra_power из tdp_watts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("tdp,expected", [
    (180, True),
    (75, False),
    (76, True),
    (50, False),
    (350, True),
])
def test_rule_4_needs_extra_power_from_tdp(db_session, tdp, expected):
    comp = _insert(db_session, "gpus", model="GPU", tdp_watts=tdp)
    db_session.commit()

    run(rules=["4"], dry_run=False)
    db_session.commit()

    assert _read(db_session, "gpus", comp, "needs_extra_power") is expected
    src = _read_cfs(db_session, "gpu", comp, "needs_extra_power")
    assert src is not None
    assert src["source"] == "derived"
    assert src["source_detail"] == "from_tdp_watts"


def test_rule_4_does_not_apply_when_tdp_unknown(db_session):
    comp = _insert(db_session, "gpus", model="GPU")  # tdp_watts NULL
    db_session.commit()

    run(rules=["4"], dry_run=False)
    db_session.commit()

    assert _read(db_session, "gpus", comp, "needs_extra_power") is None
    assert _read_cfs(db_session, "gpu", comp, "needs_extra_power") is None


def test_rule_4_does_not_overwrite_existing_value(db_session):
    # needs_extra_power уже TRUE (manual override), tdp_watts говорит обратное
    comp = _insert(
        db_session, "gpus",
        model="GPU", tdp_watts=50, needs_extra_power=True,
    )
    db_session.commit()

    run(rules=["4"], dry_run=False)
    db_session.commit()

    # значение НЕ перезаписано
    assert _read(db_session, "gpus", comp, "needs_extra_power") is True


# ---------------------------------------------------------------------------
# Правило 5: storages.storage_type из interface=NVMe
# ---------------------------------------------------------------------------


def test_rule_5_storage_type_from_nvme(db_session):
    comp = _insert(db_session, "storages", model="SSD", interface="NVMe")
    db_session.commit()

    run(rules=["5"], dry_run=False)
    db_session.commit()

    assert _read(db_session, "storages", comp, "storage_type") == "SSD"
    src = _read_cfs(db_session, "storage", comp, "storage_type")
    assert src is not None
    assert src["source"] == "derived"
    assert src["source_detail"] == "from_nvme_interface"


def test_rule_5_does_not_apply_for_sata(db_session):
    comp = _insert(db_session, "storages", model="HDD", interface="SATA")
    db_session.commit()

    run(rules=["5"], dry_run=False)
    db_session.commit()

    assert _read(db_session, "storages", comp, "storage_type") is None


def test_rule_5_does_not_overwrite_existing(db_session):
    # storage_type уже HDD (явно указан вручную), но interface=NVMe — странная
    # комбинация, но правило не должно её менять.
    comp = _insert(
        db_session, "storages",
        model="X", interface="NVMe", storage_type="HDD",
    )
    db_session.commit()

    run(rules=["5"], dry_run=False)
    db_session.commit()

    assert _read(db_session, "storages", comp, "storage_type") == "HDD"


# ---------------------------------------------------------------------------
# Сквозные свойства: dry-run, идемпотентность
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write(db_session):
    sup = _create_supplier(db_session)
    case_comp = _insert(db_session, "cases", model="Case")
    _attach_supplier_price(
        db_session, supplier_id=sup, category="case",
        component_id=case_comp, raw_name="Case без БП",
    )
    gpu_comp = _insert(db_session, "gpus", model="GPU", tdp_watts=180)
    db_session.commit()

    report = run(rules=["1", "2", "4", "5"], dry_run=True)
    db_session.commit()

    # БД не изменилась
    assert _read(db_session, "cases", case_comp, "has_psu_included") is None
    assert _read(db_session, "gpus", gpu_comp, "needs_extra_power") is None
    assert _read_cfs(db_session, "case", case_comp, "has_psu_included") is None
    assert _read_cfs(db_session, "gpu", gpu_comp, "needs_extra_power") is None

    # Но отчёт показывает «было бы записано»
    assert report.by_rule["1"].fields_written == 1
    assert report.by_rule["4"].fields_written == 1


def test_idempotency(db_session):
    sup = _create_supplier(db_session)
    case_comp = _insert(db_session, "cases", model="Case")
    _attach_supplier_price(
        db_session, supplier_id=sup, category="case",
        component_id=case_comp, raw_name="Case без БП",
    )
    gpu_comp = _insert(db_session, "gpus", model="GPU", tdp_watts=180)
    db_session.commit()

    # Первый прогон: всё пишется
    report1 = run(rules=["1", "2", "4", "5"], dry_run=False)
    db_session.commit()

    # Второй прогон поверх: ничего нового
    report2 = run(rules=["1", "2", "4", "5"], dry_run=False)
    db_session.commit()

    # Состояние БД одинаковое
    assert _read(db_session, "cases", case_comp, "has_psu_included") is False
    assert _read(db_session, "gpus", gpu_comp, "needs_extra_power") is True

    # Второй прогон ничего не записал
    assert report2.by_rule["1"].fields_written == 0
    assert report2.by_rule["2"].not_applicable_marked == 0
    assert report2.by_rule["4"].fields_written == 0
    assert report2.by_rule["5"].fields_written == 0

    # Дубликатов в CFS нет (UNIQUE по (category, component_id, field_name))
    cfs_count = db_session.execute(
        text(
            "SELECT COUNT(*) FROM component_field_sources "
            " WHERE source = 'derived'"
        )
    ).scalar()
    assert cfs_count == report1.total_fields_written + report1.total_not_applicable_marked
