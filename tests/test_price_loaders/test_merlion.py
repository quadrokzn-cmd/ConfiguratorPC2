# Парсер Merlion: чтение Excel, маппинг категорий, фильтры.

from __future__ import annotations

from decimal import Decimal

from app.services.price_loaders.merlion import MerlionLoader


def test_merlion_reads_headers_from_row_11(make_merlion_xlsx):
    """Заголовки именно в 11-й строке — проверим, что 12-я уже читается
    как данные (в первых 10 строках — служебный мусор)."""
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров",
            "g2": "Материнские Платы",
            "g3": "Socket-1700",
            "brand": "ASUS",
            "number": "M-001",
            "mpn": "PRIME H610M-E",
            "name": "ASUS PRIME H610M-E D4",
            "price_rub": 8500,
            "stock": 3,
            "transit_1": 0,
            "transit_2": 2,
        },
    ])
    rows = list(MerlionLoader().iter_rows(path))
    assert len(rows) == 1
    r = rows[0]
    assert r.supplier_sku == "M-001"
    assert r.mpn == "PRIME H610M-E"
    assert r.name == "ASUS PRIME H610M-E D4"
    assert r.brand == "ASUS"
    assert r.our_category == "motherboard"
    assert r.price == Decimal("8500")
    assert r.currency == "RUB"
    assert r.stock == 3
    assert r.transit == 2


def test_merlion_maps_all_required_categories(make_merlion_xlsx):
    """Каждая из основных групп (материнки/RAM/GPU/SSD/HDD/корпуса/БП/кулеры)
    корректно отображается на нашу категорию."""
    cases = [
        ("Комплектующие для компьютеров", "Материнские Платы", "Socket-AM5", "motherboard"),
        ("Комплектующие для компьютеров", "Память оперативная", "DDR5",       "ram"),
        ("Комплектующие для компьютеров", "Память оперативная", "SO-DIMM",    "ram"),
        ("Комплектующие для компьютеров", "Видеокарты", "PCI-E",              "gpu"),
        ("Оборудование для геймеров",     "Видеокарты", "Видеокарты",         "gpu"),
        ("Комплектующие для компьютеров", "Накопители SSD", "M.2",            "storage"),
        ("Комплектующие для компьютеров", "Накопители SSD", "2.5\"",          "storage"),
        ("Комплектующие для компьютеров", "Жесткие Диски", "SATA",            "storage"),
        ("Комплектующие для компьютеров", "Корпуса", "ATX",                   "case"),
        ("Оборудование для геймеров",     "Корпуса", "Корпуса",               "case"),
        ("Комплектующие для компьютеров", "Блоки питания", "Блоки питания",   "psu"),
        ("Комплектующие для компьютеров", "Устройства охлаждения", "Все кулеры", "cooler"),
        ("Комплектующие для компьютеров", "Устройства охлаждения", "Для INTEL","cooler"),
    ]
    rows_input = []
    for i, (g1, g2, g3, _) in enumerate(cases, start=1):
        rows_input.append({
            "g1": g1, "g2": g2, "g3": g3,
            "brand": "Brand",
            "number": f"M-{i:03d}",
            "mpn": f"MPN-{i:03d}",
            "name": f"Товар {i}",
            "price_rub": 1000 + i,
            "stock": 1,
        })

    path = make_merlion_xlsx(rows_input)
    rows = list(MerlionLoader().iter_rows(path))
    assert len(rows) == len(cases)
    for row, (_, _, _, expected_cat) in zip(rows, cases, strict=True):
        assert row.our_category == expected_cat


