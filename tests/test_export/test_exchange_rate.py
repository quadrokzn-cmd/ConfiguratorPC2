# Тесты сервиса курса ЦБ РФ.
#
# Этап 9А.2.3: курс хранится в БД (таблица exchange_rates), а не в
# файловом кэше. Все тесты идут через тестовую БД и моки httpx.
# Сеть никогда не дёргается.

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from portal.services.configurator.export import exchange_rate


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
def db_session():
    """Прямая сессия в тестовую БД с миграциями 015 уже применёнными.

    Не зависит от веб-conftest — работает в test_export/, где web-conftest
    не подгружается."""
    from app.config import settings
    engine = create_engine(
        settings.test_database_url,
        future=True,
        connect_args={"client_encoding": "utf8"},
    )
    # Гарантируем наличие таблицы (если этот файл прогоняется в одиночку).
    from pathlib import Path
    sql = (
        Path(__file__).resolve().parents[2]
        / "migrations" / "015_exchange_rates_table.sql"
    ).read_text(encoding="utf-8")
    with engine.begin() as conn:
        conn.execute(text(sql))
        conn.execute(text("TRUNCATE TABLE exchange_rates RESTART IDENTITY"))

    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


def _make_http_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        content=text.encode("windows-1251"),
        headers={"content-type": "application/xml; charset=windows-1251"},
        request=httpx.Request("GET", exchange_rate.CBR_URL),
    )


def _mock_client_returning(xml: str) -> MagicMock:
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get = MagicMock(return_value=_make_http_response(xml))
    return mock_client


def test_fetch_and_store_writes_to_db(db_session):
    """fetch_and_store_cbr_rate пишет курс в exchange_rates."""
    mc = _mock_client_returning(_SAMPLE_XML)
    with patch.object(httpx, "Client", return_value=mc):
        info = exchange_rate.fetch_and_store_cbr_rate(db_session)

    assert info.rate == Decimal("95.1234")
    assert info.rate_date == date(2026, 4, 10)
    assert info.source == "cbr"

    # В БД появилась запись.
    row = db_session.execute(
        text("SELECT rate_usd_rub, source FROM exchange_rates WHERE rate_date = '2026-04-10'")
    ).first()
    assert row is not None
    assert Decimal(str(row.rate_usd_rub)) == Decimal("95.1234")
    assert row.source == "cbr"


def test_fetch_skips_recent_cbr_call(db_session):
    """Повторный fetch в течение часа не дёргает ЦБ."""
    today = date.today()
    db_session.execute(
        text(
            "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source, fetched_at) "
            "VALUES (:d, 95.0, 'cbr', NOW())"
        ),
        {"d": today},
    )
    db_session.commit()

    mc = _mock_client_returning(_SAMPLE_XML)
    with patch.object(httpx, "Client", return_value=mc):
        info = exchange_rate.fetch_and_store_cbr_rate(db_session)

    # ЦБ не дёргался — рейт остался прежним.
    assert info.rate_date == today
    assert info.rate == Decimal("95.0000")
    mc.get.assert_not_called()


def test_get_current_rate_returns_latest(db_session):
    """get_current_rate возвращает самый свежий курс по rate_date DESC."""
    older = date.today() - timedelta(days=2)
    today = date.today()
    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source, fetched_at) "
        "VALUES (:d1, 80.0, 'cbr', NOW()), (:d2, 95.5, 'cbr', NOW())"
    ), {"d1": older, "d2": today})
    db_session.commit()

    info = exchange_rate.get_current_rate(db_session)
    assert info.rate_date == today
    assert info.rate == Decimal("95.5000")


def test_get_current_rate_fallback_to_cbr_when_empty(db_session):
    """Если в БД пусто, get_current_rate сам зовёт fetch_and_store."""
    mc = _mock_client_returning(_SAMPLE_XML)
    with patch.object(httpx, "Client", return_value=mc):
        info = exchange_rate.get_current_rate(db_session)
    assert info.rate == Decimal("95.1234")
    assert mc.get.call_count == 1


