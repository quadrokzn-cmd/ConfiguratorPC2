# Парсер Treolan: чтение каталога, категории в строках-разделителях,
# Intel CPU (S-Spec), GTIN.

from __future__ import annotations

from decimal import Decimal

from portal.services.configurator.price_loaders.treolan import TreolanLoader


def test_treolan_reads_headers_from_row_3(make_treolan_xlsx):
    """Заголовки в строке 3; данные — с 4-й."""
    path = make_treolan_xlsx([
        {"category": "Комплектующие->Процессоры"},
        {
            "article": "100-000001591",
            "name":    "AMD Ryzen 5 7600 6-core",
            "brand":   "AMD",
            "stock":   7, "transit_1": 2, "transit_2": 0,
            "price_usd": 220, "price_rub": 19800,
            "gtin": "0730143314572",
        },
    ])
    rows = list(TreolanLoader().iter_rows(path))
    assert len(rows) == 1
    r = rows[0]
    assert r.supplier_sku == "100-000001591"
    assert r.mpn == "100-000001591"
    assert r.brand == "AMD"
    assert r.name == "AMD Ryzen 5 7600 6-core"
    assert r.our_category == "cpu"
    assert r.price == Decimal("19800")
    assert r.currency == "RUB"
    assert r.stock == 7
    assert r.transit == 2
    assert r.gtin == "0730143314572"


def test_treolan_intel_cpu_s_spec_is_kept_as_mpn(make_treolan_xlsx):
    """Intel CPU: артикул SRMBG (5-символьный S-Spec). У нас он
    становится и supplier_sku, и mpn. GTIN — единственный путь к
    OCS-компоненту, должен корректно считываться."""
    path = make_treolan_xlsx([
        {"category": "Комплектующие->Процессоры"},
        {
            "article": "SRMBG",
            "name":    "Intel Core i5-13400F 10-core",
            "brand":   "Intel",
            "stock":   3, "transit_1": 0, "transit_2": 0,
            "price_usd": 210, "price_rub": 18900,
            "gtin": "5032037260466",
        },
        {
            "article": "SRH3P",
            "name":    "Intel Core i3-10105 4-core",
            "brand":   "Intel",
            "stock":   5, "transit_1": 0, "transit_2": 0,
            "price_rub": 8400,
            "gtin": "5032037195836",
        },
        {
            "article": "SRH3C",
            "name":    "Intel Core i5-10400 6-core",
            "brand":   "Intel",
            "stock":   2, "transit_1": 0, "transit_2": 0,
            "price_rub": 11500,
            "gtin": "5032037195829",
        },
    ])
    rows = list(TreolanLoader().iter_rows(path))
    assert [r.mpn for r in rows] == ["SRMBG", "SRH3P", "SRH3C"]
    assert all(r.supplier_sku == r.mpn for r in rows)
    assert all(r.our_category == "cpu" for r in rows)
    assert [r.gtin for r in rows] == ["5032037260466", "5032037195836", "5032037195829"]


def test_treolan_category_separator_changes_context(make_treolan_xlsx):
    """Разделители категорий сменяют контекст для следующих товаров.
    Товар между двумя разделителями получает свою категорию."""
    path = make_treolan_xlsx([
        {"category": "Комплектующие->Процессоры"},
        {
            "article": "YD3200C5M4MFH", "name": "AMD Ryzen 3 3200", "brand": "AMD",
            "stock": 1, "price_rub": 6000,
        },
        {"category": "Комплектующие->Видеокарты->Видеокарты на чипсетах NVIDIA"},
        {
            "article": "PA-RTX4060-8", "name": "Palit RTX 4060 8GB", "brand": "Palit",
            "stock": 4, "price_rub": 27500, "gtin": "4710636273898",
        },
        {"category": "Комплектующие->БП для корпусов"},
        {
            "article": "CP-9020200", "name": "Corsair RM750x", "brand": "Corsair",
            "stock": 2, "price_rub": 12500,
        },
    ])
    rows = list(TreolanLoader().iter_rows(path))
    assert [r.our_category for r in rows] == ["cpu", "gpu", "psu"]
    # И raw_category корректная.
    assert rows[1].raw_category == "Комплектующие->Видеокарты->Видеокарты на чипсетах NVIDIA"


