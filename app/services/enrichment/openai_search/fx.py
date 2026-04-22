# Получение актуального курса USD/RUB без токенов и платных API.
#
# Источник: ЦБ РФ, эндпоинт https://www.cbr-xml-daily.ru/daily_json.js
# — бесплатный, без регистрации, JSON с дневными курсами.
#
# Курс кэшируется в data/.fx_cache.json на один календарный день: за сутки
# делается максимум один HTTP-запрос. Если ЦБ недоступен и кэша на сегодня
# нет — используется fallback из переменной окружения OPENAI_ENRICH_USD_RUB_FALLBACK
# (по умолчанию 95.0).

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

_CBR_URL = "https://www.cbr-xml-daily.ru/daily_json.js"
_TIMEOUT_SEC = 5.0
_CACHE_FILE = Path(__file__).resolve().parents[4] / "data" / ".fx_cache.json"
_FALLBACK_ENV = "OPENAI_ENRICH_USD_RUB_FALLBACK"
_FALLBACK_DEFAULT = 95.0


def _load_cache() -> dict[str, Any] | None:
    try:
        with _CACHE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("FX cache повреждён, игнорируем: %s", exc)
        return None


def _save_cache(today: str, rate: float) -> None:
    try:
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _CACHE_FILE.open("w", encoding="utf-8") as f:
            json.dump({"date": today, "usd_rub": rate, "source": "cbr"}, f)
    except Exception as exc:
        logger.warning("Не удалось записать FX cache: %s", exc)


def _fetch_from_cbr() -> float | None:
    try:
        # В HTTP-заголовках допустим только ASCII, поэтому никаких русских букв.
        req = Request(_CBR_URL, headers={"User-Agent": "ConfiguratorPC2/2.5"})
        with urlopen(req, timeout=_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        usd = data.get("Valute", {}).get("USD", {})
        val = usd.get("Value")
        if isinstance(val, (int, float)) and val > 0:
            return float(val)
    except (URLError, TimeoutError, ValueError) as exc:
        logger.warning("Не удалось получить курс ЦБ РФ: %s", exc)
    except Exception as exc:
        logger.warning("Неожиданная ошибка при запросе ЦБ: %s", exc)
    return None


def _fallback() -> float:
    raw = os.getenv(_FALLBACK_ENV)
    if raw:
        try:
            v = float(raw)
            if v > 0:
                return v
        except ValueError:
            pass
    return _FALLBACK_DEFAULT


def get_usd_rub_rate(*, force_refresh: bool = False) -> tuple[float, str]:
    """Возвращает (курс, источник).

    Источник:
      - 'cbr'      — свежее значение от ЦБ РФ;
      - 'cache'    — курс из файла-кэша на сегодня;
      - 'fallback' — значение из переменной окружения или дефолт.

    Кэш: data/.fx_cache.json, {"date":"YYYY-MM-DD","usd_rub":<float>,"source":"cbr"}.
    """
    today = date.today().isoformat()

    # 1) кэш
    if not force_refresh:
        cache = _load_cache()
        if cache and cache.get("date") == today:
            rate = cache.get("usd_rub")
            if isinstance(rate, (int, float)) and rate > 0:
                return float(rate), "cache"

    # 2) ЦБ
    rate = _fetch_from_cbr()
    if rate is not None:
        _save_cache(today, rate)
        return rate, "cbr"

    # 3) fallback
    return _fallback(), "fallback"
