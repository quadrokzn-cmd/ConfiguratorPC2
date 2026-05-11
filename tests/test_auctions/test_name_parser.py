"""Тесты regex-парсера атрибутов SKU printer/mfu из printers_mfu.name."""
from __future__ import annotations

import pytest

from portal.services.auctions.catalog.enrichment.name_parser import parse_printer_attrs


# ---- Базовые ------------------------------------------------------------

def test_empty_input_returns_empty_dict():
    assert parse_printer_attrs(None) == {}
    assert parse_printer_attrs("") == {}


def test_no_attrs_recognised_returns_empty_dict():
    # Чистое имя без характеристик — ничего не вернётся
    assert parse_printer_attrs("Pantum P2500W") == {}


# ---- print_speed_ppm ----------------------------------------------------

def test_speed_ppm_simple():
    assert parse_printer_attrs("G&G P2022W, 22 ppm")["print_speed_ppm"] == 22


def test_speed_ppm_str_min_russian():
    assert parse_printer_attrs("Принтер 30 стр/мин")["print_speed_ppm"] == 30


def test_speed_ppm_pages_per_minute():
    assert parse_printer_attrs("Printer 40 pages per minute")["print_speed_ppm"] == 40


def test_speed_ppm_pages_min_short():
    assert parse_printer_attrs("Printer 25 pages/min")["print_speed_ppm"] == 25


def test_speed_ppm_str_dot_min():
    # «22 стр./мин.» — с точками
    assert parse_printer_attrs("МФУ G&G M2022 A4 22 стр./мин.")["print_speed_ppm"] == 22


def test_speed_ppm_does_not_grab_non_speed_number():
    # «Принтер на 100 страниц» — без «/мин» или «в минуту» → не speed
    out = parse_printer_attrs("Принтер на 100 страниц")
    assert "print_speed_ppm" not in out


def test_speed_ppm_does_not_grab_max_pmon():
    # «max 25000 p/mon» — это месячная нагрузка, не скорость. Парсер
    # должен взять явное «22 ppm», а не 25000.
    out = parse_printer_attrs(
        "G&G P2022W, 22 ppm (max 25000 p/mon), 1200x1200 dpi"
    )
    assert out["print_speed_ppm"] == 22


def test_speed_ppm_strazh_min_no_space():
    # «25стр/мин» — без пробела между числом и единицей
    out = parse_printer_attrs("Canon, A3, 25стр/мин (A4 ч/б)")
    assert out["print_speed_ppm"] == 25


def test_speed_ppm_high_value_ignored():
    # Над 199 ppm не существует — отбрасываем (защита от случайных совпадений)
    out = parse_printer_attrs("Какой-то странный 250 ppm")
    assert "print_speed_ppm" not in out


# ---- colorness ----------------------------------------------------------

def test_colorness_mono_laser_eng():
    out = parse_printer_attrs("G&G P2022W, Mono laser, A4")
    assert out["colorness"] == "ч/б"


def test_colorness_color_eng():
    out = parse_printer_attrs("HP Color LaserJet Pro M454dw")
    assert out["colorness"] == "цветной"


def test_colorness_cyrillic_chb():
    out = parse_printer_attrs("МФУ Sindoh ЧБ принтер/копир/сканер, А3")
    assert out["colorness"] == "ч/б"


def test_colorness_russian_color():
    out = parse_printer_attrs("Принтер Цветной лазерный А4")
    assert out["colorness"] == "цветной"


def test_colorness_monochrome_word():
    out = parse_printer_attrs("Brother monochrome printer")
    assert out["colorness"] == "ч/б"


def test_colorness_bw_short():
    # «BW» как отдельный токен
    out = parse_printer_attrs("Printer BW A4 30 ppm")
    assert out["colorness"] == "ч/б"


def test_colorness_chb_with_slash():
    out = parse_printer_attrs("Lazer А3 ч/б 35 ppm")
    assert out["colorness"] == "ч/б"


def test_colorness_mono_wins_over_color_when_both():
    # «mono laser, colour scanner» — mono выигрывает
    out = parse_printer_attrs("MFU mono laser, colour scanner, A4")
    assert out["colorness"] == "ч/б"


# ---- max_format ---------------------------------------------------------

def test_max_format_a4_latin():
    out = parse_printer_attrs("Printer A4 mono laser")
    assert out["max_format"] == "A4"


def test_max_format_a4_cyrillic():
    out = parse_printer_attrs("Принтер формат А4")
    assert out["max_format"] == "A4"


def test_max_format_a3_latin():
    out = parse_printer_attrs("Printer A3 colour")
    assert out["max_format"] == "A3"


def test_max_format_a3_cyrillic():
    out = parse_printer_attrs("МФУ формат А3 30 стр/мин")
    assert out["max_format"] == "A3"


