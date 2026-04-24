# Курс USD/RUB от ЦБ РФ с файловым кэшем (этап 8.1).
#
# Источник: https://www.cbr.ru/scripts/XML_daily.asp — суточный XML курсов
# к рублю. Нас интересует валюта R01235 (USD). Ответ в Windows-1251 с
# «,» в качестве десятичного разделителя.
#
# Кэш лежит в data/exchange_rate_cache.json и обновляется только когда
# успешно получили «сегодняшний» курс. data/ в .gitignore, так что кэш
# не попадает в репозиторий — у каждой установки он свой.

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)


CBR_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
USD_VALUTE_ID = "R01235"
_HTTP_TIMEOUT = 5.0
_MAX_ATTEMPTS = 2


# Путь к файлу кэша задаётся относительно корня проекта (где запускается
# приложение). Для тестов его можно переопределить через set_cache_path().
_cache_path: Path = Path("data") / "exchange_rate_cache.json"


def set_cache_path(path: Path | str) -> None:
    """Тесты используют отдельный tmp-файл — чтобы не ломать локальный кэш."""
    global _cache_path
    _cache_path = Path(path)


def get_cache_path() -> Path:
    return _cache_path


@dataclass
class _CacheEntry:
    rate: Decimal
    rate_date: date
    fetched_at: datetime


# ---------------------------------------------------------------------
# HTTP + XML
# ---------------------------------------------------------------------

def _fetch_live() -> _CacheEntry:
    """Запрашивает XML_daily у ЦБ. Возвращает курс USD.

    Делает до _MAX_ATTEMPTS попыток. Пробрасывает последнее исключение,
    если все попытки упали — вызывающий код решает, откатываться ли в кэш.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
                resp = client.get(CBR_URL)
                resp.raise_for_status()
                # ЦБ отдаёт windows-1251 — httpx.text возьмёт из заголовка.
                xml_text = resp.text
            return _parse_xml(xml_text)
        except Exception as exc:
            last_exc = exc
            logger.warning(
                "exchange_rate: попытка %d/%d не удалась: %s",
                attempt, _MAX_ATTEMPTS, exc,
            )
    # Если пробрасывать не через raise from, теряется причина — пусть
    # будет явно последний exc.
    assert last_exc is not None
    raise last_exc


def _parse_xml(xml_text: str) -> _CacheEntry:
    """Находит <Valute ID="R01235"> и достаёт <Value> + дату из атрибута Date."""
    root = ET.fromstring(xml_text)
    # Дата в атрибуте Date корня, формат dd.mm.yyyy.
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
            # ЦБ использует «,» как десятичный разделитель.
            rate = Decimal(value_el.text.replace(",", "."))
            return _CacheEntry(
                rate=rate,
                rate_date=rate_date,
                fetched_at=datetime.now(),
            )
    raise RuntimeError(f"В XML ЦБ РФ не найдена валюта {USD_VALUTE_ID} (USD).")


# ---------------------------------------------------------------------
# Файловый кэш
# ---------------------------------------------------------------------

def _load_cache() -> _CacheEntry | None:
    path = _cache_path
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _CacheEntry(
            rate=Decimal(str(raw["rate"])),
            rate_date=date.fromisoformat(raw["date"]),
            fetched_at=datetime.fromisoformat(raw["fetched_at"]),
        )
    except Exception as exc:
        logger.warning("exchange_rate: повреждённый кэш %s: %s", path, exc)
        return None


def _save_cache(entry: _CacheEntry) -> None:
    path = _cache_path
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "rate": str(entry.rate),
        "date": entry.rate_date.isoformat(),
        "fetched_at": entry.fetched_at.isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------
# Публичная функция
# ---------------------------------------------------------------------

def get_usd_rate() -> tuple[Decimal, date, Literal["live", "cache"]]:
    """Возвращает (rate, rate_date, source).

    Логика:
      1. Если кэш существует И его дата совпадает с сегодняшней — отдаём
         кэш без обращения к ЦБ (экономим запрос и не зависим от сети).
      2. Иначе — пробуем получить live. При успехе обновляем кэш.
      3. Если live упал — откатываемся в кэш (даже если он не сегодняшний),
         возвращаем source='cache'.
      4. Если кэша нет И live упал — RuntimeError.
    """
    cached = _load_cache()
    today = date.today()

    if cached is not None and cached.rate_date == today:
        return cached.rate, cached.rate_date, "cache"

    try:
        fresh = _fetch_live()
    except Exception as exc:
        if cached is not None:
            logger.warning(
                "exchange_rate: live ЦБ упал (%s), используем старый кэш от %s",
                exc, cached.rate_date,
            )
            return cached.rate, cached.rate_date, "cache"
        raise RuntimeError(
            "Не удалось получить курс ЦБ РФ и нет кэша"
        ) from exc

    _save_cache(fresh)
    return fresh.rate, fresh.rate_date, "live"
