# OCS-адаптер после рефакторинга: парсинг правильный, GTIN читается
# из колонки EAN128. Запись в БД проверяется в test_orchestrator.py.

from __future__ import annotations

from decimal import Decimal

from portal.services.configurator.price_loaders.ocs import OcsLoader


def test_ocs_parses_basic_row(make_ocs_xlsx):
    path = make_ocs_xlsx([
        {
            "cat_a": "Комплектующие", "cat_b": "Процессоры", "kind_c": "",
            "maker": "AMD",
            "supplier_sku": "1000001001",
            "mpn": "100-000001591",
            "name": "AMD Ryzen 5 7600",
            "price": 220, "currency": "USD",
            "stock": 5, "transit": 2,
            "ean": "0730143314572",
        },
    ])
    [r] = list(OcsLoader().iter_rows(path))
    assert r.supplier_sku == "1000001001"
    assert r.mpn == "100-000001591"
    assert r.our_category == "cpu"
    assert r.brand == "AMD"
    assert r.price == Decimal("220")
    assert r.currency == "USD"
    assert r.stock == 5
    assert r.transit == 2
    assert r.gtin == "0730143314572"


def test_ocs_ean_column_detected_by_header(make_ocs_xlsx):
    """EAN128 ищется по имени колонки, а не по индексу."""
    path = make_ocs_xlsx([
        {
            "cat_a": "Комплектующие", "cat_b": "Видеокарты", "kind_c": "",
            "maker": "Palit", "supplier_sku": "1000002002",
            "mpn": "RTX4060-8", "name": "Palit RTX 4060 8GB",
            "price": 300, "currency": "USD", "stock": 2,
            "ean": "4710636273898",
        },
    ])
    [r] = list(OcsLoader().iter_rows(path))
    assert r.gtin == "4710636273898"


def test_ocs_without_ean_column_is_ok(make_ocs_xlsx):
    """Старые версии прайса без EAN128 — не падаем, просто gtin=None."""
    path = make_ocs_xlsx(
        [
            {
                "cat_a": "Комплектующие", "cat_b": "Корпуса", "kind_c": "",
                "maker": "Zalman", "supplier_sku": "1000003003",
                "mpn": "T3-PLUS", "name": "Zalman T3 Plus",
                "price": 3500, "currency": "RUB", "stock": 1,
            },
        ],
        with_ean=False,
    )
    [r] = list(OcsLoader().iter_rows(path))
    assert r.gtin is None
    assert r.our_category == "case"


def test_ocs_skips_rows_without_mpn(make_ocs_xlsx):
    path = make_ocs_xlsx([
        {
            "cat_a": "Комплектующие", "cat_b": "Корпуса", "kind_c": "",
            "maker": "X", "supplier_sku": "1000003003",
            "mpn": "", "name": "Без MPN",
            "price": 100, "currency": "RUB", "stock": 1,
        },
        {
            "cat_a": "Комплектующие", "cat_b": "Корпуса", "kind_c": "",
            "maker": "Y", "supplier_sku": "1000003004",
            "mpn": "OK-1", "name": "Нормальный",
            "price": 200, "currency": "RUB", "stock": 1,
        },
    ])
    rows = list(OcsLoader().iter_rows(path))
    assert len(rows) == 1
    assert rows[0].mpn == "OK-1"


def test_ocs_unknown_category_returns_none_but_not_skipped(make_ocs_xlsx):
    path = make_ocs_xlsx([
        {
            "cat_a": "Мебель", "cat_b": "Столы", "kind_c": "",
            "maker": "IKEA", "supplier_sku": "1000004004",
            "mpn": "DESK-1", "name": "Стол",
            "price": 9900, "currency": "RUB", "stock": 1,
        },
    ])
    [r] = list(OcsLoader().iter_rows(path))
    assert r.our_category is None
