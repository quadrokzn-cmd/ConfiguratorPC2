# Тесты TreolanFetcher (этап 12.3).
#
# Все HTTP-вызовы через httpx подменяем FakeClient'ом — никакой сети.
# БД-логика проверяется на реальной test-БД через orchestrator.

from __future__ import annotations

import base64
import json
import time
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text


# ---- helpers ----------------------------------------------------------

def _make_jwt(exp_ts: int) -> str:
    """Минимальный JWT (header.payload.signature) с настоящим exp."""
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    payload = json.dumps({"exp": int(exp_ts)}).encode()
    payload_b64 = base64.urlsafe_b64encode(payload).rstrip(b"=").decode()
    return f"{header}.{payload_b64}.fake-signature"


class FakeResponse:
    def __init__(self, status_code: int, text: str = "", json_data: Any | None = None):
        self.status_code = status_code
        self.text = text
        self._json = json_data

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class FakeClient:
    """Мок httpx.Client с заранее заскриптованными ответами.

    Передаём список (url_substring, response) либо callable handler(url, **kwargs).
    """
    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def post(self, url, json=None, params=None, headers=None):
        return self._handler(
            url=url, json=json, params=params, headers=headers,
        )


def _patch_httpx(monkeypatch, handler):
    """Подменяет httpx.Client в модуле fetcher'а на FakeClient."""
    import portal.services.configurator.auto_price.fetchers.treolan as treolan_mod

    def _factory(timeout=None):
        return FakeClient(handler)

    monkeypatch.setattr(treolan_mod.httpx, "Client", _factory)


# ---- 1. _get_token: основной endpoint ---------------------------------

def test_get_token_success_token_endpoint(treolan_env, monkeypatch):
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    token_str = _make_jwt(int(time.time()) + 4 * 3600)
    calls = []

    def handler(url, json, params, headers):
        calls.append(url)
        if url.endswith("/v1/auth/token"):
            return FakeResponse(200, text=token_str)
        return FakeResponse(404, text="not found")

    _patch_httpx(monkeypatch, handler)

    fetcher = TreolanFetcher()
    result = fetcher._get_token()

    assert result == token_str
    assert len(calls) == 1
    assert calls[0].endswith("/v1/auth/token")


# ---- 2. fallback на /v1/auth/login ------------------------------------

def test_get_token_fallback_to_login_endpoint(treolan_env, monkeypatch):
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    token_str = _make_jwt(int(time.time()) + 4 * 3600)
    calls = []

    def handler(url, json, params, headers):
        calls.append((url, params))
        if url.endswith("/v1/auth/token"):
            return FakeResponse(404, text="not found")
        if url.endswith("/v1/auth/login"):
            assert params == {"login": "test_login", "password": "test_password"}
            return FakeResponse(200, text=token_str)
        return FakeResponse(500, text="??")

    _patch_httpx(monkeypatch, handler)

    fetcher = TreolanFetcher()
    result = fetcher._get_token()

    assert result == token_str
    # Сначала /token (404), потом /login (200) — два вызова.
    assert len(calls) == 2
    assert calls[0][0].endswith("/v1/auth/token")
    assert calls[1][0].endswith("/v1/auth/login")


# ---- 3. кеш токена внутри TTL ----------------------------------------

def test_get_token_caches_within_ttl(treolan_env, monkeypatch):
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    token_str = _make_jwt(int(time.time()) + 4 * 3600)  # 4 часа > 1ч буфер
    call_count = {"n": 0}

    def handler(url, json, params, headers):
        call_count["n"] += 1
        return FakeResponse(200, text=token_str)

    _patch_httpx(monkeypatch, handler)

    fetcher = TreolanFetcher()
    t1 = fetcher._get_token()
    t2 = fetcher._get_token()
    t3 = TreolanFetcher()._get_token()  # другой экземпляр — общий кеш модуля

    assert t1 == t2 == t3 == token_str
    # HTTP-вызов был только в первый раз.
    assert call_count["n"] == 1


# ---- 4. 401 в _fetch_catalog → сброс кеша + повтор --------------------

def test_get_token_refreshes_after_401_in_catalog(treolan_env, monkeypatch):
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    token1 = _make_jwt(int(time.time()) + 4 * 3600)
    token2 = _make_jwt(int(time.time()) + 5 * 3600)

    state = {"auth_calls": 0, "catalog_calls": 0}

    def handler(url, json, params, headers):
        if url.endswith("/v1/auth/token"):
            state["auth_calls"] += 1
            return FakeResponse(200, text=token1 if state["auth_calls"] == 1 else token2)
        if url.endswith("/v1/Catalog/Get"):
            state["catalog_calls"] += 1
            if state["catalog_calls"] == 1:
                # Первый раз отвечаем 401 — токен «протух».
                return FakeResponse(401, text="token expired")
            return FakeResponse(200, json_data={"categories": [], "positions": []})
        return FakeResponse(404, text="???")

    _patch_httpx(monkeypatch, handler)

    fetcher = TreolanFetcher()
    token = fetcher._get_token()
    data = fetcher._fetch_catalog(token)

    assert state["auth_calls"] == 2  # первый токен + повтор после 401
    assert state["catalog_calls"] == 2
    assert data == {"categories": [], "positions": []}