def test_fetch_falls_back_to_db_when_cbr_down(db_session):
    """Если ЦБ упал, но в БД есть запись — возвращаем последнюю существующую."""
    older = date.today() - timedelta(days=3)
    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source, fetched_at) "
        "VALUES (:d, 91.1234, 'cbr', NOW())"
    ), {"d": older})
    db_session.commit()

    mc = MagicMock()
    mc.__enter__ = MagicMock(return_value=mc)
    mc.__exit__ = MagicMock(return_value=False)
    mc.get = MagicMock(side_effect=httpx.ConnectTimeout("timeout"))

    with patch.object(httpx, "Client", return_value=mc):
        info = exchange_rate.fetch_and_store_cbr_rate(db_session)
    assert info.rate == Decimal("91.1234")
    assert info.rate_date == older


def test_fetch_raises_when_db_empty_and_cbr_down(db_session):
    """Кэша нет в БД, ЦБ недоступен — RuntimeError."""
    mc = MagicMock()
    mc.__enter__ = MagicMock(return_value=mc)
    mc.__exit__ = MagicMock(return_value=False)
    mc.get = MagicMock(side_effect=httpx.ConnectTimeout("timeout"))

    with patch.object(httpx, "Client", return_value=mc):
        with pytest.raises(RuntimeError, match="курс ЦБ РФ"):
            exchange_rate.fetch_and_store_cbr_rate(db_session)


def test_xml_without_usd_raises(db_session):
    """XML без валюты R01235 (USD) — ошибка парсинга. Если БД пуста — RuntimeError."""
    xml_without_usd = """<?xml version="1.0" encoding="windows-1251"?>
    <ValCurs Date="10.04.2026" name="x">
      <Valute ID="R01239"><Value>100,00</Value></Valute>
    </ValCurs>
    """
    mc = _mock_client_returning(xml_without_usd)
    with patch.object(httpx, "Client", return_value=mc):
        with pytest.raises(RuntimeError):
            exchange_rate.fetch_and_store_cbr_rate(db_session)


def test_http_retries_on_transient_failure(db_session):
    """После первой ошибки запроса делаем вторую попытку — успех."""
    mc = MagicMock()
    mc.__enter__ = MagicMock(return_value=mc)
    mc.__exit__ = MagicMock(return_value=False)
    mc.get = MagicMock(side_effect=[
        httpx.ReadTimeout("slow"),
        _make_http_response(_SAMPLE_XML),
    ])
    with patch.object(httpx, "Client", return_value=mc):
        info = exchange_rate.fetch_and_store_cbr_rate(db_session)
    assert info.rate == Decimal("95.1234")
    assert mc.get.call_count == 2


def test_get_usd_rate_legacy_interface(db_session):
    """Старый интерфейс get_usd_rate() используется в Excel/Word экспорте."""
    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source, fetched_at) "
        "VALUES (:d, 92.5, 'cbr', NOW())"
    ), {"d": date.today()})
    db_session.commit()

    rate, rate_date, source = exchange_rate.get_usd_rate()
    assert rate == Decimal("92.5000")
    assert rate_date == date.today()
    assert source in ("live", "cache")


def test_get_rate_history(db_session):
    """history возвращает курсы за последние N дней (DESC)."""
    today = date.today()
    db_session.execute(text(
        "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source, fetched_at) "
        "VALUES "
        "(:d1, 80.0, 'cbr', NOW()), "
        "(:d2, 90.0, 'cbr', NOW()), "
        "(:d3, 95.5, 'cbr', NOW())"
    ), {
        "d1": today - timedelta(days=10),
        "d2": today - timedelta(days=2),
        "d3": today,
    })
    db_session.commit()

    hist = exchange_rate.get_rate_history(7, db_session)
    assert len(hist) == 2  # 10 дней назад не должно попасть
    assert hist[0].rate_date == today
    assert hist[1].rate_date == today - timedelta(days=2)
