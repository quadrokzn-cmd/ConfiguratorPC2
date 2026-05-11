# Тесты обогащения характеристик через regex по supplier_prices.raw_name
# (этап 11.6.1).
#
# Тесты идут через реальную тестовую БД (миграции 001..023): создаём
# скелеты компонентов с NULL-полями, привязываем к supplier_prices с
# raw_name, прогоняем raw_name_runner.run(...) и проверяем результат.

from __future__ import annotations

import pytest
from sqlalchemy import text

from portal.services.configurator.enrichment.raw_name_runner import run


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


def _create_supplier(db_session, name: str) -> int:
    return db_session.execute(
        text("INSERT INTO suppliers (name) VALUES (:n) RETURNING id"),
        {"n": name},
    ).scalar_one()


def _create_component(
    db_session, table: str, *, model: str, manufacturer: str = "Test", **fields
) -> int:
    cols = ["model", "manufacturer"] + list(fields.keys())
    placeholders = [f":{c}" for c in cols]
    params = {"model": model, "manufacturer": manufacturer, **fields}
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) "
        f"VALUES ({', '.join(placeholders)}) RETURNING id"
    )
    return db_session.execute(text(sql), params).scalar_one()


def _attach_supplier_price(
    db_session, *, supplier_id: int, category: str, component_id: int,
    raw_name: str, supplier_sku: str = "SKU",
) -> None:
    db_session.execute(
        text(
            "INSERT INTO supplier_prices "
            "    (supplier_id, category, component_id, supplier_sku, "
            "     price, stock_qty, transit_qty, raw_name) "
            "VALUES "
            "    (:supplier_id, :category, :component_id, :supplier_sku, "
            "     :price, :stock, :transit, :raw_name)"
        ),
        {
            "supplier_id":  supplier_id,
            "category":     category,
            "component_id": component_id,
            "supplier_sku": supplier_sku,
            "price":        100.0,
            "stock":        1,
            "transit":      0,
            "raw_name":     raw_name,
        },
    )


def _read_field(db_session, table: str, comp_id: int, field: str):
    return db_session.execute(
        text(f"SELECT {field} FROM {table} WHERE id = :id"),
        {"id": comp_id},
    ).scalar()


