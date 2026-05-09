"""Правила сравнения атрибутов лот ↔ SKU.

Решение собственника №19 (план, 2026-05-07): атрибуты делятся на critical и optional.
- critical: формат, цветность, дуплекс, скорость печати — SKU отбрасывается, если значение
  у SKU присутствует и **не удовлетворяет** требованию лота. Если у SKU стоит `n/a` —
  SKU остаётся, помечается флагом `needs_manual_verification=True` для этого атрибута.
- optional: всё остальное — не отбрасывает SKU; ` n/a` пропускаем без флага.

Атрибут вне таблицы `ATTRIBUTE_RULES` → автоматически optional (не блокирует матчинг).

Пары значений — ключи как в `app/modules/auctions/catalog/enrichment/schema.py`
(`PRINTER_MFU_ATTRS`); парсер `name_attrs_parser.py` приводит требования лота к тем же
ключам.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.modules.auctions.catalog.enrichment.schema import NA

# Группы атрибутов
CRITICAL = "critical"
OPTIONAL = "optional"

# Поддерживаемые правила сравнения (sku_value vs item_value):
# - "eq"      — равенство (строки/перечисления)
# - "ge"      — sku_value ≥ item_value (минимум, заявленный лотом — лот указывает
#               «не менее N», SKU должен быть как минимум N)
# - "le"      — sku_value ≤ item_value (максимум, заявленный лотом)
# - "in_list" — sku_value (список) содержит item_value (один элемент)
COMPARISON_EQ = "eq"
COMPARISON_GE = "ge"
COMPARISON_LE = "le"
COMPARISON_IN_LIST = "in_list"


@dataclass(frozen=True)
class AttributeRule:
    """Правило сравнения одного атрибута."""

    name: str
    comparison: str
    group: str  # CRITICAL | OPTIONAL


# Эмпирический набор: основан на 188 печатно-релевантных позициях из tender_items
# (zakupki, прогон 2026-05-07). Часть атрибутов в карточках лотов лежит в `name`
# одной строкой — name_attrs_parser извлекает их и приводит к нашей схеме.
ATTRIBUTE_RULES: dict[str, AttributeRule] = {
    # Critical — жёсткий матч, SKU отбрасывается при несоответствии
    "max_format":       AttributeRule("max_format",       COMPARISON_EQ, CRITICAL),
    "colorness":        AttributeRule("colorness",        COMPARISON_EQ, CRITICAL),
    "duplex":           AttributeRule("duplex",           COMPARISON_EQ, CRITICAL),
    "print_speed_ppm":  AttributeRule("print_speed_ppm",  COMPARISON_GE, CRITICAL),
    "print_technology": AttributeRule("print_technology", COMPARISON_EQ, CRITICAL),
    # Optional — пишем в rule_hits, не блокируем
    "resolution_dpi":           AttributeRule("resolution_dpi",           COMPARISON_GE, OPTIONAL),
    "usb":                      AttributeRule("usb",                      COMPARISON_EQ, OPTIONAL),
    "network_interface":        AttributeRule("network_interface",        COMPARISON_IN_LIST, OPTIONAL),
    "starter_cartridge_pages":  AttributeRule("starter_cartridge_pages",  COMPARISON_GE, OPTIONAL),
}

# Эквивалентность значений атрибутов. zakupki в требованиях лота использует
# термин «электрографическая» как обобщение лазерной и светодиодной (это одно
# семейство технологий — печать через сухой тонер, отличие в источнике света).
# В нашей схеме SKU значения хранятся узко («лазерная» / «светодиодная»),
# поэтому при сравнении нормализуем оба значения к каноничному ключу семейства.
# Подтверждено собственником 2026-05-07.
_EQUIVALENCE_GROUPS: dict[str, dict[str, str]] = {
    "print_technology": {
        "электрографическая": "electrographic",
        "лазерная":           "electrographic",
        "светодиодная":       "electrographic",
    },
}


@dataclass(frozen=True)
class AttributeCheck:
    """Результат проверки одного атрибута для пары (item, sku)."""

    attr: str
    group: str
    sku_value: Any
    item_value: Any
    passed: bool
    needs_manual_verification: bool


def get_rule(attr: str) -> AttributeRule:
    """Правило для атрибута; неизвестный атрибут → optional eq, не блокирует."""
    rule = ATTRIBUTE_RULES.get(attr)
    if rule is not None:
        return rule
    return AttributeRule(attr, COMPARISON_EQ, OPTIONAL)


def _normalize_for_compare(attr: str, value: Any) -> Any:
    """Если у атрибута есть группа эквивалентности — приводит значение к каноничному ключу.
    Иначе возвращает значение как есть. В rule_hits мы кладём оригиналы — нормализация
    влияет только на исход сравнения, не на аудитный след."""
    table = _EQUIVALENCE_GROUPS.get(attr)
    if not table:
        return value
    return table.get(value, value)


def _compare(comparison: str, sku_value: Any, item_value: Any) -> bool:
    """True — SKU удовлетворяет требованию лота; False — нет."""
    if comparison == COMPARISON_EQ:
        return sku_value == item_value
    if comparison == COMPARISON_GE:
        try:
            return float(sku_value) >= float(item_value)
        except (TypeError, ValueError):
            return False
    if comparison == COMPARISON_LE:
        try:
            return float(sku_value) <= float(item_value)
        except (TypeError, ValueError):
            return False
    if comparison == COMPARISON_IN_LIST:
        if not isinstance(sku_value, (list, tuple)):
            return False
        return item_value in sku_value
    return False


def check_attribute(attr: str, sku_value: Any, item_value: Any) -> AttributeCheck:
    """Проверка одного атрибута. Поведение:

    - sku_value == NA → SKU не отбрасывается. Для critical ставим
      needs_manual_verification=True; для optional — флаг не выставляется.
    - значение есть → сравниваем по правилу. Для critical: passed=False → SKU
      будет отброшен на уровне matcher.py. Для optional: passed=False — просто
      запись в rule_hits.
    """
    rule = get_rule(attr)
    if sku_value == NA or sku_value is None:
        return AttributeCheck(
            attr=attr,
            group=rule.group,
            sku_value=sku_value,
            item_value=item_value,
            passed=True,
            needs_manual_verification=(rule.group == CRITICAL),
        )
    passed = _compare(
        rule.comparison,
        _normalize_for_compare(attr, sku_value),
        _normalize_for_compare(attr, item_value),
    )
    return AttributeCheck(
        attr=attr,
        group=rule.group,
        sku_value=sku_value,
        item_value=item_value,
        passed=passed,
        needs_manual_verification=False,
    )


def is_critical(attr: str) -> bool:
    return get_rule(attr).group == CRITICAL