# ---- 5. _fetch_catalog → положительный кейс ---------------------------
#
# 12.3-fix: production-формат — иерархия categories→children/products,
# а не плоский positions[]. Sample отражает реальный формат: товары
# в category.products (включая вложенные children).

_TREOLAN_API_SAMPLE = {
    "categories": [
        {
            "id": 1, "name": "Комплектующие", "products": [],
            "children": [
                {
                    "id": 100, "name": "Процессоры",
                    "products": [
                        {
                            "articul":      "BX8071512400F",
                            "rusName":      "Процессор Intel Core i5-12400F BOX",
                            "vendor":       "Intel",
                            "currentPrice": "180.50",
                            "price":        "200.00",
                            "currency":     "USD",
                            "atStock":      "12",
                            "atTransit":    "0",
                            "gtin":         "5032037240306",
                        },
                    ],
                    "children": [],
                },
                {
                    "id": 200, "name": "Корпуса",
                    "products": [
                        {
                            "articul":      "PCASE-001",
                            "rusName":      "Корпус DeepCool MATREXX 55",
                            "vendor":       "DeepCool",
                            "currentPrice": "5500",
                            "price":        "5500",
                            "currency":     "RUB",
                            "atStock":      "3",
                            "atTransit":    "5",
                            "gtin":         "",
                        },
                    ],
                    "children": [],
                },
            ],
        },
    ],
}


def test_fetch_catalog_returns_positions(treolan_env, monkeypatch):
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    token_str = _make_jwt(int(time.time()) + 4 * 3600)

    def handler(url, json, params, headers):
        if url.endswith("/v1/auth/token"):
            return FakeResponse(200, text=token_str)
        if url.endswith("/v1/Catalog/Get"):
            assert headers["Authorization"] == f"Bearer {token_str}"
            assert json["category"] == ""
            assert json["freeNom"] is True
            return FakeResponse(200, json_data=_TREOLAN_API_SAMPLE)
        return FakeResponse(404, text="???")

    _patch_httpx(monkeypatch, handler)

    fetcher = TreolanFetcher()
    token = fetcher._get_token()
    data = fetcher._fetch_catalog(token)

    assert "categories" in data
    assert len(data["categories"]) == 1
    # Товары лежат в category.children[].products — DFS-обходом найдём.
    from portal.services.configurator.auto_price.fetchers.treolan import _walk_products
    walked = list(_walk_products(data["categories"]))
    assert len(walked) == 2
    sku_set = {p["articul"] for _path, _cat_id, p in walked}
    assert sku_set == {"BX8071512400F", "PCASE-001"}


# ---- 6. _save: реальный INSERT в price_uploads/supplier_prices --------

def test_save_inserts_price_upload_and_rows(treolan_env, monkeypatch, db_session):
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    # Курс USD/RUB на сегодня — нужен для USD-позиции.
    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source) "
        "VALUES (CURRENT_DATE, 100.00, 'cbr')"
    ))
    db_session.commit()

    fetcher = TreolanFetcher()
    upload_id = fetcher._save(_TREOLAN_API_SAMPLE)

    assert isinstance(upload_id, int) and upload_id > 0

    # Запись в price_uploads — статус success/partial.
    row = db_session.execute(text(
        "SELECT pu.id, pu.status, pu.filename, s.name AS supplier "
        "FROM price_uploads pu JOIN suppliers s ON s.id=pu.supplier_id "
        "WHERE pu.id = :id"
    ), {"id": upload_id}).first()
    assert row is not None
    assert row.supplier == "Treolan"
    assert row.filename.startswith("auto_treolan_api_")
    assert row.status in ("success", "partial")

    # supplier_prices получили обе позиции (CPU и Case).
    prices = db_session.execute(text(
        "SELECT supplier_sku, price, currency, stock_qty "
        "FROM supplier_prices ORDER BY supplier_sku"
    )).all()
    skus = [r.supplier_sku for r in prices]
    assert "BX8071512400F" in skus
    assert "PCASE-001" in skus

    # USD-позиция конвертирована в RUB по курсу 100 → 180.50 USD = 18050 RUB.
    cpu_row = next(r for r in prices if r.supplier_sku == "BX8071512400F")
    assert cpu_row.currency == "RUB"
    assert Decimal(cpu_row.price) == Decimal("18050.00")


