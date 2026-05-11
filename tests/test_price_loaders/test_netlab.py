# Парсер Netlab: чтение DealerD-прайса, разделители-категории
# в одной колонке, повторяющиеся заголовки внутри листа,
# бинарные маркеры остатка «+»/«-», тариф D как USD-цена,
# fallback на РРЦ(Руб.), .zip-обёртка над .xlsx.

from __future__ import annotations

import os
import zipfile
from decimal import Decimal

from portal.services.configurator.price_loaders.netlab import NetlabLoader


def test_netlab_reads_headers_from_row_21(make_netlab_xlsx):
    """Заголовки в строке 21; данные начинаются с 22-й. Колонка A
    («Бусиново») — маркер остатка «+»/«-», колонка C — PartNumber/MPN,
    D — Артикул/supplier_sku, E — Наименование, H — цена тарифа D в USD."""
    path = make_netlab_xlsx([
        {"category": "Видеокарты ASUS"},
        {
            "stock": "+",
            "partnumber": "RTX4060-O8G-GAMING",
            "article": "11199001",
            "name": "ASUS RTX 4060 OC 8GB",
            "price_d": 305.50,
        },
    ])
    rows = list(NetlabLoader().iter_rows(path))
    assert len(rows) == 1
    r = rows[0]
    assert r.supplier_sku == "11199001"
    assert r.mpn == "RTX4060-O8G-GAMING"
    assert r.name == "ASUS RTX 4060 OC 8GB"
    assert r.our_category == "gpu"
    assert r.raw_category == "Видеокарты ASUS"
    assert r.price == Decimal("305.50")
    assert r.currency == "USD"
    assert r.stock == 5     # «+» → 5
    assert r.transit == 0
    assert r.gtin is None


def test_netlab_binary_stock_markers():
    """«+» → 5, «-» → 0. Других маркеров в реальном Netlab-прайсе нет."""
    from portal.services.configurator.price_loaders.netlab import _parse_stock

    assert _parse_stock("+") == 5
    assert _parse_stock("-") == 0
    assert _parse_stock("") == 0
    assert _parse_stock(None) == 0
    # Числа — как раньше (на случай, если когда-нибудь появятся).
    assert _parse_stock(7) == 7
    assert _parse_stock("12") == 12
    # Незнакомый маркер — 0 (а не исключение).
    assert _parse_stock("много") == 0


def test_netlab_skips_repeated_inner_headers(make_netlab_xlsx):
    """Внутри листа Netlab перед каждой брендовой подсекцией повторно
    вставляется строка заголовков «PartNumber|Артикул|...». Парсер
    обязан её распознать и пропустить, не считая за товар."""
    path = make_netlab_xlsx([
        {"category": "SSD Kingston", "repeat_header": True},
        {
            "stock": "+",
            "partnumber": "SA400S37/240G",
            "article": "11199500",
            "name": "Kingston A400 240GB",
            "price_d": 19.90,
        },
        {"category": "SSD Samsung", "repeat_header": True},
        {
            "stock": "-",
            "partnumber": "MZ-77E1T0BW",
            "article": "11199501",
            "name": "Samsung 870 EVO 1TB",
            "price_d": 89.00,
        },
    ])
    rows = list(NetlabLoader().iter_rows(path))
    assert [r.supplier_sku for r in rows] == ["11199500", "11199501"]
    assert all(r.our_category == "storage" for r in rows)
    # Маркеры остатка корректно интерпретированы.
    assert rows[0].stock == 5
    assert rows[1].stock == 0


def test_netlab_category_keyword_mapping(make_netlab_xlsx):
    """Маппинг категорий идёт по ключевым словам в строке-разделителе.
    Должен корректно ловить варианты «Видеокарты ASUS», «SSD Kingston»,
    «Корпуса AEROCOOL» и т. п."""
    cases = [
        ("Процессоры",                    "cpu"),
        ("Материнские платы ASUS (для INTEL)", "motherboard"),
        ("Видеокарты Palit",              "gpu"),
        ("Память DDR4",                   "ram"),
        ("SSD Kingston",                  "storage"),
        ("Корпуса AEROCOOL",              "case"),
        ("Блоки питания к корпусам",      "psu"),
        ("Охлаждающие системы CBR",       "cooler"),
        ("Вентиляторы и Охлаждающие системы PCCooler", "cooler"),
    ]
    items = []
    for i, (sep, _) in enumerate(cases):
        items.append({"category": sep})
        items.append({
            "stock": "+",
            "partnumber": f"PN-{i}",
            "article":    f"A-{i}",
            "name":       f"Тестовый товар {i}",
            "price_d":    100.0 + i,
        })
    path = make_netlab_xlsx(items)
    rows = list(NetlabLoader().iter_rows(path))
    assert len(rows) == len(cases)
    for r, (_, expected_cat) in zip(rows, cases, strict=True):
        assert r.our_category == expected_cat, f"{r.raw_category!r} → {r.our_category!r}"


