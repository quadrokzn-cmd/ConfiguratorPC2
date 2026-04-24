# Юнит-тесты сервиса курса ЦБ РФ (этап 8.1).
#
# Не ходит в сеть и не трогает общий проектный кэш: для каждого теста
# задаётся tmp-файл кэша через exchange_rate.set_cache_path().

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.services.export import exchange_rate


# Готовый маленький XML_daily с USD — как отдаёт ЦБ.
_SAMPLE_XML = """<?xml version="1.0" encoding="windows-1251"?>
<ValCurs Date="10.04.2026" name="Foreign Currency Market">
  <Valute ID="R01235">
    <NumCode>840</NumCode>
    <CharCode>USD</CharCode>
    <Nominal>1</Nominal>
    <Name>Доллар США</Name>
    <Value>95,1234</Value>
    <VunitRate>95,1234</VunitRate>
  </Valute>
  <Valute ID="R01239">
    <NumCode>978</NumCode>
    <CharCode>EUR</CharCode>
    <Nominal>1</Nominal>
    <Name>Евро</Name>
    <Value>102,5</Value>
    <VunitRate>102,5</VunitRate>
  </Valute>
</ValCurs>
"""


@pytest.fixture()
def _tmp_cache(tmp_path, monkeypatch):
    """Перенаправляем кэш в tmp-каталог на время теста."""
    cache_file = tmp_path / "exchange_rate_cache.json"
    exchange_rate.set_cache_path(cache_file)
    yield cache_file
    exchange_rate.set_cache_path("data/exchange_rate_cache.json")


def _make_http_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=text.encode("windows-1251"),
        headers={"content-type": "application/xml; charset=windows-1251"},
        request=httpx.Request("GET", exchange_rate.CBR_URL),
    )


def test_live_success_parses_and_writes_cache(_tmp_cache):
    """Успех live → разбираем XML, возвращаем 'live', кладём кэш на диск."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=_make_http_response(_SAMPLE_XML))

    with patch.object(httpx, "Client", return_value=mock_client):
        rate, rate_date, source = exchange_rate.get_usd_rate()

    assert source == "live"
    assert rate == Decimal("95.1234")
    assert rate_date == date(2026, 4, 10)
    assert _tmp_cache.exists()
    payload = json.loads(_tmp_cache.read_text(encoding="utf-8"))
    assert payload["rate"] == "95.1234"
    assert payload["date"] == "2026-04-10"


def test_fresh_cache_today_returns_without_http(_tmp_cache):
    """Если в кэше сегодняшний курс — HTTP не дёргаем вообще."""
    today = date.today()
    _tmp_cache.write_text(
        json.dumps({
            "rate": "93.50",
            "date": today.isoformat(),
            "fetched_at": datetime.now().isoformat(),
        }),
        encoding="utf-8",
    )

    with patch.object(httpx, "Client") as mock_cli:
        rate, rate_date, source = exchange_rate.get_usd_rate()

    assert source == "cache"
    assert rate == Decimal("93.50")
    assert rate_date == today
    mock_cli.assert_not_called()


def test_stale_cache_and_live_fail_returns_old_cache(_tmp_cache):
    """Live упал, но есть несвежий кэш → отдаём его с source='cache'."""
    older = date.today() - timedelta(days=3)
    _tmp_cache.write_text(
        json.dumps({
            "rate": "91.1234",
            "date": older.isoformat(),
            "fetched_at": datetime.now().isoformat(),
        }),
        encoding="utf-8",
    )

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(side_effect=httpx.ConnectTimeout("timeout"))

    with patch.object(httpx, "Client", return_value=mock_client):
        rate, rate_date, source = exchange_rate.get_usd_rate()

    assert source == "cache"
    assert rate == Decimal("91.1234")
    assert rate_date == older


def test_no_cache_and_live_fail_raises_runtime_error(_tmp_cache):
    """Кэша нет, ЦБ недоступен — RuntimeError."""
    assert not _tmp_cache.exists()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(side_effect=httpx.ConnectTimeout("timeout"))

    with patch.object(httpx, "Client", return_value=mock_client):
        with pytest.raises(RuntimeError, match="курс ЦБ РФ"):
            exchange_rate.get_usd_rate()


def test_xml_without_usd_raises(_tmp_cache):
    """XML без валюты R01235 (USD) — ошибка парсинга, live считается упавшим.

    Так как кэша нет — итог RuntimeError.
    """
    xml_without_usd = """<?xml version="1.0" encoding="windows-1251"?>
    <ValCurs Date="10.04.2026" name="x">
      <Valute ID="R01239"><Value>100,00</Value></Valute>
    </ValCurs>
    """
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(
        return_value=_make_http_response(xml_without_usd),
    )
    with patch.object(httpx, "Client", return_value=mock_client):
        with pytest.raises(RuntimeError):
            exchange_rate.get_usd_rate()


def test_http_retries_on_transient_failure(_tmp_cache):
    """После первой ошибки запроса делаем вторую попытку; если она ок —
    результат live, без обращения к кэшу."""
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    # 1-й вызов — падение, 2-й — успех.
    mock_client.get = MagicMock(side_effect=[
        httpx.ReadTimeout("slow"),
        _make_http_response(_SAMPLE_XML),
    ])

    with patch.object(httpx, "Client", return_value=mock_client):
        rate, rate_date, source = exchange_rate.get_usd_rate()

    assert source == "live"
    assert rate == Decimal("95.1234")
    # Убеждаемся, что вызывали дважды.
    assert mock_client.get.call_count == 2
