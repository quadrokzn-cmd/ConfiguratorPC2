# Курс USD/RUB от ЦБ РФ — хранение в БД, scheduler-обновление (этап 9А.2.3).
#
# Источник: https://www.cbr.ru/scripts/XML_daily.asp — суточный XML курсов
# к рублю. Нас интересует валюта R01235 (USD). Ответ в Windows-1251 с
# «,» в качестве десятичного разделителя.
#
# Раньше (этап 8.1): файловый JSON-кэш в data/exchange_rate_cache.json,
# обновлялся ленивым get_usd_rate(). Каждый инстанс приложения имел свой кэш.
#
# Теперь:
#   - таблица exchange_rates хранит ВСЕ полученные курсы (история);
#   - APScheduler 5 раз в день (8:30, 13:00, 16:00, 17:00, 18:15 МСК)
#     ходит на ЦБ и кладёт сюда новый курс через fetch_and_store_cbr_rate();
#   - get_current_rate(db) возвращает самый свежий курс из БД для UI/экспорта.

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
USD_VALUTE_ID = "R01235"
_HTTP_TIMEOUT = 5.0
_MAX_ATTEMPTS = 2

# Если в БД сегодня уже есть запись с source='cbr' и она получена меньше
# этого окна назад — повторно ЦБ не дёргаем. Экономия на повторных cron-запусках.
_FETCH_SKIP_WINDOW = timedelta(hours=1)


@dataclass
class ExchangeRateInfo:
    """Курс из БД, отдаётся в UI и экспорт."""
    rate: Decimal
    rate_date: date
    fetched_at: datetime
    source: str = "cbr"


# ---------------------------------------------------------------------
# HTTP + XML
# ---------------------------------------------------------------------

@dataclass
class _CBRReply:
    rate: Decimal
    rate_date: date


