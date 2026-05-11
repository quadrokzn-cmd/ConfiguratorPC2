"""Тесты name_attrs_parser: извлечение требований лота из tender_items.name."""
from __future__ import annotations

from portal.services.auctions.match.name_attrs_parser import (
    extract_attrs_from_name,
    merge_required_attrs,
)


def test_empty_input():
    assert extract_attrs_from_name(None) == {}
    assert extract_attrs_from_name("") == {}


def test_colorness_chb_full():
    name = "Многофункциональное устройство (МФУ) Цветность печати Черно-Белая"
    out = extract_attrs_from_name(name)
    assert out["colorness"] == "ч/б"


def test_colorness_color():
    name = "Принтер Цветность печати Цветная"
    assert extract_attrs_from_name(name)["colorness"] == "цветной"


def test_max_format_a4_with_cyrillic_a():
    # «А4» — кириллическая А (U+0410); парсер должен это понимать
    name = "Принтер Максимальный формат печати А4"
    assert extract_attrs_from_name(name)["max_format"] == "A4"


def test_max_format_a3_latin():
    name = "Принтер Формат A3"
    assert extract_attrs_from_name(name)["max_format"] == "A3"


def test_max_format_a3_in_scanning_phrase():
    # Реальный кейс из лота 0358200055826000034: max_format=A3 не извлекался
    name = "МФУ Возможность автоматического двухстороннего сканирования Да Возможность сканирования в форматах A3"
    assert extract_attrs_from_name(name)["max_format"] == "A3"


def test_max_format_a3_in_format_phrase():
    # «в формате А3» (кириллическая А)
    name = "МФУ Скорость цветной печати в формате А3, стр/мин ≥ 20"
    assert extract_attrs_from_name(name)["max_format"] == "A3"


def test_max_format_a3_support_phrase():
    name = "МФУ поддержка А3 и автоматическая двусторонняя печать"
    assert extract_attrs_from_name(name)["max_format"] == "A3"


def test_max_format_a3_wins_over_a4_when_both_present():
    # A3-устройство умеет и A4 — если упомянут A3, он перевешивает
    name = "МФУ формат печати А4 возможность сканирования в формате А3"
    assert extract_attrs_from_name(name)["max_format"] == "A3"


def test_duplex_yes():
    name = "МФУ Возможность автоматической двухсторонней печати Да"
    assert extract_attrs_from_name(name)["duplex"] == "yes"


def test_duplex_no():
    name = "Принтер Возможность автоматической двухсторонней печати Нет"
    assert extract_attrs_from_name(name)["duplex"] == "no"


def test_usb_detected():
    name = "Принтер Способ подключения USB"
    assert extract_attrs_from_name(name)["usb"] == "yes"


def test_print_technology_electrographic_extracted():
    """Электрография в требовании — отдельное значение «электрографическая».
    Эквивалентность с лазер/LED обрабатывается на уровне attribute_rules."""
    name = "МФУ Технология печати Электрографическая"
    assert extract_attrs_from_name(name)["print_technology"] == "электрографическая"


def test_print_technology_laser_extracted():
    name = "Принтер Технология печати Лазерная"
    assert extract_attrs_from_name(name)["print_technology"] == "лазерная"


def test_print_technology_led_extracted():
    name = "Принтер Технология печати Светодиодная"
    assert extract_attrs_from_name(name)["print_technology"] == "светодиодная"


def test_print_technology_inkjet():
    name = "Принтер Технология печати Струйная"
    assert extract_attrs_from_name(name)["print_technology"] == "струйная"


def test_print_speed_ge_pattern():
    name = "МФУ Скорость черно-белой печати ≥ 25 стр/мин"
    out = extract_attrs_from_name(name)
    assert out["print_speed_ppm"] == 25


def test_print_speed_does_not_grab_digit_from_a3():
    # Реальный кейс из лота 0373100031126000016: «А3» близко к слову «печати»
    # — старый regex возвращал 3. Должно вернуть 20.
    name = "МФУ Скорость цветной печати в формате А3, стр/мин ≥ 20"
    out = extract_attrs_from_name(name)
    assert out["print_speed_ppm"] == 20


def test_print_speed_ge_30_pattern():
    name = "Принтер Скорость печати ≥ 30 стр/мин"
    out = extract_attrs_from_name(name)
    assert out["print_speed_ppm"] == 30


def test_print_speed_ppm_after_format_letter():
    # «А4» рядом со «скорость 25 ppm»: «А4» не должно перехватываться как 4 ppm
    name = "Принтер А4, скорость печати 25 ppm"
    out = extract_attrs_from_name(name)
    assert out["print_speed_ppm"] == 25


def test_network_interface_wifi():
    name = "Принтер Способ подключения Wi-Fi"
    out = extract_attrs_from_name(name)
    assert out.get("network_interface") == "WiFi"


def test_combined_real_world_example():
    name = (
        "Многофункциональное устройство (МФУ) Цветность печати Черно-Белая "
        "Возможность автоматической двухсторонней печати Да "
        "Максимальный формат печати А4 Способ подключения USB"
    )
    out = extract_attrs_from_name(name)
    assert out["colorness"] == "ч/б"
    assert out["duplex"] == "yes"
    assert out["max_format"] == "A4"
    assert out["usb"] == "yes"


def test_merge_jsonb_takes_precedence():
    parsed = {"colorness": "ч/б", "duplex": "yes"}
    jsonb = {"colorness": "цветной"}
    merged = merge_required_attrs(jsonb, parsed)
    assert merged["colorness"] == "цветной"
    assert merged["duplex"] == "yes"


def test_no_pattern_returns_empty():
    name = "Просто какой-то непонятный текст без характеристик"
    assert extract_attrs_from_name(name) == {}