# ---- 7. _save: пропуск unknown-currency -------------------------------

def test_save_skips_unknown_currency(treolan_env, monkeypatch, db_session):
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source) "
        "VALUES (CURRENT_DATE, 100.00, 'cbr')"
    ))
    db_session.commit()

    sample = {
        "categories": [{
            "id": 100, "name": "Процессоры",
            "products": [
                {
                    "articul": "EUR-CPU", "rusName": "Test CPU", "vendor": "X",
                    "currentPrice": "100", "currency": "EUR", "atStock": "1",
                },
                {
                    "articul": "RUB-CPU", "rusName": "Test CPU2", "vendor": "X",
                    "currentPrice": "1000", "currency": "RUB", "atStock": "2",
                },
            ],
            "children": [],
        }],
    }

    fetcher = TreolanFetcher()
    upload_id = fetcher._save(sample)

    prices = db_session.execute(text(
        "SELECT supplier_sku FROM supplier_prices"
    )).all()
    skus = [r.supplier_sku for r in prices]
    assert "RUB-CPU" in skus
    assert "EUR-CPU" not in skus, "EUR-позиция должна быть пропущена"


# ---- 8. _save: USD → RUB через cb_rate --------------------------------

def test_save_converts_usd_to_rub_via_cb_rate(treolan_env, monkeypatch, db_session):
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    # Курс USD/RUB = 87.5
    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source) "
        "VALUES (CURRENT_DATE, 87.50, 'cbr')"
    ))
    db_session.commit()

    sample = {
        "categories": [{
            "id": 100, "name": "Процессоры",
            "products": [
                {
                    "articul": "USD-CPU-1",
                    "rusName": "Intel CPU 200 USD",
                    "vendor": "Intel",
                    "currentPrice": "200.00",
                    "currency": "USD",
                    "atStock": "5",
                },
            ],
            "children": [],
        }],
    }

    fetcher = TreolanFetcher()
    fetcher._save(sample)

    row = db_session.execute(text(
        "SELECT price, currency FROM supplier_prices WHERE supplier_sku = 'USD-CPU-1'"
    )).first()
    assert row is not None
    assert row.currency == "RUB"
    # 200 * 87.5 = 17500.00
    assert Decimal(row.price) == Decimal("17500.00")


# ---- 9. RuntimeError, если креды не заданы ----------------------------

def test_init_raises_without_credentials(monkeypatch):
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    monkeypatch.delenv("TREOLAN_API_LOGIN", raising=False)
    monkeypatch.delenv("TREOLAN_API_PASSWORD", raising=False)

    with pytest.raises(RuntimeError) as ei:
        TreolanFetcher()
    msg = str(ei.value)
    assert "TREOLAN_API_LOGIN" in msg
    assert "TREOLAN_API_PASSWORD" in msg


# =====================================================================
# 12.3-fix: тесты обхода дерева categories→children/products
# =====================================================================

def test_walk_products_recursive_collects_from_all_levels():
    """DFS должен поднимать товары и из узла-корня, и из вложенных
    children на любой глубине, и сохранять path-имён по дороге."""
    from portal.services.configurator.auto_price.fetchers.treolan import _walk_products

    tree = [
        {
            "name": "L0-A",
            "products": [{"articul": "A1"}],
            "children": [
                {
                    "name": "L1-A",
                    "products": [{"articul": "A2"}],
                    "children": [
                        {
                            "name": "L2-A",
                            "products": [{"articul": "A3"}, {"articul": "A4"}],
                            "children": [],
                        },
                    ],
                },
            ],
        },
        {
            "name": "L0-B",
            "products": [],
            "children": [
                {"name": "L1-B", "products": [{"articul": "B1"}], "children": []},
            ],
        },
    ]

    walked = list(_walk_products(tree))
    skus = [p["articul"] for _path, _cat_id, p in walked]
    assert sorted(skus) == ["A1", "A2", "A3", "A4", "B1"]

    # Пути сохраняют последовательность от корня к листу.
    sku_to_path = {p["articul"]: path for path, _cat_id, p in walked}
    assert sku_to_path["A1"] == ["L0-A"]
    assert sku_to_path["A2"] == ["L0-A", "L1-A"]
    assert sku_to_path["A3"] == ["L0-A", "L1-A", "L2-A"]
    assert sku_to_path["B1"] == ["L0-B", "L1-B"]


def test_walk_products_empty_tree_yields_nothing():
    from portal.services.configurator.auto_price.fetchers.treolan import _walk_products

    assert list(_walk_products([])) == []
    assert list(_walk_products(None)) == []
    # Дерево из пустых нод — тоже ничего.
    tree = [{"name": "X", "products": [], "children": []}]
    assert list(_walk_products(tree)) == []