def test_max_format_a3_wins_over_a4():
    # «25 стр/мин (A4 ч/б), 12 стр/мин (A3 ч/б)» — A3 побеждает
    out = parse_printer_attrs(
        "Canon imageRUNNER 2425i, A3, 25 стр/мин (A4), 12 стр/мин (A3)"
    )
    assert out["max_format"] == "A3"


def test_max_format_no_a35_collision():
    # «A35» в коде модели не должно дать A3
    out = parse_printer_attrs("Module A350 controller")
    assert "max_format" not in out


def test_max_format_no_a4plus_collision():
    out = parse_printer_attrs("Some product A4plus paper")
    assert "max_format" not in out


# ---- duplex -------------------------------------------------------------

def test_duplex_eng():
    out = parse_printer_attrs("Printer with duplex, USB, A4")
    assert out["duplex"] == "yes"


def test_duplex_russian_dvustoronnyaya():
    out = parse_printer_attrs("МФУ A4 двусторонняя печать USB")
    assert out["duplex"] == "yes"


def test_duplex_dupleks_word():
    out = parse_printer_attrs("Printer A4 дуплекс")
    assert out["duplex"] == "yes"


def test_duplex_absent():
    out = parse_printer_attrs("Printer A4 30 ppm")
    assert "duplex" not in out


# ---- resolution_dpi -----------------------------------------------------

def test_resolution_simple_dpi():
    out = parse_printer_attrs("Printer 1200 dpi A4")
    assert out["resolution_dpi"] == 1200


def test_resolution_x_format_picks_max_horizontal():
    out = parse_printer_attrs("Printer 1200x1200 dpi A4")
    assert out["resolution_dpi"] == 1200


def test_resolution_2400x600():
    out = parse_printer_attrs("Printer 2400x600 dpi A4")
    assert out["resolution_dpi"] == 2400


def test_resolution_cyrillic_x():
    # 1800х600 dpi (кир. х)
    out = parse_printer_attrs("МФУ Sindoh, 1800х600 dpi А3")
    assert out["resolution_dpi"] == 1800


def test_resolution_no_dpi_marker():
    out = parse_printer_attrs("Printer 1200 pages")
    assert "resolution_dpi" not in out


# ---- print_technology ---------------------------------------------------

def test_print_technology_laser_eng():
    out = parse_printer_attrs("HP Color LaserJet Pro M454dw")
    assert out["print_technology"] == "лазерная"


def test_print_technology_lazer_russian():
    out = parse_printer_attrs("МФУ лазерное Canon")
    assert out["print_technology"] == "лазерная"


def test_print_technology_inkjet():
    out = parse_printer_attrs("Epson L805 inkjet A4")
    assert out["print_technology"] == "струйная"


def test_print_technology_struynyj_russian():
    out = parse_printer_attrs("Принтер струйный Epson")
    assert out["print_technology"] == "струйная"


def test_print_technology_led():
    out = parse_printer_attrs("Printer OKI LED A4")
    assert out["print_technology"] == "светодиодная"


def test_print_technology_electrography_maps_to_laser():
    # По решению собственника №20: «электрографическая» ≡ «лазерная»
    out = parse_printer_attrs("Принтер электрографический A4")
    assert out["print_technology"] == "лазерная"


# ---- usb ----------------------------------------------------------------

def test_usb_present():
    out = parse_printer_attrs("Printer A4, USB, WiFi")
    assert out["usb"] == "yes"


def test_usb_absent():
    out = parse_printer_attrs("Printer A4 30 ppm")
    assert "usb" not in out


# ---- network_interface --------------------------------------------------

def test_network_lan_only():
    out = parse_printer_attrs("Printer A4 30 ppm, USB, Ethernet")
    assert out["network_interface"] == ["LAN"]


def test_network_wifi_only():
    out = parse_printer_attrs("Printer A4 30 ppm, USB, Wi-Fi")
    assert out["network_interface"] == ["WiFi"]


def test_network_lan_and_wifi():
    out = parse_printer_attrs("Printer A4 30 ppm, USB, RJ-45, Wi-Fi")
    assert sorted(out["network_interface"]) == ["LAN", "WiFi"]


def test_network_wifi_no_dash():
    out = parse_printer_attrs("Printer A4 USB WiFi")
    assert out["network_interface"] == ["WiFi"]


def test_network_lan_word_lan():
    out = parse_printer_attrs("Printer A4 USB LAN")
    assert out["network_interface"] == ["LAN"]


def test_network_absent():
    out = parse_printer_attrs("Printer A4 30 ppm")
    assert "network_interface" not in out


# ---- starter_cartridge_pages --------------------------------------------

def test_starter_pages_in_parens_russian():
    out = parse_printer_attrs(
        "G&G P2022W, A4, 22 ppm, USB, WiFi, старт. картридж (700 стр.)"
    )
    assert out["starter_cartridge_pages"] == 700


