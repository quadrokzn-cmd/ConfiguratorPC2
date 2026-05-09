"""Тесты ядра матчинга — `match_tender_item`."""
from __future__ import annotations

from decimal import Decimal

from app.services.auctions.match.matcher import (
    NomenclatureView,
    TenderItemView,
    match_tender_item,
    serialize_rule_hits,
)


def _item(
    name: str,
    nmck_per_unit: Decimal | None = Decimal("20000"),
    qty: Decimal = Decimal("1"),
    required_attrs: dict | None = None,
    item_id: int = 1,
) -> TenderItemView:
    return TenderItemView(
        id=item_id,
        tender_id="0816500000626000001",
        position_num=1,
        ktru_code="26.20.18.000-00000069",
        name=name,
        qty=qty,
        unit="шт",
        nmck_per_unit=nmck_per_unit,
        required_attrs_jsonb=required_attrs or {},
    )


def _sku(
    sku: str,
    cost: Decimal | None,
    attrs: dict,
    sku_id: int = 100,
    category: str = "mfu",
) -> NomenclatureView:
    return NomenclatureView(
        id=sku_id,
        sku=sku,
        brand="Pantum",
        name=f"Pantum {sku}",
        category=category,
        ktru_codes_array=["26.20.18.000-00000069"],
        attrs_jsonb=attrs,
        cost_base_rub=cost,
    )


def test_happy_path_single_match():
    item = _item("МФУ Цветность печати Черно-Белая Максимальный формат А4 двухсторонней печати Да")
    sku = _sku(
        "pantum:M6700DW",
        Decimal("12000"),
        {
            "colorness": "ч/б",
            "max_format": "A4",
            "duplex": "yes",
            "print_speed_ppm": 30,
            "usb": "yes",
            "print_technology": "лазерная",
            "network_interface": ["LAN"],
            "resolution_dpi": 1200,
            "starter_cartridge_pages": 1500,
        },
    )
    matches = match_tender_item(item, [sku])
    assert len(matches) == 1
    m = matches[0]
    assert m.match_type == "primary"
    assert m.margin_rub == Decimal("8000.00")
    assert m.margin_pct == Decimal("40.00")
    assert m.price_total_rub == Decimal("20000.00")
    assert not m.needs_manual_verification


def test_critical_attr_mismatch_drops_sku():
    # Лот требует ч/б, SKU — цветной → отбрасывается
    item = _item("МФУ Цветность печати Черно-Белая")
    sku = _sku(
        "pantum:CP1100",
        Decimal("15000"),
        {
            "colorness": "цветной",
            "max_format": "A4",
            "duplex": "yes",
            "print_speed_ppm": 25,
        },
    )
    assert match_tender_item(item, [sku]) == []


def test_na_in_critical_keeps_sku_with_manual_flag():
    item = _item("МФУ Цветность печати Черно-Белая Максимальный формат А4")
    sku = _sku(
        "pantum:UNKNOWN",
        Decimal("10000"),
        {
            "colorness": "n/a",
            "max_format": "A4",
            "duplex": "yes",
        },
    )
    matches = match_tender_item(item, [sku])
    assert len(matches) == 1
    assert matches[0].needs_manual_verification


def test_optional_attr_mismatch_does_not_drop_sku():
    # Лот требует duplex=Да, SKU duplex=yes → ок. usb=yes у лота, у SKU нет —
    # optional не блокирует. Но required attrs из текста не извлеклись для usb,
    # поэтому соберу пример попроще: лот требует только duplex (critical),
    # network_interface в имени отсутствует.
    item = _item("МФУ Цветность печати Цветная Возможность автоматической двухсторонней печати Да")
    sku = _sku(
        "pantum:CP2200",
        Decimal("18000"),
        {
            "colorness": "цветной",
            "duplex": "yes",
            "max_format": "A4",
            "print_speed_ppm": 20,
            "usb": "no",
        },
    )
    matches = match_tender_item(item, [sku])
    assert len(matches) == 1


def test_no_cost_base_drops_sku():
    item = _item("МФУ Цветность печати Черно-Белая")
    sku = _sku(
        "pantum:NO_COST",
        cost=None,
        attrs={"colorness": "ч/б"},
    )
    assert match_tender_item(item, [sku]) == []


