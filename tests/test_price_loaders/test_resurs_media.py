# Парсер «Ресурс Медиа»: заголовки в строке 2, двухуровневые
# разделители категорий через колонки A (раздел) и B (подкатегория),
# в строках данных колонка B превращается в бренд, качественные
# маркеры остатка «Мало/Средне/Много/Нет».

from __future__ import annotations

from decimal import Decimal

from portal.services.configurator.price_loaders.resurs_media import ResursMediaLoader


def test_resurs_media_basic_data_row(make_resurs_media_xlsx):
    """Базовый сценарий: раздел + подкатегория + одна строка данных."""
    path = make_resurs_media_xlsx([
        {"section":    "Комплектующие и компоненты"},
        {"subsection": "Видеокарты"},
        {
            "brand":     "Palit",
            "article":   "RM-001",
            "mpn":       "RTX4060-OC",
            "name":      "Palit RTX 4060 8GB",
            "price_usd": 300,
            "price_rub": 27000,
            "stock":     5,
            "transit":   "Мало",
        },
    ])
    rows = list(ResursMediaLoader().iter_rows(path))
    assert len(rows) == 1
    r = rows[0]
    assert r.supplier_sku == "RM-001"
    assert r.mpn == "RTX4060-OC"
    assert r.brand == "Palit"
    assert r.name == "Palit RTX 4060 8GB"
    assert r.our_category == "gpu"
    assert r.raw_category == "Комплектующие и компоненты | Видеокарты"
    assert r.price == Decimal("27000")  # приоритет RUB
    assert r.currency == "RUB"
    assert r.stock == 5
    assert r.transit == 5             # «Мало» → 5
    assert r.gtin is None


def test_resurs_media_qualitative_stock_markers():
    """Маркеры Мало/Средне/Много/Нет переводятся в числа,
    иначе фильтр stock_qty>0 не пропустит ни одной позиции."""
    from portal.services.configurator.price_loaders.resurs_media import _parse_stock

    assert _parse_stock("Мало") == 5
    assert _parse_stock("МАЛО") == 5            # регистр не важен
    assert _parse_stock("Средне") == 20
    assert _parse_stock("Много") == 100
    assert _parse_stock("Нет") == 0
    assert _parse_stock("") == 0
    assert _parse_stock(None) == 0
    assert _parse_stock(7) == 7
    assert _parse_stock("12") == 12
    assert _parse_stock(" Много ") == 100       # пробелы вокруг
    assert _parse_stock("неизвестный маркер") == 0


def test_resurs_media_qualitative_stock_end_to_end(make_resurs_media_xlsx):
    """Сквозной тест: «Много» в колонке Доступно даёт stock=100."""
    path = make_resurs_media_xlsx([
        {"section":    "Комплектующие и компоненты"},
        {"subsection": "Корпуса"},
        {
            "brand": "Zalman", "article": "RM-009", "mpn": "T3",
            "name": "Zalman T3", "price_rub": 3500, "stock": "Много",
            "transit": "Средне",
        },
    ])
    [r] = list(ResursMediaLoader().iter_rows(path))
    assert r.stock == 100
    assert r.transit == 20


def test_resurs_media_section_changes_context_and_resets_subsection(make_resurs_media_xlsx):
    """Новый раздел сбрасывает текущую подкатегорию: до явной строки
    подкатегории контекст некорректен и not-applicable категория
    не должна «протекать» из предыдущего раздела."""
    path = make_resurs_media_xlsx([
        {"section":    "Комплектующие и компоненты"},
        {"subsection": "Процессоры"},
        {
            "brand": "AMD", "article": "RM-100", "mpn": "100-000001591",
            "name": "AMD Ryzen 5 7600", "price_rub": 19800, "stock": 3,
        },
        # Новый раздел — подкатегория должна сброситься.
        {"section":    "ИБП и АКБ"},
        {
            "brand": "APC", "article": "RM-200", "mpn": "BR1500MS",
            "name": "APC Back-UPS Pro 1500", "price_rub": 25000, "stock": 1,
        },
        # И только теперь явно — новая подкатегория, новый товар.
        {"subsection": "Источники бесперебойного питания"},
        {
            "brand": "APC", "article": "RM-201", "mpn": "BR900MI",
            "name": "APC Back-UPS Pro 900", "price_rub": 18000, "stock": 1,
        },
    ])
    rows = list(ResursMediaLoader().iter_rows(path))
    assert [r.our_category for r in rows] == ["cpu", None, None]
    # Первая позиция в новом разделе без подкатегории → raw_category
    # содержит только верхний раздел.
    assert rows[1].raw_category == "ИБП и АКБ"
    assert rows[2].raw_category == "ИБП и АКБ | Источники бесперебойного питания"


