"""Матчер позиции лота с SKU-кандидатами.

5 этапов из плана (Волна 2):
1. KTRU — выполняется в repository.py при загрузке кандидатов (ktru_code лота
   совпадает с любым из printers_mfu.ktru_codes_array).
2. атрибуты — здесь, через `name_attrs_parser` + `attribute_rules`.
3. маржа — здесь, в Decimal.
4. выбор primary (минимум cost_base = максимум маржи).
5. сохранение — в repository.save_matches.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any

from portal.services.auctions.match.attribute_rules import (
    AttributeCheck,
    is_critical,
    check_attribute,
)
from portal.services.auctions.match.name_attrs_parser import (
    extract_attrs_from_name,
    merge_required_attrs,
)


@dataclass(frozen=True)
class TenderItemView:
    """Позиция лота в виде, удобном для матчера."""

    id: int
    tender_id: str
    position_num: int
    ktru_code: str | None
    name: str | None
    qty: Decimal
    unit: str | None
    nmck_per_unit: Decimal | None  # м.б. NULL → этап 3 не считаем
    required_attrs_jsonb: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NomenclatureView:
    id: int
    sku: str
    brand: str | None
    name: str | None
    category: str | None
    ktru_codes_array: list[str]
    attrs_jsonb: dict[str, Any]
    cost_base_rub: Decimal | None


@dataclass(frozen=True)
class CandidateMatch:
    """Сматченная пара (tender_item, sku)."""

    tender_item_id: int
    nomenclature_id: int
    sku: str
    cost_base_rub: Decimal
    nmck_per_unit: Decimal
    qty: Decimal
    margin_rub: Decimal
    margin_pct: Decimal
    price_total_rub: Decimal
    rule_hits: list[AttributeCheck]
    needs_manual_verification: bool
    match_type: str  # 'primary' | 'alternative'


def match_tender_item(
    item: TenderItemView,
    candidates: list[NomenclatureView],
) -> list[CandidateMatch]:
    """Запускает этапы 2–4 для одной позиции лота. Возвращает все подходящие SKU
    с проставленными `primary`/`alternative` и посчитанной маржой.

    Возвращает пустой список, если:
    - нет кандидатов после KTRU-фильтра,
    - все отброшены критическим атрибутом,
    - у позиции лота нет `nmck_per_unit` (нечего считать),
    - все survived без `cost_base_rub`.
    """
    if not candidates:
        return []

    item_attrs = merge_required_attrs(
        item.required_attrs_jsonb,
        extract_attrs_from_name(item.name),
    )

    survived: list[CandidateMatch] = []
    for sku in candidates:
        sku_attrs = sku.attrs_jsonb or {}
        checks: list[AttributeCheck] = []
        dropped = False
        for attr, item_val in item_attrs.items():
            sku_val = sku_attrs.get(attr)
            check = check_attribute(attr, sku_val, item_val)
            checks.append(check)
            if not check.passed and is_critical(attr):
                dropped = True
                break
        if dropped:
            continue

        # Этап 3 — маржа. Без cost_base или nmck_per_unit пропускаем (нечего считать).
        if sku.cost_base_rub is None or item.nmck_per_unit is None:
            continue
        nmck = Decimal(item.nmck_per_unit)
        cost = Decimal(sku.cost_base_rub)
        qty = Decimal(item.qty or 1)
        margin_rub = (nmck - cost).quantize(Decimal("0.01"))
        margin_pct = (
            (margin_rub / nmck * 100).quantize(Decimal("0.01"))
            if nmck > 0
            else Decimal("0.00")
        )
        price_total = (nmck * qty).quantize(Decimal("0.01"))
        needs_manual = any(ch.needs_manual_verification for ch in checks)

        survived.append(
            CandidateMatch(
                tender_item_id=item.id,
                nomenclature_id=sku.id,
                sku=sku.sku,
                cost_base_rub=cost,
                nmck_per_unit=nmck,
                qty=qty,
                margin_rub=margin_rub,
                margin_pct=margin_pct,
                price_total_rub=price_total,
                rule_hits=checks,
                needs_manual_verification=needs_manual,
                match_type="alternative",
            )
        )

    if not survived:
        return []

    # Этап 4 — primary = минимум cost_base = максимум margin_rub
    survived.sort(key=lambda c: (c.cost_base_rub, c.nomenclature_id))
    survived[0] = replace(survived[0], match_type="primary")
    return survived


def serialize_rule_hits(checks: list[AttributeCheck]) -> dict[str, Any]:
    """JSON-вид rule_hits для записи в `matches.rule_hits_jsonb`."""
    return {
        "checks": [
            {
                "attr": ch.attr,
                "group": ch.group,
                "sku_value": _to_jsonable(ch.sku_value),
                "item_value": _to_jsonable(ch.item_value),
                "passed": ch.passed,
                "needs_manual_verification": ch.needs_manual_verification,
            }
            for ch in checks
        ],
        "needs_manual_verification": any(ch.needs_manual_verification for ch in checks),
    }


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value