def _read_field_sources(db_session, category: str, comp_id: int) -> list[dict]:
    rows = db_session.execute(
        text(
            "SELECT field_name, source, source_detail, confidence "
            "  FROM component_field_sources "
            " WHERE category = :c AND component_id = :id "
            " ORDER BY field_name"
        ),
        {"c": category, "id": comp_id},
    ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 4.1 — извлечение из одного raw_name
# ---------------------------------------------------------------------------


def test_extracts_from_single_raw_name(db_session):
    sup = _create_supplier(db_session, "OCS Distribution")
    comp = _create_component(
        db_session, "cpus",
        model="Core i5-12400F",
        manufacturer="Intel",
    )
    _attach_supplier_price(
        db_session, supplier_id=sup, category="cpu", component_id=comp,
        raw_name="Intel Core i5-12400F 2.5/4.4GHz LGA1700 6C/12T 18MB 65W OEM",
    )
    db_session.commit()

    report = run(categories=["cpu"], dry_run=False)
    db_session.commit()

    assert _read_field(db_session, "cpus", comp, "socket") == "LGA1700"
    assert _read_field(db_session, "cpus", comp, "cores") == 6
    assert _read_field(db_session, "cpus", comp, "threads") == 12
    assert _read_field(db_session, "cpus", comp, "tdp_watts") == 65
    assert float(_read_field(db_session, "cpus", comp, "base_clock_ghz")) == 2.5
    assert float(_read_field(db_session, "cpus", comp, "turbo_clock_ghz")) == 4.4
    assert _read_field(db_session, "cpus", comp, "package_type") == "OEM"

    sources = _read_field_sources(db_session, "cpu", comp)
    # source_detail='from_raw_name' для regex-полей
    regex_rows = [s for s in sources if s["source"] == "regex"]
    assert len(regex_rows) >= 4, f"ожидаем 4+ regex-полей, получили {regex_rows}"
    for r in regex_rows:
        assert r["source_detail"] == "from_raw_name", (
            f"ожидаем source_detail='from_raw_name', получили {r}"
        )
    # cpu-stats в отчёте
    assert report.by_category["cpu"].components_updated == 1
    assert report.by_category["cpu"].fields_written >= 4


# ---------------------------------------------------------------------------
# 4.2 — агрегация из нескольких raw_name
# ---------------------------------------------------------------------------


def test_aggregates_from_multiple_raw_names(db_session):
    sup1 = _create_supplier(db_session, "Merlion")
    sup2 = _create_supplier(db_session, "OCS Distribution")
    comp = _create_component(
        db_session, "storages",
        model="WD Blue",
        manufacturer="WD",
    )
    # Первый поставщик пишет ёмкость и интерфейс
    _attach_supplier_price(
        db_session, supplier_id=sup1, category="storage", component_id=comp,
        raw_name="SSD WD Blue 1TB SATA",
        supplier_sku="MR-1",
    )
    # Второй — формфактор и интерфейс
    _attach_supplier_price(
        db_session, supplier_id=sup2, category="storage", component_id=comp,
        raw_name="WD Blue 2.5\" SATA III 6Gb/s",
        supplier_sku="OCS-1",
    )
    db_session.commit()

    run(categories=["storage"], dry_run=False)
    db_session.commit()

    assert _read_field(db_session, "storages", comp, "capacity_gb") == 1000
    assert _read_field(db_session, "storages", comp, "interface") == "SATA"
    assert _read_field(db_session, "storages", comp, "form_factor") == '2.5"'


# ---------------------------------------------------------------------------
# 4.3 — не перезатираем существующие значения
# ---------------------------------------------------------------------------


def test_does_not_overwrite_existing_value(caplog, db_session):
    sup = _create_supplier(db_session, "OCS")
    # Скелет CPU с уже заполненным socket='LGA1200'
    comp = _create_component(
        db_session, "cpus",
        model="Core i5-10400",
        manufacturer="Intel",
        socket="LGA1200",
    )
    _attach_supplier_price(
        db_session, supplier_id=sup, category="cpu", component_id=comp,
        raw_name="Intel Core i5-12400F 2.5/4.4GHz LGA1700 6C/12T 18MB 65W OEM",
    )
    db_session.commit()

    run(categories=["cpu"], dry_run=False)
    db_session.commit()

    # socket НЕ перезаписан
    assert _read_field(db_session, "cpus", comp, "socket") == "LGA1200"
    # Прочие поля при этом могли быть заполнены — это нормально.
    assert _read_field(db_session, "cpus", comp, "cores") == 6


# ---------------------------------------------------------------------------
# 4.4 — dry-run не пишет в БД
# ---------------------------------------------------------------------------


def test_dry_run_does_not_write(db_session):
    sup = _create_supplier(db_session, "OCS")
    comp = _create_component(
        db_session, "cpus",
        model="Core i5-12400F",
        manufacturer="Intel",
    )
    _attach_supplier_price(
        db_session, supplier_id=sup, category="cpu", component_id=comp,
        raw_name="Intel Core i5-12400F 2.5/4.4GHz LGA1700 6C/12T 18MB 65W OEM",
    )
    db_session.commit()

    report = run(categories=["cpu"], dry_run=True)
    db_session.commit()

    # В БД — всё ещё NULL
    assert _read_field(db_session, "cpus", comp, "socket") is None
    assert _read_field(db_session, "cpus", comp, "cores") is None
    sources = _read_field_sources(db_session, "cpu", comp)
    assert sources == [], f"в dry-run не должно быть записей в CFS, получили {sources}"
    # Но отчёт показывает «было бы записано»
    assert report.by_category["cpu"].components_updated == 1
    assert report.by_category["cpu"].fields_written >= 4


# ---------------------------------------------------------------------------
# 4.5 — фильтр по supplier
# ---------------------------------------------------------------------------


def test_filter_by_supplier(db_session):
    sup_merlion = _create_supplier(db_session, "Merlion")
    sup_netlab = _create_supplier(db_session, "Netlab")

    # Компонент 1: только raw_name с TDP от Netlab
    comp1 = _create_component(db_session, "cpus", model="i5-12400")
    _attach_supplier_price(
        db_session, supplier_id=sup_netlab, category="cpu", component_id=comp1,
        raw_name="Intel Core i5-12400F 2.5/4.4GHz LGA1700 6C/12T 18MB 65W OEM",
        supplier_sku="N1",
    )

    # Компонент 2: только raw_name с TDP от Merlion
    comp2 = _create_component(db_session, "cpus", model="i7-13700")
    _attach_supplier_price(
        db_session, supplier_id=sup_merlion, category="cpu", component_id=comp2,
        raw_name="Intel Core i7-13700K 3.4/5.4GHz LGA1700 16C/24T 30MB 125W BOX",
        supplier_sku="M1",
    )
    db_session.commit()

    # Фильтр netlab → обогащается только comp1
    run(categories=["cpu"], supplier="netlab", dry_run=False)
    db_session.commit()

    assert _read_field(db_session, "cpus", comp1, "tdp_watts") == 65
    assert _read_field(db_session, "cpus", comp2, "tdp_watts") is None

    # Фильтр merlion → обогащается comp2
    run(categories=["cpu"], supplier="merlion", dry_run=False)
    db_session.commit()

    assert _read_field(db_session, "cpus", comp2, "tdp_watts") == 125


# ---------------------------------------------------------------------------
# 4.6 — фильтр по category
# ---------------------------------------------------------------------------


def test_filter_by_category(db_session):
    sup = _create_supplier(db_session, "OCS")
    cpu_id = _create_component(db_session, "cpus", model="i5-12400")
    ram_id = _create_component(db_session, "rams", model="DDR4 3200")
    _attach_supplier_price(
        db_session, supplier_id=sup, category="cpu", component_id=cpu_id,
        raw_name="Intel Core i5-12400F 2.5/4.4GHz LGA1700 6C/12T 18MB 65W OEM",
        supplier_sku="C1",
    )
    _attach_supplier_price(
        db_session, supplier_id=sup, category="ram", component_id=ram_id,
        raw_name="Kingston 16GB 3200MHz DDR4 DIMM",
        supplier_sku="R1",
    )
    db_session.commit()

    # Только cpu
    run(categories=["cpu"], dry_run=False)
    db_session.commit()
    assert _read_field(db_session, "cpus", cpu_id, "tdp_watts") == 65
    assert _read_field(db_session, "rams", ram_id, "frequency_mhz") is None

    # Только ram
    run(categories=["ram"], dry_run=False)
    db_session.commit()
    assert _read_field(db_session, "rams", ram_id, "frequency_mhz") == 3200


# ---------------------------------------------------------------------------
# 4.7 — идемпотентность
# ---------------------------------------------------------------------------


def test_idempotency(db_session):
    sup = _create_supplier(db_session, "OCS")
    comp = _create_component(db_session, "cpus", model="i5-12400")
    _attach_supplier_price(
        db_session, supplier_id=sup, category="cpu", component_id=comp,
        raw_name="Intel Core i5-12400F 2.5/4.4GHz LGA1700 6C/12T 18MB 65W OEM",
    )
    db_session.commit()

    # Первый прогон
    report1 = run(categories=["cpu"], dry_run=False)
    db_session.commit()
    sources_after_first = _read_field_sources(db_session, "cpu", comp)
    socket_after_first = _read_field(db_session, "cpus", comp, "socket")

    # Второй прогон
    report2 = run(categories=["cpu"], dry_run=False)
    db_session.commit()
    sources_after_second = _read_field_sources(db_session, "cpu", comp)
    socket_after_second = _read_field(db_session, "cpus", comp, "socket")

    # Ничего не изменилось
    assert socket_after_first == socket_after_second
    # Дубликатов не появилось — UNIQUE(category, component_id, field_name)
    # гарантирует это на уровне схемы; но проверим, что строк столько же.
    assert len(sources_after_first) == len(sources_after_second), (
        f"первый прогон: {len(sources_after_first)} строк, "
        f"второй: {len(sources_after_second)} — появились дубли"
    )
    # Второй прогон ничего не записал
    assert report2.by_category["cpu"].fields_written == 0


# ---------------------------------------------------------------------------
# Доп.тест: конфликт значений из разных raw_name → берётся самый длинный
# ---------------------------------------------------------------------------


def test_conflict_takes_longest_raw_name(db_session):
    sup1 = _create_supplier(db_session, "Merlion")
    sup2 = _create_supplier(db_session, "OCS")
    comp = _create_component(
        db_session, "cpus",
        model="Core i5-?",
        manufacturer="Intel",
    )
    # Короткий raw_name говорит LGA1200
    _attach_supplier_price(
        db_session, supplier_id=sup1, category="cpu", component_id=comp,
        raw_name="i5-10400 LGA1200 OEM",
        supplier_sku="MR-1",
    )
    # Длинный raw_name говорит LGA1700 — он информативнее, его и берём
    _attach_supplier_price(
        db_session, supplier_id=sup2, category="cpu", component_id=comp,
        raw_name=("Intel Core i5-12400F 2.5/4.4GHz LGA1700 6C/12T 18MB 65W OEM "
                  "Alder Lake new gen"),
        supplier_sku="OCS-1",
    )
    db_session.commit()

    report = run(categories=["cpu"], dry_run=False)
    db_session.commit()

    assert _read_field(db_session, "cpus", comp, "socket") == "LGA1700"
    # Конфликт по socket зафиксирован в отчёте
    socket_conflicts = [
        c for c in report.by_category["cpu"].conflicts if c["field"] == "socket"
    ]
    assert socket_conflicts, "ожидаем зафиксированный конфликт по socket"