def test_save_raises_runtimeerror_on_empty_categories(treolan_env):
    """Defensive layer 1: если data['categories'] пустой — RuntimeError,
    pipeline закроется failed и disappeared НЕ запустится."""
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    fetcher = TreolanFetcher()
    with pytest.raises(RuntimeError, match="categories"):
        fetcher._save({"categories": []})
    with pytest.raises(RuntimeError, match="categories"):
        fetcher._save({})


def test_save_raises_runtimeerror_on_zero_products_after_walk(treolan_env):
    """Defensive layer 2: categories есть, но после DFS ни одного товара
    не нашлось → RuntimeError. Закрывает случай 'структура изменилась
    второй раз и products куда-то переехали'."""
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    sample = {
        "categories": [
            {"name": "A", "products": [], "children": [
                {"name": "A1", "products": [], "children": []},
            ]},
            {"name": "B", "products": [], "children": []},
        ],
    }
    fetcher = TreolanFetcher()
    with pytest.raises(RuntimeError, match="не найдено ни одного товара"):
        fetcher._save(sample)


def test_save_handles_atstock_string_with_less_than_marker(
    treolan_env, monkeypatch, db_session,
):
    """В production atStock приходит строкой '<10' — не должно превращаться
    в stock=0 (иначе товары мгновенно станут «нет в наличии»)."""
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source) "
        "VALUES (CURRENT_DATE, 100.00, 'cbr')"
    ))
    db_session.commit()

    sample = {
        "categories": [{
            "id": 100, "name": "Процессоры",
            "products": [{
                "articul": "FUZZY-STOCK",
                "rusName": "Test CPU fuzzy stock",
                "vendor": "X",
                "currentPrice": "100",
                "currency": "RUB",
                "atStock": "<10",
                "atTransit": "<5",
            }],
            "children": [],
        }],
    }

    TreolanFetcher()._save(sample)

    row = db_session.execute(text(
        "SELECT stock_qty, transit_qty FROM supplier_prices "
        "WHERE supplier_sku = 'FUZZY-STOCK'"
    )).first()
    assert row is not None
    assert int(row.stock_qty) >= 1
    assert int(row.transit_qty) >= 1


def test_detect_our_category_matches_any_level_in_path():
    """_detect_our_category должен ловить наш ключ на любом уровне
    переданного path, не только на последнем."""
    from portal.services.configurator.auto_price.fetchers.treolan import _detect_our_category

    # Совпадение на среднем уровне (популярная схема Treolan).
    assert _detect_our_category(
        ["Комплектующие", "Процессоры", "Intel Core i5"]
    ) == "cpu"
    # Совпадение на корне.
    assert _detect_our_category(["Видеокарты", "GeForce RTX 4060"]) == "gpu"
    # Нет совпадений — None.
    assert _detect_our_category(["Серверы", "1-процессорные", "DELL"]) is None
    # Обратная совместимость: одна строка.
    assert _detect_our_category("Корпус ATX") == "case"
    assert _detect_our_category(None) is None
    assert _detect_our_category([]) is None


# =====================================================================
# 12.3-fix-2: качественные значения склада + порядок ключей маппинга
# =====================================================================

def test_parse_stock_str_qualitative_values_via_shared_table():
    """_parse_stock_str должен прогонять значение через общую таблицу
    TREOLAN_QUAL_STOCK (ту же что у XLSX-loader). До этого «много»
    падало в _to_int → InvalidOperation → 0, и ~700 позиций каждой
    автозагрузки терялись со stock=0."""
    from portal.services.configurator.auto_price.fetchers.treolan import _parse_stock_str
    from portal.services.configurator.price_loaders._qual_stock import TREOLAN_QUAL_STOCK

    # Качественные значения берутся ровно из shared таблицы.
    assert _parse_stock_str("много") == TREOLAN_QUAL_STOCK["много"]
    assert _parse_stock_str("МНОГО") == TREOLAN_QUAL_STOCK["много"]  # регистр
    assert _parse_stock_str(" много ") == TREOLAN_QUAL_STOCK["много"]
    assert _parse_stock_str("<10") == TREOLAN_QUAL_STOCK["<10"]
    assert _parse_stock_str("< 10") == TREOLAN_QUAL_STOCK["<10"]  # пробелы внутри
    assert _parse_stock_str(">10") == TREOLAN_QUAL_STOCK[">10"]
    assert _parse_stock_str(">100") == TREOLAN_QUAL_STOCK[">100"]

    # Старая логика — НЕ должна сломаться.
    assert _parse_stock_str("нет") == 0
    assert _parse_stock_str("0") == 0
    assert _parse_stock_str("") == 0
    assert _parse_stock_str(None) == 0
    assert _parse_stock_str("12") == 12
    # «<5» нет в shared таблице → fallback к startswith("<") → 1 (товар есть).
    assert _parse_stock_str("<5") == 1
    # «>50» нет в таблице → fallback к startswith(">") → 51.
    assert _parse_stock_str(">50") == 51
    # Случайная строка — graceful fallback к 0.
    assert _parse_stock_str("fjadkfj") == 0


