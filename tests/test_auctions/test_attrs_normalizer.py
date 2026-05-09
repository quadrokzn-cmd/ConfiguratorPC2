"""Тесты нормализатора zakupki-строк → schema-keys.

Покрытие: 9 schema-атрибутов, специфичные кейсы из реальной выгрузки
(0373100056024000064 «Скорость ч/б A4 ≥ 30 стр/мин», «Цветность Черно-Белая»,
«Способ подключения USB+LAN» и т.п.) + защита от ловли цифры из «А4»."""
from __future__ import annotations

from app.services.auctions.ingest.attrs_normalizer import normalize_attrs


def test_print_speed_ppm_lower_bound():
    raw = {"Скорость черно-белой печати в формате А4 по ISO/IEC 24734, стр/мин": "≥ 30"}
    out, unknown = normalize_attrs(raw)
    assert out == {"print_speed_ppm": 30}
    assert unknown == []


def test_print_speed_ppm_does_not_grab_digit_from_a4_in_key():
    """В ключе «формат А4» не должно ловиться 4 — нижняя граница в значении 20."""
    raw = {"Скорость цветной печати в формате А4, стр/мин": "не менее 20"}
    out, _ = normalize_attrs(raw)
    assert out["print_speed_ppm"] == 20


def test_print_speed_ppm_does_not_grab_digit_from_a3_in_value():
    """Если значение случайно содержит «А3» как формат — игнорируем эту цифру."""
    raw = {"Скорость черно-белой печати": "А3, не менее 25"}
    out, _ = normalize_attrs(raw)
    assert out["print_speed_ppm"] == 25


def test_max_format_a3_wins_over_a4():
    raw = {
        "Максимальный формат печати": "А3",
        "Максимальный формат сканирования": "А4",
    }
    out, _ = normalize_attrs(raw)
    assert out["max_format"] == "A3"


def test_max_format_a4():
    raw = {"Максимальный формат печати": "А4"}
    out, _ = normalize_attrs(raw)
    assert out["max_format"] == "A4"


def test_colorness_chb_from_chernobelaya():
    raw = {"Цветность": "Черно-Белая"}
    out, _ = normalize_attrs(raw)
    assert out["colorness"] == "ч/б"


def test_colorness_color_from_polnocvetnaya():
    raw = {"Цветность печати": "Полноцветная"}
    out, _ = normalize_attrs(raw)
    assert out["colorness"] == "цветной"


def test_duplex_yes_from_da():
    raw = {"Наличие автоматической двусторонней печати": "Да"}
    out, _ = normalize_attrs(raw)
    assert out["duplex"] == "yes"


def test_duplex_no_from_net():
    raw = {"Двусторонняя печать": "Нет"}
    out, _ = normalize_attrs(raw)
    assert out["duplex"] == "no"


def test_print_technology_electrographic():
    raw = {"Технология печати": "Электрографическая"}
    out, _ = normalize_attrs(raw)
    assert out["print_technology"] == "электрографическая"


def test_print_technology_laser():
    raw = {"Технология печати": "Лазерная"}
    out, _ = normalize_attrs(raw)
    assert out["print_technology"] == "лазерная"


def test_connection_usb_and_lan_from_rowspan_list():
    """`_extract_position_attrs` для rowspan>1 кладёт значения списком."""
    raw = {"Способ подключения": ["USB", "LAN"]}
    out, _ = normalize_attrs(raw)
    assert out["usb"] == "yes"
    assert out["network_interface"] == "LAN"


def test_connection_only_wifi():
    raw = {"Способ подключения": "Wi-Fi"}
    out, _ = normalize_attrs(raw)
    assert out["usb"] == "no"
    assert out["network_interface"] == "WiFi"


def test_connection_lan_wins_over_wifi():
    raw = {"Способ подключения": ["LAN", "Wi-Fi"]}
    out, _ = normalize_attrs(raw)
    assert out["network_interface"] == "LAN"