def test_starter_pages_no_starter_keyword_ignored():
    # «1500 страниц» без «стартов*»/«starter» — это ресурс полного картриджа,
    # для матчинга атрибут starter_cartridge_pages не должен заполняться
    out = parse_printer_attrs("Принтер ресурс картриджа 1500 страниц")
    assert "starter_cartridge_pages" not in out


def test_starter_pages_eng_starter_cartridge():
    out = parse_printer_attrs("Printer with starter cartridge 1500 pages")
    assert out["starter_cartridge_pages"] == 1500


# ---- Регистронезависимость ---------------------------------------------

def test_case_insensitivity_usb():
    out = parse_printer_attrs("Printer a4, usb, wi-fi, mono laser")
    assert out["usb"] == "yes"
    assert out["max_format"] == "A4"
    assert out["network_interface"] == ["WiFi"]
    assert out["colorness"] == "ч/б"


# ---- Реальные имена с pre-prod (полный сценарий) -----------------------

def test_real_gg_p2022w():
    """G&G P2022W — тот самый кейс из проблемы (22 ppm vs ≥30 в лоте)."""
    name = (
        "Принтер G&G P2022W, Printer, Mono laser, А4, 22 ppm "
        "(max 20000 p/mon), 1200x1200 dpi, paper tray 150 pages, "
        "USB, WiFi, старт. картридж (700 стр.) оригинальный картридж - GT202T (1600 стр.)"
    )
    out = parse_printer_attrs(name)
    assert out["print_speed_ppm"] == 22
    assert out["colorness"] == "ч/б"
    assert out["max_format"] == "A4"
    assert out["resolution_dpi"] == 1200
    assert out["print_technology"] == "лазерная"
    assert out["usb"] == "yes"
    assert out["network_interface"] == ["WiFi"]
    assert out["starter_cartridge_pages"] == 700


def test_real_canon_imagerunner_2425i():
    name = (
        "МФУ лазерное Canon imageRUNNER 2425i, МФУ, A3, ч/б, 25стр/мин (A4 ч/б), "
        "12стр/мин (A3 ч/б), 2 Гб, 600x600dpi, RADF, сетевой, Wi-Fi, USB."
    )
    out = parse_printer_attrs(name)
    assert out["print_speed_ppm"] == 25
    assert out["colorness"] == "ч/б"
    assert out["max_format"] == "A3"
    assert out["resolution_dpi"] == 600
    assert out["print_technology"] == "лазерная"
    assert out["usb"] == "yes"
    assert "WiFi" in out["network_interface"]


def test_real_bulat_p1024w():
    """Bulat P1024W — Claude Code дал только print_technology, остальное
    парсер должен догнать частично (colorness из «монохромный»)."""
    name = "Принтер лазерный монохромный/ BULAT P1024W"
    out = parse_printer_attrs(name)
    assert out["colorness"] == "ч/б"
    assert out["print_technology"] == "лазерная"


def test_real_brother_pocketjet():
    name = "Мобильный принтер Brother PocketJet PJ-722, 8 стр/мин, термопечать, 200т/д, USB"
    out = parse_printer_attrs(name)
    assert out["print_speed_ppm"] == 8
    assert out["usb"] == "yes"
    # термопечать не входит в наш enum print_technology — не возвращается
    assert "print_technology" not in out


def test_real_pantum_m7302fdn():
    # МФУ лазерное/ Pantum M7302FDN — лазерная
    out = parse_printer_attrs("МФУ лазерное/ Pantum M7302FDN")
    assert out["print_technology"] == "лазерная"


def test_real_canon_isensys_mf272dw():
    name = "Лазерное монохромное МФУ формата А4/ Canon I-SENSYS MF272DW"
    out = parse_printer_attrs(name)
    assert out["colorness"] == "ч/б"
    assert out["max_format"] == "A4"
    assert out["print_technology"] == "лазерная"


def test_real_hp_color_laserjet():
    out = parse_printer_attrs("Лазерный принтер/ HP Color LaserJet Pro M454dw")
    assert out["colorness"] == "цветной"
    assert out["print_technology"] == "лазерная"


def test_real_sindoh_n512_partial():
    """Длинное имя Sindoh N512 — должно вытащить много полей."""
    name = (
        "МФУ Sindoh N512 ЧБ принтер/копир/сканер, А3. 36 стр/мин Ч/Б печать/копирование, "
        "1800х600 dpi. Сканер до 45 стр/мин. Факс Super G3 (опция). 1000 Мб Ethernet, USB"
    )
    out = parse_printer_attrs(name)
    assert out["print_speed_ppm"] == 36
    assert out["colorness"] == "ч/б"
    assert out["max_format"] == "A3"
    assert out["resolution_dpi"] == 1800
    assert out["usb"] == "yes"
    assert out["network_interface"] == ["LAN"]