def test_no_nmck_per_unit_drops_sku():
    item = _item("МФУ Цветность печати Черно-Белая", nmck_per_unit=None)
    sku = _sku("pantum:OK", Decimal("12000"), {"colorness": "ч/б"})
    assert match_tender_item(item, [sku]) == []


def test_no_candidates_returns_empty():
    item = _item("МФУ")
    assert match_tender_item(item, []) == []


def test_primary_is_cheapest():
    item = _item("МФУ Цветность печати Черно-Белая Максимальный формат А4")
    sku_a = _sku("a", Decimal("15000"), {"colorness": "ч/б", "max_format": "A4"}, sku_id=1)
    sku_b = _sku("b", Decimal("10000"), {"colorness": "ч/б", "max_format": "A4"}, sku_id=2)
    sku_c = _sku("c", Decimal("12000"), {"colorness": "ч/б", "max_format": "A4"}, sku_id=3)
    matches = match_tender_item(item, [sku_a, sku_b, sku_c])
    assert len(matches) == 3
    by_type = {m.sku: m.match_type for m in matches}
    assert by_type["b"] == "primary"
    assert by_type["a"] == "alternative"
    assert by_type["c"] == "alternative"


def test_print_speed_ge_rule_drops_too_slow():
    item = _item("МФУ Скорость черно-белой печати 30 стр/мин")
    too_slow = _sku("slow", Decimal("9000"), {"print_speed_ppm": 20}, sku_id=1)
    fast = _sku("fast", Decimal("15000"), {"print_speed_ppm": 32}, sku_id=2)
    matches = match_tender_item(item, [too_slow, fast])
    assert len(matches) == 1
    assert matches[0].sku == "fast"


def test_qty_used_in_price_total():
    item = _item("МФУ", nmck_per_unit=Decimal("10000"), qty=Decimal("3"))
    sku = _sku("ok", Decimal("8000"), {})
    matches = match_tender_item(item, [sku])
    assert matches[0].price_total_rub == Decimal("30000.00")


def test_print_technology_electro_equiv_passes_laser_sku():
    """Лот требует электрографию; SKU — лазерный. print_technology теперь critical
    (эквивалентность электрография≡лазерная≡светодиодная), поэтому SKU должен
    остаться, а не отброситься."""
    item = _item(
        "МФУ Цветность печати Черно-Белая Максимальный формат А4 "
        "Возможность автоматической двухсторонней печати Да Технология печати Электрографическая"
    )
    sku = _sku(
        "pantum:ELECTRO_OK",
        Decimal("12000"),
        {
            "colorness": "ч/б",
            "max_format": "A4",
            "duplex": "yes",
            "print_speed_ppm": 25,
            "print_technology": "лазерная",
        },
    )
    matches = match_tender_item(item, [sku])
    assert len(matches) == 1
    assert matches[0].match_type == "primary"


def test_print_technology_inkjet_critical_drops_laser_sku():
    """Лот требует струйную, SKU — лазерная. Они в разных семействах,
    эквивалентность не покрывает, print_technology critical → отбрасывается."""
    item = _item(
        "МФУ Цветность печати Черно-Белая Максимальный формат А4 "
        "Возможность автоматической двухсторонней печати Да Технология печати Струйная"
    )
    sku = _sku(
        "hp:LASER",
        Decimal("9000"),
        {
            "colorness": "ч/б",
            "max_format": "A4",
            "duplex": "yes",
            "print_speed_ppm": 25,
            "print_technology": "лазерная",
        },
    )
    assert match_tender_item(item, [sku]) == []


def test_serialize_rule_hits_shape():
    item = _item("МФУ Цветность печати Черно-Белая")
    sku = _sku("ok", Decimal("8000"), {"colorness": "n/a"})
    matches = match_tender_item(item, [sku])
    payload = serialize_rule_hits(matches[0].rule_hits)
    assert payload["needs_manual_verification"] is True
    assert payload["checks"]
    assert payload["checks"][0]["attr"] == "colorness"
    assert payload["checks"][0]["needs_manual_verification"] is True