def test_detect_our_category_psu_branch_takes_priority_over_corpus():
    """Путь «… → БП для корпусов» содержит подстроку «корпус», поэтому
    при неправильном порядке ключей _CATEGORY_NAME_MAP первый match
    падает в 'case' и весь PSU-сегмент Treolan (~210 позиций) теряется
    как 'не наша категория'. Тест — стопор для регрессии порядка."""
    from portal.services.configurator.auto_price.fetchers.treolan import _detect_our_category

    # Главный кейс — БП для корпусов в проде.
    assert _detect_our_category(
        ["Комплектующие", "БП для корпусов"]
    ) == "psu"
    assert _detect_our_category(
        ["Комплектующие", "БП для корпусов", "Corsair RM"]
    ) == "psu"
    # «Блок питания» как отдельная подстрока тоже должен быть psu.
    assert _detect_our_category(
        ["Комплектующие", "Блок питания ATX"]
    ) == "psu"
    # «Корпуса» сами по себе — по-прежнему 'case'.
    assert _detect_our_category(
        ["Комплектующие", "Корпуса"]
    ) == "case"
    # Также страховой кейс: keyword-приоритет ВНУТРИ одного name'а.
    # «БП для корпусов» содержит И «бп для», И «корпус» — порядок
    # ключей в _CATEGORY_NAME_MAP должен дать «psu».
    assert _detect_our_category("БП для корпусов") == "psu"


# =====================================================================
# 12.5c: ID-маппинг категорий (поглощает audit blocklist)
# =====================================================================

def _all_categories_tree():
    """Синтетическое дерево с ветками всех 8 наших our_category + одной
    серверной (под blocklist). Используется в тестах category_map."""
    return [
        {
            "id": 1, "name": "Комплектующие", "products": [], "productsQty": 0,
            "children": [
                {"id": 100, "name": "Процессоры", "products": [], "productsQty": 0, "children": []},
                {"id": 110, "name": "Материнские платы", "products": [], "productsQty": 0, "children": []},
                {"id": 120, "name": "Оперативная память DDR5", "products": [], "productsQty": 0, "children": []},
                {"id": 130, "name": "Видеокарты", "products": [], "productsQty": 0, "children": []},
                {"id": 140, "name": "SSD-накопители", "products": [], "productsQty": 0, "children": []},
                {"id": 141, "name": "Жесткие диски HDD", "products": [], "productsQty": 0, "children": []},
                {"id": 150, "name": "Блок питания ATX", "products": [], "productsQty": 0, "children": []},
                {"id": 151, "name": "БП для корпусов", "products": [], "productsQty": 0, "children": []},
                {"id": 160, "name": "Корпуса MidiTower", "products": [], "productsQty": 0, "children": []},
                {"id": 170, "name": "Охлаждение CPU", "products": [], "productsQty": 0, "children": []},
            ],
        },
        {
            "id": 2, "name": "Серверы", "products": [], "productsQty": 0,
            "children": [
                {
                    "id": 210, "name": "1-процессорные серверы",
                    "products": [],
                    "productsQty": 50,  # есть товары — должно бы попасть в cpu по substring,
                                        # но blocklist режет «сервер» в path → None
                    "children": [],
                },
            ],
        },
    ]


def test_build_category_map_from_tree(treolan_env):
    """Map покрывает все 8 our_category по корневым веткам, серверная
    ветка (через blocklist) → None."""
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    fetcher = TreolanFetcher()
    cat_map = fetcher._build_category_map(_all_categories_tree())

    assert cat_map[100] == "cpu"
    assert cat_map[110] == "motherboard"
    assert cat_map[120] == "ram"
    assert cat_map[130] == "gpu"
    assert cat_map[140] == "storage"
    assert cat_map[141] == "storage"
    assert cat_map[150] == "psu"
    assert cat_map[151] == "psu"  # «БП для корпусов» — приоритет psu над case
    assert cat_map[160] == "case"
    assert cat_map[170] == "cooler"
    # Корни без ключевых слов и серверная ветка → None
    assert cat_map[1] is None        # «Комплектующие» — root
    assert cat_map[2] is None        # «Серверы» — blocklist
    assert cat_map[210] is None      # «1-процессорные серверы» — blocklist по path