def test_treolan_skips_unknown_categories(make_treolan_xlsx):
    path = make_treolan_xlsx([
        {"category": "Периферия->Мыши"},
        {"article": "LOGI-M1", "name": "Мышь офисная", "brand": "Logitech", "stock": 10, "price_rub": 500},
        {"category": "Комплектующие->Корпуса"},
        {"article": "ZALMAN-1", "name": "Zalman T3", "brand": "Zalman", "stock": 3, "price_rub": 3500},
    ])
    rows = list(TreolanLoader().iter_rows(path))
    assert [r.our_category for r in rows] == [None, "case"]


def test_treolan_prefers_rub_over_usd(make_treolan_xlsx):
    path = make_treolan_xlsx([
        {"category": "Комплектующие->Процессоры"},
        {
            "article": "R7-7700", "name": "AMD Ryzen 7 7700", "brand": "AMD",
            "stock": 1, "price_usd": 300, "price_rub": 27000,
        },
    ])
    [r] = list(TreolanLoader().iter_rows(path))
    assert r.currency == "RUB"
    assert r.price == Decimal("27000")


def test_treolan_transit_sum_from_e_and_f(make_treolan_xlsx):
    path = make_treolan_xlsx([
        {"category": "Комплектующие->Процессоры"},
        {
            "article": "X-100", "name": "Test CPU", "brand": "Test",
            "stock": 0, "transit_1": 3, "transit_2": 4, "price_rub": 1000,
        },
    ])
    [r] = list(TreolanLoader().iter_rows(path))
    assert r.transit == 7


def test_treolan_no_article_is_skipped(make_treolan_xlsx):
    path = make_treolan_xlsx([
        {"category": "Комплектующие->Процессоры"},
        {"article": "", "name": "Товар без артикула", "brand": "X", "stock": 0, "price_rub": 100},
        {"article": "OK-1", "name": "Нормальный", "brand": "X", "stock": 0, "price_rub": 200},
    ])
    rows = list(TreolanLoader().iter_rows(path))
    assert len(rows) == 1
    assert rows[0].supplier_sku == "OK-1"


def test_treolan_detect_by_filename():
    assert TreolanLoader.detect("23_04_2026_catalog__1_.xlsx") is True
    assert TreolanLoader.detect("Treolan_price.xlsx") is True
    assert TreolanLoader.detect("OCS_price.xlsx") is False


def test_treolan_parses_qualitative_stock_markers():
    """Колонки «Склад»/«Транзит» у Treolan часто содержат не числа,
    а маркеры «<10», «много» и пр. Без перевода в числа конфигуратор
    (фильтр stock_qty > 0) их не увидит."""
    from portal.services.configurator.price_loaders.treolan import _parse_stock

    assert _parse_stock("<10") == 5
    assert _parse_stock("много") == 50
    assert _parse_stock("МНОГО") == 50      # регистр не важен
    assert _parse_stock(" < 10 ") == 5      # пробелы вокруг и внутри
    assert _parse_stock(">10") == 20
    assert _parse_stock(">100") == 100
    assert _parse_stock("") == 0
    assert _parse_stock(None) == 0
    # Числа — как раньше
    assert _parse_stock(5) == 5
    assert _parse_stock("12") == 12
    assert _parse_stock("неизвестный маркер") == 0


def test_treolan_qualitative_stock_end_to_end(make_treolan_xlsx):
    """Сквозной тест: «много» в колонке «Склад» даёт stock=50."""
    path = make_treolan_xlsx([
        {"category": "Комплектующие->Корпуса"},
        {
            "article": "ZLM-QUAL", "name": "Zalman T3", "brand": "Zalman",
            "stock": "много",
            "transit_1": "<10",
            "transit_2": 0,
            "price_rub": 3500,
        },
    ])
    [r] = list(TreolanLoader().iter_rows(path))
    assert r.stock == 50
    # transit = 5 («<10») + 0 = 5
    assert r.transit == 5