def test_resolution_dpi_min_from_two_axes():
    """«по вертикали» и «по горизонтали» — две строки; берём минимум."""
    raw = {
        "Максимальное разрешение черно-белой печати по вертикали, dpi": "≥ 1200",
        "Максимальное разрешение черно-белой печати по горизонтали, dpi": "≥ 600",
    }
    out, _ = normalize_attrs(raw)
    assert out["resolution_dpi"] == 600


def test_starter_cartridge_pages_from_resource():
    raw = {"Ресурс стартового картриджа, страниц": "не менее 1500"}
    out, _ = normalize_attrs(raw)
    assert out["starter_cartridge_pages"] == 1500


def test_unknown_key_not_in_schema_and_logged():
    """Ключи вне schema не попадают в результат, идут в unknown_keys."""
    raw = {
        "Класс энергетической эффективности": "не ниже A",  # known non-schema
        "Какой-то неведомый параметр": "42",                # unknown
    }
    out, unknown = normalize_attrs(raw)
    assert out == {}
    assert "Какой-то неведомый параметр" in unknown
    assert "Класс энергетической эффективности" not in unknown


def test_known_non_schema_keys_silently_ignored():
    """«Объём ОЗУ», «Класс энергоэффективности», «Время первого отпечатка» —
    не в schema, но в whitelist'е известных не-schema → не логируются."""
    raw = {
        "Объем установленной оперативной памяти": "≥ 128",
        "Класс энергетической эффективности": "не ниже A",
        "Время выхода первого черно-белого отпечатка": "≤ 8",
        "Суммарная емкость лотков подачи бумаги для печати": "≥ 250",
    }
    out, unknown = normalize_attrs(raw)
    assert out == {}
    assert unknown == []


def test_empty_input_returns_empty():
    out, unknown = normalize_attrs({})
    assert out == {}
    assert unknown == []


def test_full_realistic_position_from_0373100056024000064():
    """Эталонный набор атрибутов МФУ из реальной выгрузки zakupki:
    A4 МФУ 30 стр/мин ч/б, дуплекс, электрографический, USB+LAN, 600 dpi."""
    raw = {
        "Наличие в комплекте поставки кабеля питания": "Да",
        "Количество оригинальных черных тонер-картриджей (включая стартовый), поставляемых с оборудованием": "≥ 2",
        "Максимальное разрешение черно-белой печати по вертикали, dpi": "≥ 600",
        "Максимальное разрешение черно-белой печати по горизонтали, dpi": "≥ 600",
        "Наличие в комплекте поставки оригинального стартового черного тонер-картриджа": "Да",
        "Суммарная емкость выходных лотков": "≥ 100",
        "Суммарная емкость лотков подачи бумаги для печати": "≥ 250",
        "Объем установленной оперативной памяти": "≥ 128",
        "Класс энергетической эффективности": "не ниже A",
        "Наличие автоматической двусторонней печати": "Да",
        "Скорость черно-белой печати в формате А4 по ISO/IEC 24734, стр/мин": "≥ 30",
        "Способ подключения": ["USB", "LAN"],
        "Технология печати": "Электрографическая",
        "Максимальный формат печати": "А4",
        "Цветность": "Черно-Белая",
        "Время выхода первого черно-белого отпечатка": "≤ 8",
    }
    out, unknown = normalize_attrs(raw)
    assert out == {
        "colorness": "ч/б",
        "max_format": "A4",
        "duplex": "yes",
        "print_technology": "электрографическая",
        "print_speed_ppm": 30,
        "resolution_dpi": 600,
        "usb": "yes",
        "network_interface": "LAN",
    }
    assert unknown == []


def test_robustness_empty_value_skipped():
    """Пустое значение не порождает ключа; нормализатор не падает."""
    raw = {"Цветность": "", "Технология печати": "  "}
    out, _ = normalize_attrs(raw)
    assert out == {}