def test_netlab_skips_server_external_and_irrelevant(make_netlab_xlsx):
    """Серверные/внешние позиции и периферийные «корпусные» вещи
    не должны мапиться в наши категории."""
    cases = [
        ("Серверные материнские платы ASUS",  None),
        ("HPE Память",                         None),  # серверная
        ("Память серверная DDR5",              None),
        ("HPE SSD",                            None),  # серверный SSD
        ("Внешние HDD/SSD",                    None),
        ("Внешние контейнеры для HDD",         None),
        ("Корпуса под моноблоки PowerCool",    None),
        ("Охлаждающие подставки для ноутбуков", None),
        ("Корпусные пылевые фильтры",          None),
        ("Блок питания/Электрика Tantos",      None),
    ]
    items = []
    for i, (sep, _) in enumerate(cases):
        items.append({"category": sep})
        items.append({
            "stock": "+",
            "partnumber": f"P-{i}", "article": f"A-{i}",
            "name": f"Товар {i}", "price_d": 50.0 + i,
        })
    path = make_netlab_xlsx(items)
    rows = list(NetlabLoader().iter_rows(path))
    # Все должны быть без нашей категории.
    assert all(r.our_category is None for r in rows)
    assert len(rows) == len(cases)


def test_netlab_falls_back_to_rrc_rub_when_d_missing(make_netlab_xlsx):
    """Если D-USD пуст, но РРЦ(Руб.) заполнена — используем рублёвую
    цену как запасной вариант."""
    path = make_netlab_xlsx([
        {"category": "Корпуса CHIEFTEC"},
        {
            "stock": "+",
            "partnumber": "BX-01B-OP", "article": "11199900",
            "name": "Chieftec BX-01B",
            "price_d": None,
            "price_rrc": 4500.00,
        },
    ])
    [r] = list(NetlabLoader().iter_rows(path))
    assert r.currency == "RUB"
    assert r.price == Decimal("4500.00")


def test_netlab_skips_row_without_any_price(make_netlab_xlsx):
    """И D, и РРЦ пусты → строка пропущена (orchestrator не сможет
    записать supplier_prices без цены)."""
    path = make_netlab_xlsx([
        {"category": "Корпуса CHIEFTEC"},
        {
            "stock": "+",
            "partnumber": "BX-NO-PRICE", "article": "11199901",
            "name": "Test no price",
        },
    ])
    rows = list(NetlabLoader().iter_rows(path))
    assert rows == []


def test_netlab_skips_row_without_article(make_netlab_xlsx):
    """Без Артикула (Netlab-SKU) строка не имеет смысла (orchestrator
    не сможет идемпотентно писать в supplier_prices)."""
    path = make_netlab_xlsx([
        {"category": "SSD Kingston"},
        {
            "stock": "+",
            "partnumber": "SA400S37/240G", "article": "",
            "name": "Без артикула", "price_d": 19.9,
        },
        {
            "stock": "+",
            "partnumber": "SA400S37/480G", "article": "11199502",
            "name": "Нормальный", "price_d": 35.0,
        },
    ])
    rows = list(NetlabLoader().iter_rows(path))
    assert len(rows) == 1
    assert rows[0].supplier_sku == "11199502"


def test_netlab_no_gtin_field(make_netlab_xlsx):
    """В прайсе Netlab GTIN нет — всегда None в PriceRow."""
    path = make_netlab_xlsx([
        {"category": "Процессоры"},
        {
            "stock": "+",
            "partnumber": "100-100000910WOF", "article": "11199700",
            "name": "AMD Ryzen 5 7600", "price_d": 220.0,
        },
    ])
    [r] = list(NetlabLoader().iter_rows(path))
    assert r.gtin is None


def test_netlab_detect_by_filename():
    assert NetlabLoader.detect("DealerD.xlsx") is True
    assert NetlabLoader.detect("dealerd.zip") is True
    assert NetlabLoader.detect("netlab_price.xlsx") is True
    assert NetlabLoader.detect("OCS_price.xlsx") is False
    assert NetlabLoader.detect("23_04_2026_catalog__1_.xlsx") is False