def test_position_classification_uses_category_id_lookup(treolan_env, db_session):
    """Позиция в категории с известным id попадает в supplier_prices с
    нашей our_category из map (через _save → orchestrator)."""
    from sqlalchemy import text
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source) "
        "VALUES (CURRENT_DATE, 100.00, 'cbr')"
    ))
    db_session.commit()

    sample = {
        "categories": [{
            "id": 100, "name": "Процессоры", "productsQty": 1,
            "products": [{
                "articul": "MAP-CPU-1", "rusName": "Test CPU via map",
                "vendor": "Intel", "currentPrice": "5000",
                "currency": "RUB", "atStock": "10",
            }],
            "children": [],
        }],
    }

    fetcher = TreolanFetcher()
    fetcher._save(sample)

    # Map должен быть построен и содержать id=100 → cpu.
    assert fetcher._category_map.get(100) == "cpu"

    # Проверяем, что позиция реально попала в supplier_prices с правильной
    # маппинг-нашей-категорией: orchestrator пишет наш our_category в
    # supplier_prices через mapping. Достаточно проверить присутствие SKU.
    row = db_session.execute(text(
        "SELECT supplier_sku FROM supplier_prices WHERE supplier_sku = 'MAP-CPU-1'"
    )).first()
    assert row is not None


def test_position_with_unknown_category_id_falls_back_to_path(
    treolan_env, db_session, monkeypatch,
):
    """Если позиция отнесена к категории, которой нет в category_map
    (например, у узла нет int id), должен сработать fallback на
    substring-классификацию по path — позиция всё равно попадёт в БД
    с корректной our_category."""
    from sqlalchemy import text
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher
    import portal.services.configurator.auto_price.fetchers.treolan as treolan_mod

    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source) "
        "VALUES (CURRENT_DATE, 100.00, 'cbr')"
    ))
    db_session.commit()

    # Узел с id=None — _walk_products даст cat_id=None, lookup промахнётся,
    # должен сработать fallback.
    sample = {
        "categories": [{
            "id": "no-int-id", "name": "Видеокарты", "productsQty": 1,
            "products": [{
                "articul": "FB-GPU-1", "rusName": "Test GPU fallback",
                "vendor": "NVIDIA", "currentPrice": "30000",
                "currency": "RUB", "atStock": "1",
            }],
            "children": [],
        }],
    }

    # Считаем фактические вызовы _detect_our_category — должен быть
    # вызван и из _build_category_map, и из fallback при обработке позиции.
    detect_calls: list[Any] = []
    real_detect = treolan_mod._detect_our_category

    def _spy(path):
        detect_calls.append(path)
        return real_detect(path)

    monkeypatch.setattr(treolan_mod, "_detect_our_category", _spy)

    fetcher = TreolanFetcher()
    fetcher._save(sample)

    # Позиция всё-таки попала в БД (значит fallback отработал).
    row = db_session.execute(text(
        "SELECT supplier_sku FROM supplier_prices WHERE supplier_sku = 'FB-GPU-1'"
    )).first()
    assert row is not None

    # _detect_our_category вызывался МИНИМУМ дважды: один раз в _build_category_map
    # для узла «Видеокарты», и один раз в fallback при обработке позиции.
    assert len(detect_calls) >= 2


def test_build_category_map_handles_recursive_depth(treolan_env):
    """Дерево 3+ уровня: каждая вложенная категория получает свой
    our_category по полному path."""
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    tree = [{
        "id": 1, "name": "Комплектующие", "productsQty": 0, "products": [],
        "children": [{
            "id": 10, "name": "Накопители", "productsQty": 0, "products": [],
            "children": [{
                "id": 100, "name": "SSD", "productsQty": 0, "products": [],
                "children": [{
                    "id": 1000, "name": "M.2 NVMe", "productsQty": 5, "products": [],
                    "children": [{
                        "id": 10000, "name": "Samsung 990 PRO", "productsQty": 3,
                        "products": [], "children": [],
                    }],
                }],
            }],
        }],
    }]

    fetcher = TreolanFetcher()
    cat_map = fetcher._build_category_map(tree)

    # На каждом уровне, где встречается substring "ssd", запись попадает в storage.
    assert cat_map[1] is None              # «Комплектующие» — без match
    assert cat_map[10] is None             # «Накопители» — без match (нет в keyword-map)
    assert cat_map[100] == "storage"       # «SSD»
    assert cat_map[1000] == "storage"      # path содержит «SSD»
    assert cat_map[10000] == "storage"     # path содержит «SSD»
    # Все 5 узлов попали в map.
    assert len(cat_map) == 5


