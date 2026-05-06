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
    import app.services.auto_price.fetchers.treolan as treolan_mod

    def _factory(timeout=None):
        return FakeClient(handler)

    monkeypatch.setattr(treolan_mod.httpx, "Client", _factory)


# ---- 1. _get_token: основной endpoint ---------------------------------

def test_get_token_success_token_endpoint(treolan_env, monkeypatch):
    from app.services.auto_price.fetchers.treolan import TreolanFetcher

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
    from app.services.auto_price.fetchers.treolan import TreolanFetcher

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
    from app.services.auto_price.fetchers.treolan import TreolanFetcher

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
    from app.services.auto_price.fetchers.treolan import TreolanFetcher

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
    from app.services.auto_price.fetchers.treolan import TreolanFetcher

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
    from app.services.auto_price.fetchers.treolan import _walk_products
    walked = list(_walk_products(data["categories"]))
    assert len(walked) == 2
    sku_set = {p["articul"] for _path, p in walked}
    assert sku_set == {"BX8071512400F", "PCASE-001"}


# ---- 6. _save: реальный INSERT в price_uploads/supplier_prices --------

def test_save_inserts_price_upload_and_rows(treolan_env, monkeypatch, db_session):
    from app.services.auto_price.fetchers.treolan import TreolanFetcher

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
    from app.services.auto_price.fetchers.treolan import TreolanFetcher

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
    from app.services.auto_price.fetchers.treolan import TreolanFetcher

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
    from app.services.auto_price.fetchers.treolan import TreolanFetcher

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
    from app.services.auto_price.fetchers.treolan import _walk_products

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
    skus = [p["articul"] for _path, p in walked]
    assert sorted(skus) == ["A1", "A2", "A3", "A4", "B1"]

    # Пути сохраняют последовательность от корня к листу.
    sku_to_path = {p["articul"]: path for path, p in walked}
    assert sku_to_path["A1"] == ["L0-A"]
    assert sku_to_path["A2"] == ["L0-A", "L1-A"]
    assert sku_to_path["A3"] == ["L0-A", "L1-A", "L2-A"]
    assert sku_to_path["B1"] == ["L0-B", "L1-B"]


def test_walk_products_empty_tree_yields_nothing():
    from app.services.auto_price.fetchers.treolan import _walk_products

    assert list(_walk_products([])) == []
    assert list(_walk_products(None)) == []
    # Дерево из пустых нод — тоже ничего.
    tree = [{"name": "X", "products": [], "children": []}]
    assert list(_walk_products(tree)) == []


def test_save_raises_runtimeerror_on_empty_categories(treolan_env):
    """Defensive layer 1: если data['categories'] пустой — RuntimeError,
    pipeline закроется failed и disappeared НЕ запустится."""
    from app.services.auto_price.fetchers.treolan import TreolanFetcher

    fetcher = TreolanFetcher()
    with pytest.raises(RuntimeError, match="categories"):
        fetcher._save({"categories": []})
    with pytest.raises(RuntimeError, match="categories"):
        fetcher._save({})


def test_save_raises_runtimeerror_on_zero_products_after_walk(treolan_env):
    """Defensive layer 2: categories есть, но после DFS ни одного товара
    не нашлось → RuntimeError. Закрывает случай 'структура изменилась
    второй раз и products куда-то переехали'."""
    from app.services.auto_price.fetchers.treolan import TreolanFetcher

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
    from app.services.auto_price.fetchers.treolan import TreolanFetcher

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
    from app.services.auto_price.fetchers.treolan import _detect_our_category

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
