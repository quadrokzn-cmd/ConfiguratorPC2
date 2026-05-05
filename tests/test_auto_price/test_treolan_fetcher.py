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

_TREOLAN_API_SAMPLE = {
    "categories": [
        {"id": 100, "rusName": "Комплектующие->Процессоры"},
        {"id": 200, "rusName": "Комплектующие->Корпуса"},
    ],
    "positions": [
        {
            "articul":      "BX8071512400F",
            "rusName":      "Процессор Intel Core i5-12400F BOX",
            "vendor":       "Intel",
            "currentPrice": "180.50",
            "price":        "200.00",
            "currency":     "USD",
            "atStock":      "12",
            "inTransit":    "0",
            "gtin":         "5032037240306",
            "category-id":  100,
        },
        {
            "articul":      "PCASE-001",
            "rusName":      "Корпус DeepCool MATREXX 55",
            "vendor":       "DeepCool",
            "currentPrice": "5500",
            "price":        "5500",
            "currency":     "RUB",
            "atStock":      "3",
            "inTransit":    "5",
            "gtin":         "",
            "category-id":  200,
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

    assert "positions" in data
    assert len(data["positions"]) == 2
    assert data["positions"][0]["articul"] == "BX8071512400F"


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
        "categories": [{"id": 100, "rusName": "Комплектующие->Процессоры"}],
        "positions": [
            {
                "articul": "EUR-CPU", "rusName": "Test CPU", "vendor": "X",
                "currentPrice": "100", "currency": "EUR", "atStock": "1",
                "category-id": 100,
            },
            {
                "articul": "RUB-CPU", "rusName": "Test CPU2", "vendor": "X",
                "currentPrice": "1000", "currency": "RUB", "atStock": "2",
                "category-id": 100,
            },
        ],
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
        "categories": [{"id": 100, "rusName": "Комплектующие->Процессоры"}],
        "positions": [
            {
                "articul": "USD-CPU-1",
                "rusName": "Intel CPU 200 USD",
                "vendor": "Intel",
                "currentPrice": "200.00",
                "currency": "USD",
                "atStock": "5",
                "category-id": 100,
            },
        ],
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