def test_build_category_map_collects_audit_warning_for_productful_none_branches(
    treolan_env, caplog,
):
    """Ветка с productsQty=50 и path под blocklist'ом → WARNING с её
    name/path/qty в логе. Это автоматический аудит blocklist'а."""
    import logging
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    tree = [{
        "id": 2, "name": "Серверы", "productsQty": 0, "products": [],
        "children": [{
            "id": 210, "name": "1-процессорные серверы",
            "productsQty": 50, "products": [], "children": [],
        }],
    }]

    fetcher = TreolanFetcher()
    with caplog.at_level(logging.WARNING, logger="portal.services.configurator.auto_price.fetchers.treolan"):
        fetcher._build_category_map(tree)

    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("classified" in r.message.lower() or "классифицированы" in r.message
               for r in warning_records), (
        f"ожидаем WARNING про productsQty>0 → None; got: {[r.message for r in warning_records]}"
    )
    # WARNING-сообщение должно упоминать имя/путь/qty.
    full_text = " ".join(r.getMessage() for r in warning_records)
    assert "1-процессорные серверы" in full_text
    assert "50" in full_text


def test_blocklist_for_server_branches_still_works_via_id_map(
    treolan_env, db_session,
):
    """Серверная ветка (Серверы → 1-процессорные) с productsQty>0:
    в category_map её id → None, и позиции в ней не должны получить
    нашу our_category (orchestrator такие позиции игнорирует).
    Это страховка против регрессии: blocklist должен по-прежнему
    отрезать сервера."""
    from sqlalchemy import text
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source) "
        "VALUES (CURRENT_DATE, 100.00, 'cbr')"
    ))
    db_session.commit()

    sample = {
        "categories": [{
            "id": 2, "name": "Серверы", "productsQty": 0, "products": [],
            "children": [{
                "id": 210, "name": "1-процессорные серверы",
                "productsQty": 1,
                "products": [{
                    "articul": "DELL-R250-X",
                    "rusName": "Сервер DELL PowerEdge R250",
                    "vendor": "Dell",
                    "currentPrice": "1660",
                    "currency": "USD",
                    "atStock": "<10",
                }],
                "children": [],
            }],
        }],
    }

    fetcher = TreolanFetcher()
    fetcher._save(sample)

    # category_map должен дать None для обоих серверных узлов.
    assert fetcher._category_map[2] is None
    assert fetcher._category_map[210] is None

    # Orchestrator при our_category=None пропускает строку (counters.skipped),
    # SKU НЕ должен оказаться в supplier_prices ни под какой категорией.
    in_prices = db_session.execute(text(
        "SELECT 1 FROM supplier_prices WHERE supplier_sku = 'DELL-R250-X' LIMIT 1"
    )).first()
    assert in_prices is None, (
        "сервер DELL R250 не должен оказаться в supplier_prices — "
        "blocklist обязан отрезать его на этапе category_map."
    )


# =====================================================================
# 12.5d: метрики category_map в report_json (observability)
# =====================================================================

def test_save_writes_category_map_metrics_to_report(treolan_env, db_session):
    """После _save price_uploads.report_json должен содержать
    category_map со всеми ключами: size, fallback_lookups,
    audit_misses_count, audit_misses_top5, by_our_category."""
    from sqlalchemy import text
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source) "
        "VALUES (CURRENT_DATE, 100.00, 'cbr')"
    ))
    db_session.commit()

    sample = {
        "categories": [
            {
                "id": 1, "name": "Комплектующие", "productsQty": 0,
                "products": [],
                "children": [
                    {
                        "id": 100, "name": "Процессоры", "productsQty": 1,
                        "products": [{
                            "articul": "MAP-METRICS-CPU",
                            "rusName": "CPU for metrics test",
                            "vendor": "Intel", "currentPrice": "5000",
                            "currency": "RUB", "atStock": "10",
                        }],
                        "children": [],
                    },
                    {
                        "id": 150, "name": "Блок питания ATX",
                        "productsQty": 0, "products": [], "children": [],
                    },
                ],
            },
            {
                "id": 2, "name": "Серверы", "productsQty": 0,
                "products": [],
                "children": [
                    {
                        "id": 210, "name": "1-процессорные серверы",
                        "productsQty": 50,  # productful-None ветка → audit miss
                        "products": [], "children": [],
                    },
                ],
            },
        ],
    }

    fetcher = TreolanFetcher()
    upload_id = fetcher._save(sample)

    rec = db_session.execute(text(
        "SELECT report_json FROM price_uploads WHERE id = :id"
    ), {"id": upload_id}).first()
    assert rec is not None
    report = rec.report_json
    assert "category_map" in report, (
        f"report должен содержать category_map; ключи: {list(report.keys())}"
    )
    cm = report["category_map"]

    # size = число узлов с int id (1, 100, 150, 2, 210) = 5.
    assert cm["size"] == 5

    # Без аномалий — все позиции попадают через id-lookup, fallback=0.
    assert cm["fallback_lookups"] == 0

    # Только узел 210 (productsQty=50, blocklist) — productful-None.
    assert cm["audit_misses_count"] == 1
    assert len(cm["audit_misses_top5"]) == 1
    miss = cm["audit_misses_top5"][0]
    assert miss["name"] == "1-процессорные серверы"
    assert "Серверы" in miss["path"]
    assert miss["products_qty"] == 50

    # by_our_category: по числу КАТЕГОРИЙ дерева, не позиций.
    by_cat = cm["by_our_category"]
    assert by_cat["cpu"] == 1       # узел 100 «Процессоры»
    assert by_cat["psu"] == 1       # узел 150 «Блок питания ATX»
    assert by_cat["none"] == 3      # узлы 1, 2, 210 → None
    # Остальные ключи присутствуют, но нулевые.
    for k in ("cooler", "gpu", "storage", "motherboard", "ram", "case"):
        assert by_cat[k] == 0, f"ожидаем by_our_category[{k!r}]=0, got {by_cat[k]}"