def test_netlab_unknown_category_yields_no_our_category(make_netlab_xlsx):
    """Если строка-разделитель не похожа ни на одну нашу категорию,
    товар возвращается с our_category=None — orchestrator его пропустит."""
    path = make_netlab_xlsx([
        {"category": "Планшеты APPLE"},
        {
            "stock": "+",
            "partnumber": "MK473RK/A", "article": "11051003",
            "name": "Apple iPad", "price_d": 546.0,
        },
    ])
    [r] = list(NetlabLoader().iter_rows(path))
    assert r.our_category is None
    assert r.raw_category == "Планшеты APPLE"


def test_netlab_normalizes_numeric_article_from_excel(make_netlab_xlsx):
    """В реальном DealerD.xlsx Артикул (col D) — числовой, openpyxl
    отдаёт его как float (11051003.0). Без нормализации повторная
    загрузка плодит дубликаты supplier_sku. Целочисленные float
    сворачиваются к int-строке."""
    from portal.services.configurator.price_loaders.netlab import _normalize
    assert _normalize(11051003.0) == "11051003"
    assert _normalize(0.0) == "0"
    assert _normalize(0.5) == "0.5"
    assert _normalize("ABC") == "ABC"
    assert _normalize(None) == ""

    # Сквозной тест: Excel-моки часто хранят числа как float —
    # поверяем, что в PriceRow.supplier_sku приходит чистая строка.
    path = make_netlab_xlsx([
        {"category": "Видеокарты ASUS"},
        {
            "stock": "+", "partnumber": "RTX4060-O8G",
            "article": 11199001.0,
            "name": "ASUS RTX 4060", "price_d": 305.0,
        },
    ])
    [r] = list(NetlabLoader().iter_rows(path))
    assert r.supplier_sku == "11199001"


def test_netlab_reads_real_dealerd_dimension_quirk(make_netlab_xlsx):
    """В реальном DealerD.xlsx внутренний XML-элемент <dimension>
    повреждён и сообщает «A1:A1». В режиме read_only openpyxl
    возвращает 0 строк. Парсер форсирует reset_dimensions(),
    чтобы прочитать весь лист.

    Воспроизводим квирк программно: создаём файл и затем напрямую
    переписываем sheet1.xml на dimension="A1:A1".
    """
    import zipfile
    import re
    import tempfile, os

    path = make_netlab_xlsx([
        {"category": "Видеокарты ASUS"},
        {
            "stock": "+", "partnumber": "RTX-1", "article": "A-1",
            "name": "Test 1", "price_d": 100,
        },
        {
            "stock": "-", "partnumber": "RTX-2", "article": "A-2",
            "name": "Test 2", "price_d": 200,
        },
    ], name="DealerD_quirk.xlsx")

    # Перезаписываем dimension во встроенном sheet1.xml.
    fd, broken = tempfile.mkstemp(suffix=".xlsx")
    os.close(fd)
    try:
        with zipfile.ZipFile(path, "r") as zin, zipfile.ZipFile(broken, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.namelist():
                data = zin.read(item)
                if item.endswith("sheet1.xml"):
                    text = data.decode("utf-8")
                    text = re.sub(r'<dimension ref="[^"]+"', '<dimension ref="A1:A1"', text)
                    data = text.encode("utf-8")
                zout.writestr(item, data)

        rows = list(NetlabLoader().iter_rows(broken))
        assert len(rows) == 2
        assert [r.supplier_sku for r in rows] == ["A-1", "A-2"]
    finally:
        os.remove(broken)


def test_netlab_reads_zip_with_inner_xlsx(make_netlab_xlsx, tmp_path):
    """Реальный Netlab отдаётся в виде .zip с одним .xlsx внутри —
    парсер должен прозрачно его открыть."""
    xlsx_path = make_netlab_xlsx([
        {"category": "Видеокарты ASUS"},
        {
            "stock": "+",
            "partnumber": "RTX4060-O8G", "article": "11199010",
            "name": "ASUS RTX 4060", "price_d": 305.0,
        },
    ], name="DealerD.xlsx")

    zip_path = tmp_path / "dealerd.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(xlsx_path, arcname="DealerD.xlsx")

    rows = list(NetlabLoader().iter_rows(str(zip_path)))
    assert len(rows) == 1
    assert rows[0].supplier_sku == "11199010"
    assert rows[0].our_category == "gpu"
    # Файл XLSX, который мы создали — на месте; временный каталог
    # для распакованного архива должен убраться сам.
    assert os.path.exists(xlsx_path)