def test_merlion_skips_unknown_categories(make_merlion_xlsx):
    """Категория вне маппинга → our_category=None. Такие строки
    всё равно возвращаются (фильтрует orchestrator), чтобы было видно,
    что именно прайс содержит."""
    path = make_merlion_xlsx([
        {
            "g1": "Техника для дома", "g2": "Телевизоры", "g3": "OLED",
            "brand": "LG", "number": "TV-1", "mpn": "OLED77",
            "name": "Телевизор LG OLED 77", "price_rub": 250000, "stock": 1,
        },
        {
            "g1": "Комплектующие для компьютеров",
            "g2": "Блоки питания", "g3": "Блоки питания",
            "brand": "Corsair", "number": "PSU-1", "mpn": "RM750x",
            "name": "Corsair RM750x", "price_rub": 12000, "stock": 2,
        },
    ])
    rows = list(MerlionLoader().iter_rows(path))
    assert len(rows) == 2
    assert rows[0].our_category is None
    assert rows[1].our_category == "psu"


def test_merlion_prefers_rub_over_usd(make_merlion_xlsx):
    """Если есть и RUB (K), и USD (J) — берём RUB."""
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров",
            "g2": "Видеокарты", "g3": "PCI-E",
            "brand": "Palit", "number": "V-1", "mpn": "RTX4060-8",
            "name": "Palit RTX 4060 8GB",
            "price_usd": 300, "price_rub": 27000,
            "stock": 5,
        },
    ])
    [r] = list(MerlionLoader().iter_rows(path))
    assert r.currency == "RUB"
    assert r.price == Decimal("27000")


def test_merlion_falls_back_to_usd_if_no_rub(make_merlion_xlsx):
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров",
            "g2": "Видеокарты", "g3": "PCI-E",
            "brand": "Palit", "number": "V-2", "mpn": "RTX4060-NO-RUB",
            "name": "Palit RTX 4060 (только USD)",
            "price_usd": 300,
            "stock": 5,
        },
    ])
    [r] = list(MerlionLoader().iter_rows(path))
    assert r.currency == "USD"
    assert r.price == Decimal("300")


def test_merlion_raw_category_is_full_path(make_merlion_xlsx):
    """raw_category должна содержать исходный путь с разделителем «|»."""
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров",
            "g2": "Устройства охлаждения", "g3": "Все кулеры",
            "brand": "DeepCool", "number": "C-1", "mpn": "AK400",
            "name": "DeepCool AK400", "price_rub": 4500, "stock": 8,
        },
    ])
    [r] = list(MerlionLoader().iter_rows(path))
    assert r.raw_category == "Комплектующие для компьютеров | Устройства охлаждения | Все кулеры"


def test_merlion_no_gtin_field(make_merlion_xlsx):
    """В прайсе Merlion Москва нет GTIN — всегда None."""
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Корпуса", "g3": "ATX",
            "brand": "NZXT", "number": "K-1", "mpn": "H5-FLOW",
            "name": "NZXT H5 Flow", "price_rub": 7500, "stock": 4,
        },
    ])
    [r] = list(MerlionLoader().iter_rows(path))
    assert r.gtin is None


def test_merlion_detect_by_filename():
    assert MerlionLoader.detect("Прайслист_Мерлион_Москва.xlsm") is True
    assert MerlionLoader.detect("merlion_price.xlsx") is True
    assert MerlionLoader.detect("OCS_price.xlsx") is False


def test_merlion_sums_transit_columns(make_merlion_xlsx):
    """transit = (M «Ожидаемый приход») + (N «На складе поставщика»)."""
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Память оперативная", "g3": "DDR4",
            "brand": "Kingston", "number": "R-1", "mpn": "KF426C16",
            "name": "Kingston Fury 16GB DDR4",
            "price_rub": 3900, "stock": 0,
            "transit_1": 5, "transit_2": 7,
        },
    ])
    [r] = list(MerlionLoader().iter_rows(path))
    assert r.transit == 12


def test_merlion_requires_supplier_sku(make_merlion_xlsx):
    """Без «Номер» (колонка E) — строка пропускается."""
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Память оперативная", "g3": "DDR4",
            "brand": "Kingston", "number": "", "mpn": "KF426C16",
            "name": "Kingston без SKU", "price_rub": 3900, "stock": 0,
        },
    ])
    assert list(MerlionLoader().iter_rows(path)) == []