def _fetch_live() -> _CBRReply:
    """Запрашивает XML_daily у ЦБ. Возвращает (rate, rate_date).

    Делает до _MAX_ATTEMPTS попыток. Пробрасывает последнее исключение,
    если все попытки упали — вызывающий код решает, что делать дальше
    (логирует и оставляет старый курс в БД).
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                resp = client.get(CBR_URL)
                resp.raise_for_status()
                xml_text = resp.text
            return _parse_xml(xml_text)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "exchange_rate: попытка %d/%d не удалась: %s",
                attempt, _MAX_ATTEMPTS, exc,
            )
    assert last_exc is not None
    raise last_exc


def _parse_xml(xml_text: str) -> _CBRReply:
    """Находит <Valute ID="R01235"> и достаёт <Value> + дату из атрибута Date."""
    root = ET.fromstring(xml_text)
    date_str = root.attrib.get("Date")
    rate_date = (
        datetime.strptime(date_str, "%d.%m.%Y").date()
        if date_str
        else date.today()
    )
    for v in root.findall("Valute"):
        if v.attrib.get("ID") == USD_VALUTE_ID:
            value_el = v.find("Value")
            if value_el is None or not value_el.text:
                raise RuntimeError("В XML ЦБ РФ нет <Value> для USD.")
            rate = Decimal(value_el.text.replace(",", "."))
            return _CBRReply(rate=rate, rate_date=rate_date)
    raise RuntimeError(f"В XML ЦБ РФ не найдена валюта {USD_VALUTE_ID} (USD).")


# ---------------------------------------------------------------------
# Чтение из БД
# ---------------------------------------------------------------------

def _row_to_info(row) -> ExchangeRateInfo:
    fetched_at = row.fetched_at
    if fetched_at is not None and fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    return ExchangeRateInfo(
        rate=Decimal(str(row.rate_usd_rub)),
        rate_date=row.rate_date,
        fetched_at=fetched_at,
        source=row.source,
    )


def _latest_row(db: Session):
    return db.execute(
        text(
            "SELECT id, rate_date, rate_usd_rub, source, fetched_at "
            "FROM exchange_rates "
            "ORDER BY rate_date DESC, fetched_at DESC LIMIT 1"
        )
    ).first()


def get_current_rate(db: Session) -> ExchangeRateInfo:
    """Возвращает самый свежий курс из БД.

    Если в БД ничего нет — синхронно подтягивает с ЦБ
    (только при самом первом запуске инстанса, дальше scheduler следит).
    Если ЦБ при первом запуске недоступен и таблица пустая — RuntimeError.
    """
    row = _latest_row(db)
    if row is not None:
        return _row_to_info(row)
    return fetch_and_store_cbr_rate(db)


def get_rate_history(days: int, db: Session) -> list[ExchangeRateInfo]:
    """История курса за последние N дней — для будущих графиков и аналитики.

    Сейчас в UI не используется, но пусть будет — заказчик уже несколько раз
    спрашивал про «график как менялся курс».
    """
    cutoff = date.today() - timedelta(days=int(days))
    rows = db.execute(
        text(
            "SELECT id, rate_date, rate_usd_rub, source, fetched_at "
            "FROM exchange_rates WHERE rate_date >= :d "
            "ORDER BY rate_date DESC, fetched_at DESC"
        ),
        {"d": cutoff},
    ).all()
    return [_row_to_info(r) for r in rows]


# ---------------------------------------------------------------------
# Запись из ЦБ → БД
# ---------------------------------------------------------------------

def fetch_and_store_cbr_rate(db: Session) -> ExchangeRateInfo:
    """Ходит на ЦБ РФ, парсит, записывает в БД.

    Если today уже есть в БД с source='cbr' и fetched_at в течение
    _FETCH_SKIP_WINDOW — пропускает HTTP-запрос (экономим на повторных
    cron-запусках, когда курс точно не успел обновиться).

    Возвращает либо новую запись, либо последнюю существующую.

    Если ЦБ недоступен и в БД ничего нет — пробрасывает исключение наверх.
    Если ЦБ недоступен, но в БД есть прошлый курс — возвращает последний
    известный (логируя warning).
    """
    today = date.today()
    now = datetime.now(timezone.utc)

    # 1. Уже есть свежая запись за сегодня — не трогаем сеть.
    fresh_row = db.execute(
        text(
            "SELECT id, rate_date, rate_usd_rub, source, fetched_at "
            "FROM exchange_rates "
            "WHERE rate_date = :d AND source = 'cbr' "
            "ORDER BY fetched_at DESC LIMIT 1"
        ),
        {"d": today},
    ).first()
    if fresh_row is not None:
        info = _row_to_info(fresh_row)
        if info.fetched_at is not None and (now - info.fetched_at) < _FETCH_SKIP_WINDOW:
            return info

    # 2. Идём на ЦБ.
    try:
        reply = _fetch_live()
    except Exception as exc:
        last_row = _latest_row(db)
        if last_row is not None:
            logger.warning(
                "exchange_rate: ЦБ недоступен (%s), используем "
                "последний курс в БД от %s",
                exc, last_row.rate_date,
            )
            return _row_to_info(last_row)
        raise RuntimeError("Не удалось получить курс ЦБ РФ и нет записей в БД") from exc

    # 3. UPSERT по (rate_date, source).
    db.execute(
        text(
            "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source, fetched_at) "
            "VALUES (:d, :r, 'cbr', NOW()) "
            "ON CONFLICT (rate_date, source) DO UPDATE "
            "  SET rate_usd_rub = EXCLUDED.rate_usd_rub, "
            "      fetched_at  = EXCLUDED.fetched_at"
        ),
        {"d": reply.rate_date, "r": str(reply.rate)},
    )
    db.commit()

    row = db.execute(
        text(
            "SELECT id, rate_date, rate_usd_rub, source, fetched_at "
            "FROM exchange_rates "
            "WHERE rate_date = :d AND source = 'cbr' LIMIT 1"
        ),
        {"d": reply.rate_date},
    ).first()
    return _row_to_info(row)


# ---------------------------------------------------------------------
# Старый интерфейс — оставлен для совместимости с export-роутами
# ---------------------------------------------------------------------

def get_usd_rate() -> tuple[Decimal, date, Literal["live", "cache"]]:
    """Старый интерфейс (этап 8.1). Возвращает (rate, rate_date, source).

    Используется в Excel/Word экспорте, где курс должен фиксироваться
    в момент генерации файла. Берёт самый свежий курс из БД.

    source='live' — если запись была создана scheduler'ом за последний час
                    (фактически свежий курс с ЦБ);
    source='cache' — если запись постарше (но свежее в БД нет).
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        info = get_current_rate(db)
    finally:
        db.close()
    now = datetime.now(timezone.utc)
    label: Literal["live", "cache"]
    if info.fetched_at is not None and (now - info.fetched_at) < _FETCH_SKIP_WINDOW:
        label = "live"
    else:
        label = "cache"
    return info.rate, info.rate_date, label
