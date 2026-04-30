# Парсер «Green Place»: лист «Worksheet», заголовки в строке 1,
# категории в трёх колонках Группа 1/2/3, USD/RUB параллельно.

from __future__ import annotations

from decimal import Decimal

from app.services.price_loaders.green_place import GreenPlaceLoader


def test_green_place_basic_data_row(make_green_place_xlsx):
    """Базовый сценарий: одна строка данных, all поля заполнены."""
    path = make_green_place_xlsx([
        {
            "supplier_sku": "1151440",
            "name":  "Процессор AMD Ryzen 5 3400G AM4 (YD3400C5M4MFH)",
            "brand": "AMD",
            "mpn":   "YD3400C5M4MFH",
            "g1":    "Оборудование для геймеров",
            "g2":    "Процессоры",
            "g3":    "",
            "stock":     166,
            "transit":   200,
            "price_usd": 88.0,
            "price_rub": 6573.13,
        },
    ])
    rows = list(GreenPlaceLoader().iter_rows(path))
    assert len(rows) == 1
    r = rows[0]
    assert r.supplier_sku == "1151440"
    assert r.mpn == "YD3400C5M4MFH"
    assert r.brand == "AMD"
    assert r.our_category == "cpu"
    assert r.raw_category == "Оборудование для геймеров | Процессоры"
    assert r.price == Decimal("6573.13")  # приоритет RUB
    assert r.currency == "RUB"
    assert r.stock == 166
    assert r.transit == 200
    assert r.gtin is None


def test_green_place_maps_consumer_cpu_categories(make_green_place_xlsx):
    """В нашу cpu-категорию мапятся обе тройки потребительских CPU,
    которые встречаются в реальном Green-Place-прайсе."""
    path = make_green_place_xlsx([
        {
            "supplier_sku": "111",
            "name": "AMD Ryzen 5 7600", "brand": "AMD", "mpn": "100-100001015",
            "g1": "Комплектующие для компьютеров", "g2": "Процессоры", "g3": "Прочие",
            "stock": 5, "transit": 0, "price_rub": 18000,
        },
        {
            "supplier_sku": "222",
            "name": "AMD Ryzen 5 3400G", "brand": "AMD", "mpn": "YD3400C5M4MFH",
            "g1": "Оборудование для геймеров", "g2": "Процессоры", "g3": "",
            "stock": 1, "transit": 0, "price_rub": 6500,
        },
    ])
    rows = list(GreenPlaceLoader().iter_rows(path))
    assert [r.our_category for r in rows] == ["cpu", "cpu"]


def test_green_place_skips_server_and_unrelated_categories(make_green_place_xlsx):
    """Серверное железо, сетевое оборудование, СХД, ИБ-софт и т. п.
    остаются с our_category=None (orchestrator такие позиции пропустит)."""
    path = make_green_place_xlsx([
        {
            "supplier_sku": "10001",
            "name": "Серверный CPU Xeon", "brand": "INTEL", "mpn": "P4X-DPE1000",
            "g1": "Серверы и СХД", "g2": "Серверные опции", "g3": "Процессоры",
            "stock": 0, "transit": 0, "price_usd": 500,
        },
        {
            "supplier_sku": "10002",
            "name": "Коммутатор Ubiquiti", "brand": "UBIQUITI", "mpn": "US-8-60W-EU",
            "g1": "Сетевое оборудование", "g2": "Cетевое оборудование", "g3": "Коммутаторы",
            "stock": 0, "transit": 0, "price_usd": 159,
        },
        {
            "supplier_sku": "10003",
            "name": "Серверная DDR4", "brand": "LENOVO", "mpn": "4ZC7A08709",
            "g1": "Комплектующие для компьютеров", "g2": "Память оперативная", "g3": "Server Memory",
            "stock": 0, "transit": 0, "price_usd": 2999.99,
        },
    ])
    rows = list(GreenPlaceLoader().iter_rows(path))
    assert all(r.our_category is None for r in rows)
    assert len(rows) == 3


def test_green_place_falls_back_to_usd_if_no_rub(make_green_place_xlsx):
    path = make_green_place_xlsx([
        {
            "supplier_sku": "999", "name": "...", "brand": "AMD",
            "mpn": "100-X", "g1": "Оборудование для геймеров",
            "g2": "Процессоры", "g3": "",
            "stock": 1, "transit": 0, "price_usd": 250,
        },
    ])
    [r] = list(GreenPlaceLoader().iter_rows(path))
    assert r.currency == "USD"
    assert r.price == Decimal("250")


def test_green_place_skips_row_without_price(make_green_place_xlsx):
    """Цены 0/None в обеих валютах → строка пропущена (для большой
    части GP-прайса так и есть: ИБ-софт идёт с прайсом 0/0)."""
    path = make_green_place_xlsx([
        {
            "supplier_sku": "1003838",
            "name": "Компонент ПАК Код Безопасности iButton",
            "brand": "КОД БЕЗОПАСНОСТИ", "mpn": "DS-1996",
            "g1": "Программное обеспечение",
            "g2": "Информационная безопасность",
            "g3": "Компонент ПАК",
            "stock": 0, "transit": 0,
            "price_usd": 0.0, "price_rub": 0.0,
        },
    ])
    rows = list(GreenPlaceLoader().iter_rows(path))
    assert rows == []


def test_green_place_skips_row_without_supplier_sku(make_green_place_xlsx):
    """Без «Но» (внутреннего кода Green Place) строка не пишется —
    у orchestrator нет ключа для supplier_prices."""
    path = make_green_place_xlsx([
        {
            "supplier_sku": "",
            "name": "Без кода", "brand": "?", "mpn": "X",
            "g1": "Оборудование для геймеров", "g2": "Процессоры", "g3": "",
            "stock": 1, "transit": 0, "price_rub": 1000,
        },
    ])
    assert list(GreenPlaceLoader().iter_rows(path)) == []