def test_resurs_media_maps_all_relevant_categories(make_resurs_media_xlsx):
    """Покрытие маппинга по всем восьми нашим категориям, которые
    у Ресурс Медиа реально встречаются."""
    cases = [
        (("Жёсткие диски и оптические носители", "Внутренние жёсткие диски"), "storage"),
        (("Комплектующие и компоненты", "SSD диски"),                         "storage"),
        (("Комплектующие и компоненты", "Видеокарты"),                        "gpu"),
        (("Комплектующие и компоненты", "Корпуса"),                           "case"),
        (("Комплектующие и компоненты", "Материнские платы"),                 "motherboard"),
        (("Комплектующие и компоненты", "Оперативная память"),                "ram"),
        (("Комплектующие и компоненты", "Процессоры"),                        "cpu"),
        (("Комплектующие и компоненты", "Устройства охлаждения"),             "cooler"),
    ]
    items = []
    expected = []
    for i, ((sec, sub), cat) in enumerate(cases):
        items.append({"section": sec})
        items.append({"subsection": sub})
        items.append({
            "brand": "Brand", "article": f"RM-T-{i}", "mpn": f"MPN-{i}",
            "name": f"Test {i}", "price_rub": 1000 + i, "stock": 1,
        })
        expected.append(cat)

    path = make_resurs_media_xlsx(items)
    rows = list(ResursMediaLoader().iter_rows(path))
    assert [r.our_category for r in rows] == expected


def test_resurs_media_falls_back_to_usd_if_no_rub(make_resurs_media_xlsx):
    path = make_resurs_media_xlsx([
        {"section":    "Комплектующие и компоненты"},
        {"subsection": "Процессоры"},
        {
            "brand": "Intel", "article": "RM-CPU", "mpn": "I7-13700",
            "name": "Intel i7-13700", "price_usd": 350, "stock": 2,
        },
    ])
    [r] = list(ResursMediaLoader().iter_rows(path))
    assert r.currency == "USD"
    assert r.price == Decimal("350")


def test_resurs_media_skips_row_without_article(make_resurs_media_xlsx):
    """Строка без артикула воспринимается как разделитель/мусор и не
    выдаётся в PriceRow."""
    path = make_resurs_media_xlsx([
        {"section":    "Комплектующие и компоненты"},
        {"subsection": "Процессоры"},
        {
            "brand": "Intel", "article": "", "mpn": "I9",
            "name": "Без артикула", "price_rub": 999, "stock": 1,
        },
        {
            "brand": "Intel", "article": "RM-OK", "mpn": "I7-13700",
            "name": "Нормальный", "price_rub": 25000, "stock": 1,
        },
    ])
    rows = list(ResursMediaLoader().iter_rows(path))
    assert len(rows) == 1
    assert rows[0].supplier_sku == "RM-OK"


def test_resurs_media_skips_row_without_price(make_resurs_media_xlsx):
    """Строка без цены пропускается (orchestrator не сможет писать
    supplier_prices без цены)."""
    path = make_resurs_media_xlsx([
        {"section":    "Комплектующие и компоненты"},
        {"subsection": "Процессоры"},
        {
            "brand": "Intel", "article": "RM-NOPRICE", "mpn": "I3",
            "name": "Без цены", "stock": 1,
        },
    ])
    rows = list(ResursMediaLoader().iter_rows(path))
    assert rows == []


def test_resurs_media_no_gtin_field(make_resurs_media_xlsx):
    """В прайсе Ресурс Медиа GTIN нет — всегда None."""
    path = make_resurs_media_xlsx([
        {"section":    "Комплектующие и компоненты"},
        {"subsection": "Корпуса"},
        {
            "brand": "NZXT", "article": "RM-K1", "mpn": "H5-FLOW",
            "name": "NZXT H5 Flow", "price_rub": 7500, "stock": 4,
        },
    ])
    [r] = list(ResursMediaLoader().iter_rows(path))
    assert r.gtin is None


def test_resurs_media_brand_taken_from_column_b_in_data_rows(make_resurs_media_xlsx):
    """В строках данных колонка B хранит бренд, а не подкатегорию.
    Подкатегория «прилипает» из последней увиденной строки-разделителя."""
    path = make_resurs_media_xlsx([
        {"section":    "Комплектующие и компоненты"},
        {"subsection": "Видеокарты"},
        {
            "brand":   "Palit",
            "article": "RM-A", "mpn": "RTX4060",
            "name": "Palit RTX 4060", "price_rub": 27000, "stock": 1,
        },
        {
            "brand":   "ASUS",
            "article": "RM-B", "mpn": "RTX4060-DUAL",
            "name": "ASUS Dual RTX 4060", "price_rub": 28000, "stock": 1,
        },
    ])
    rows = list(ResursMediaLoader().iter_rows(path))
    assert [r.brand for r in rows] == ["Palit", "ASUS"]
    assert all(r.our_category == "gpu" for r in rows)


def test_resurs_media_detect_by_filename():
    assert ResursMediaLoader.detect("price_struct.xlsx") is True
    assert ResursMediaLoader.detect("Ресурс Медиа.xlsx") is True
    assert ResursMediaLoader.detect("ресурс_медиа.xlsx") is True
    assert ResursMediaLoader.detect("resurs_media.xlsx") is True
    assert ResursMediaLoader.detect("OCS_price.xlsx") is False