def test_category_map_audit_misses_top5_capped(treolan_env, db_session):
    """Если productful-None веток больше 5 — audit_misses_top5 длины 5,
    audit_misses_count показывает полное число."""
    from sqlalchemy import text
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source) "
        "VALUES (CURRENT_DATE, 100.00, 'cbr')"
    ))
    db_session.commit()

    # 7 серверных подкатегорий, каждая с productsQty>0 → blocklist режет в None.
    server_kids = [
        {
            "id": 300 + i, "name": f"Серверная категория {i}",
            "productsQty": 10 + i,
            "products": [], "children": [],
        }
        for i in range(7)
    ]
    sample = {
        "categories": [
            # Хотя бы одна валидная позиция, чтобы _save не упал на total_walked==0.
            {
                "id": 100, "name": "Процессоры", "productsQty": 1,
                "products": [{
                    "articul": "TOP5-CPU", "rusName": "Test CPU",
                    "vendor": "Intel", "currentPrice": "5000",
                    "currency": "RUB", "atStock": "10",
                }],
                "children": [],
            },
            {
                "id": 2, "name": "Серверы", "productsQty": 0,
                "products": [],
                "children": server_kids,
            },
        ],
    }

    fetcher = TreolanFetcher()
    upload_id = fetcher._save(sample)

    rec = db_session.execute(text(
        "SELECT report_json FROM price_uploads WHERE id = :id"
    ), {"id": upload_id}).first()
    cm = rec.report_json["category_map"]

    # 7 productful-None веток (узел «Серверы» сам — productsQty=0, не считается).
    assert cm["audit_misses_count"] == 7
    assert len(cm["audit_misses_top5"]) == 5
    # Все элементы top5 — dict с ожидаемыми ключами.
    for m in cm["audit_misses_top5"]:
        assert set(m.keys()) == {"name", "path", "products_qty"}
        assert m["products_qty"] >= 10


def test_category_map_metrics_with_zero_fallback(treolan_env, db_session):
    """Стандартный случай — все позиции мапятся через ID, fallback_lookups=0."""
    from sqlalchemy import text
    from portal.services.configurator.auto_price.fetchers.treolan import TreolanFetcher

    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source) "
        "VALUES (CURRENT_DATE, 100.00, 'cbr')"
    ))
    db_session.commit()

    sample = {
        "categories": [{
            "id": 100, "name": "Процессоры", "productsQty": 2,
            "products": [
                {
                    "articul": "ZF-CPU-1", "rusName": "Test CPU 1",
                    "vendor": "Intel", "currentPrice": "5000",
                    "currency": "RUB", "atStock": "10",
                },
                {
                    "articul": "ZF-CPU-2", "rusName": "Test CPU 2",
                    "vendor": "AMD", "currentPrice": "6000",
                    "currency": "RUB", "atStock": "5",
                },
            ],
            "children": [],
        }],
    }

    fetcher = TreolanFetcher()
    upload_id = fetcher._save(sample)

    rec = db_session.execute(text(
        "SELECT report_json FROM price_uploads WHERE id = :id"
    ), {"id": upload_id}).first()
    cm = rec.report_json["category_map"]

    assert cm["fallback_lookups"] == 0
    assert cm["size"] == 1
    assert cm["audit_misses_count"] == 0
    assert cm["audit_misses_top5"] == []
    assert cm["by_our_category"]["cpu"] == 1
    assert cm["by_our_category"]["none"] == 0