def test_green_place_no_gtin_field(make_green_place_xlsx):
    """В прайсе GTIN нет — всегда None."""
    path = make_green_place_xlsx([
        {
            "supplier_sku": "1", "name": "X", "brand": "AMD", "mpn": "YD3400",
            "g1": "Оборудование для геймеров", "g2": "Процессоры", "g3": "",
            "stock": 1, "transit": 0, "price_rub": 6500,
        },
    ])
    [r] = list(GreenPlaceLoader().iter_rows(path))
    assert r.gtin is None


def test_green_place_handles_empty_g3(make_green_place_xlsx):
    """В тройке (Оборудование для геймеров, Процессоры, '') третья
    группа пуста — это допустимо, raw_category должна корректно
    собираться без хвостового разделителя."""
    path = make_green_place_xlsx([
        {
            "supplier_sku": "1", "name": "Test", "brand": "AMD", "mpn": "Z",
            "g1": "Оборудование для геймеров", "g2": "Процессоры", "g3": "",
            "stock": 1, "transit": 0, "price_rub": 1000,
        },
    ])
    [r] = list(GreenPlaceLoader().iter_rows(path))
    assert r.raw_category == "Оборудование для геймеров | Процессоры"


def test_green_place_skips_audited_non_consumer_groups(make_green_place_xlsx):
    """Этап 11.1.1: аудит реального прайса (44 уникальные тройки)
    показал, что кроме двух потребительских CPU-троек все остальные
    позиции — НЕ для конфигуратора ПК. Этот тест фиксирует решение
    по пяти крупнейшим неподходящим категориям, чтобы случайное
    расширение _CATEGORY_MAP их не зацепило.
    """
    path = make_green_place_xlsx([
        # 1) NONAME-комплектующие проектной сборки (винты/кабели/радиаторы) — 364
        {
            "supplier_sku": "10001", "name": "Винт N-S1-LS-001",
            "brand": "NONAME", "mpn": "N-S1-LS-001",
            "g1": "Комплектующие для компьютеров",
            "g2": "Прочее", "g3": "Комплектующие для проекта",
            "stock": 0, "transit": 0, "price_rub": 50,
        },
        # 2) Серверная DDR4 ECC Reg — 1
        {
            "supplier_sku": "10002", "name": "Память DDR4 Lenovo ECC Reg PC4-24300",
            "brand": "LENOVO", "mpn": "4ZC7A08709",
            "g1": "Комплектующие для компьютеров",
            "g2": "Память оперативная", "g3": "Server Memory",
            "stock": 0, "transit": 0, "price_rub": 224000,
        },
        # 3) Серверный CPU Xeon — 122
        {
            "supplier_sku": "10003", "name": "Серверный CPU Xeon 6258R",
            "brand": "INTEL", "mpn": "BX806956258R",
            "g1": "Серверы и СХД",
            "g2": "Серверные опции", "g3": "Процессоры",
            "stock": 0, "transit": 0, "price_rub": 350000,
        },
        # 4) Tesla A100 (датацентровый GPU, не consumer) — 5
        {
            "supplier_sku": "10004",
            "name": "Видеокарта Nvidia Tesla A100 80GB HBM2 PCIe",
            "brand": "NVIDIA", "mpn": "900-21001-0020-000",
            "g1": "Комплектующие для компьютеров",
            "g2": "Товар под заказ", "g3": "ТпЗ",
            "stock": 0, "transit": 0, "price_rub": 1500000,
        },
        # 5) Сетевое оборудование — коммутатор Ubiquiti — 84
        {
            "supplier_sku": "10005", "name": "Коммутатор Ubiquiti US-8-60W-EU",
            "brand": "UBIQUITI", "mpn": "US-8-60W-EU",
            "g1": "Сетевое оборудование",
            "g2": "Cетевое оборудование", "g3": "Коммутаторы",
            "stock": 0, "transit": 0, "price_rub": 11876,
        },
    ])
    rows = list(GreenPlaceLoader().iter_rows(path))
    assert len(rows) == 5
    # Все пять — за пределами нашего сегмента; orchestrator их пропустит.
    assert [r.our_category for r in rows] == [None, None, None, None, None]


def test_green_place_normalizes_numeric_supplier_sku(make_green_place_xlsx):
    """В реальных прайсах supplier_sku приходит как float из Excel
    (1003014.0). Чтобы повторная загрузка не плодила дубликаты,
    целочисленные float сворачиваются к int-строке."""
    from app.services.price_loaders.green_place import _normalize
    assert _normalize(1003014.0) == "1003014"
    assert _normalize(42) == "42"
    assert _normalize("ABC-1") == "ABC-1"
    assert _normalize(None) == ""
    # Float с дробной частью НЕ сворачиваем, иначе потеряем точность.
    assert _normalize(1003014.5) == "1003014.5"


def test_green_place_detect_by_filename():
    assert GreenPlaceLoader.detect("Price_GP_TC0075104_30.04.2026.xlsx") is True
    assert GreenPlaceLoader.detect("price_gp_data.xlsx") is True
    assert GreenPlaceLoader.detect("green_place_price.xlsx") is True
    assert GreenPlaceLoader.detect("greenplace.xlsx") is True
    assert GreenPlaceLoader.detect("OCS_price.xlsx") is False
    assert GreenPlaceLoader.detect("merlion.xlsx") is False
