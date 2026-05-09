"""Тесты правил сравнения атрибутов (Волна 2)."""
from __future__ import annotations

from app.modules.auctions.match.attribute_rules import (
    CRITICAL,
    OPTIONAL,
    check_attribute,
    get_rule,
    is_critical,
)


def test_critical_attrs_classification():
    for a in ("max_format", "colorness", "duplex", "print_speed_ppm", "print_technology"):
        assert is_critical(a), f"{a} должен быть critical"


def test_optional_attrs_classification():
    for a in ("resolution_dpi", "usb", "network_interface", "starter_cartridge_pages"):
        assert not is_critical(a)


def test_unknown_attr_defaults_to_optional():
    rule = get_rule("какой-нибудь_атрибут_не_в_таблице")
    assert rule.group == OPTIONAL


def test_eq_rule_pass():
    res = check_attribute("colorness", sku_value="ч/б", item_value="ч/б")
    assert res.passed
    assert not res.needs_manual_verification


def test_eq_rule_fail_critical():
    res = check_attribute("colorness", sku_value="цветной", item_value="ч/б")
    assert not res.passed
    assert res.group == CRITICAL


def test_ge_rule_pass():
    # SKU выдаёт 30 ppm, лот требует ≥ 25 — проходит
    res = check_attribute("print_speed_ppm", sku_value=30, item_value=25)
    assert res.passed


def test_ge_rule_fail():
    res = check_attribute("print_speed_ppm", sku_value=20, item_value=25)
    assert not res.passed


def test_na_critical_marks_manual_verification_but_passes():
    res = check_attribute("colorness", sku_value="n/a", item_value="ч/б")
    assert res.passed, "SKU с n/a в критическом не отбрасывается"
    assert res.needs_manual_verification


def test_na_optional_does_not_mark_manual_verification():
    res = check_attribute("usb", sku_value="n/a", item_value="yes")
    assert res.passed
    assert not res.needs_manual_verification


def test_in_list_rule_pass():
    res = check_attribute("network_interface", sku_value=["LAN", "WiFi"], item_value="WiFi")
    assert res.passed


def test_in_list_rule_fail():
    res = check_attribute("network_interface", sku_value=["LAN"], item_value="WiFi")
    assert not res.passed


def test_ge_with_string_inputs():
    # Защита от типа: SKU вернул строку int — должно конвертироваться
    res = check_attribute("print_speed_ppm", sku_value="30", item_value="25")
    assert res.passed


def test_none_sku_value_treated_as_na():
    res = check_attribute("max_format", sku_value=None, item_value="A4")
    assert res.passed
    assert res.needs_manual_verification, "None == n/a → ставим manual для critical"


def test_print_technology_electro_equiv_laser():
    """Лот требует «электрографическая», SKU — «лазерная»: эквивалентно (одно семейство)."""
    res = check_attribute("print_technology", sku_value="лазерная", item_value="электрографическая")
    assert res.passed
    assert not res.needs_manual_verification


def test_print_technology_electro_equiv_led():
    """Лот требует «электрографическая», SKU — «светодиодная»: тоже одно семейство."""
    res = check_attribute("print_technology", sku_value="светодиодная", item_value="электрографическая")
    assert res.passed


def test_print_technology_laser_equiv_led():
    """Симметрия: «лазерная» в одной стороне, «светодиодная» в другой — эквивалентны."""
    res = check_attribute("print_technology", sku_value="лазерная", item_value="светодиодная")
    assert res.passed


def test_print_technology_inkjet_does_not_match_laser():
    """Струйная и лазерная — разные семейства, в эквивалентность не входят."""
    res = check_attribute("print_technology", sku_value="струйная", item_value="лазерная")
    assert not res.passed


def test_print_technology_rule_hits_keep_original_values():
    """Нормализация для сравнения не должна искажать значения в rule_hits — туда
    кладём оригинал, чтобы менеджер видел, что реально было в SKU и в требовании."""
    res = check_attribute("print_technology", sku_value="лазерная", item_value="электрографическая")
    assert res.sku_value == "лазерная"
    assert res.item_value == "электрографическая"
