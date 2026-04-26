# Общая Jinja2Templates-инстанция со всеми проектными globals и фильтрами
# (этап 9А.2.3).
#
# Раньше каждый роутер делал свой Jinja2Templates(directory="app/templates") —
# это работало, но делало невозможным централизованную регистрацию
# фильтров вроде to_rub, которые должны быть доступны на каждой странице.
#
# Теперь:
#   - один шаблонный движок с globals: current_exchange_rate (callable);
#   - фильтр to_rub: USD-сумма → RUB по самому свежему курсу из БД;
#   - все роутеры импортируют ту же инстанцию через templates.

from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi.templating import Jinja2Templates

from app.database import SessionLocal
from app.services.export import exchange_rate

logger = logging.getLogger(__name__)


templates = Jinja2Templates(directory="app/templates")


def _safe_get_current_rate() -> exchange_rate.ExchangeRateInfo | None:
    """Достаёт самый свежий курс из БД. Не падает наружу — если в БД
    пусто и ЦБ недоступен (редкий edge-case), вернёт None, и плашка
    в sidebar просто скроется."""
    db = SessionLocal()
    try:
        return exchange_rate.get_current_rate(db)
    except Exception as exc:
        logger.warning("templating: не удалось прочитать текущий курс: %s", exc)
        return None
    finally:
        db.close()


def current_exchange_rate() -> dict[str, Any] | None:
    """Глобал для шаблонов: словарь с курсом или None.

    Структура:
      {
        'rate':         95.27,
        'rate_date':    date(2026, 4, 26),
        'fetched_at':   datetime,
        'source':       'cbr',
        'fetched_label': '16:00',  # короткая «hh:mm» в МСК для плашки
      }
    """
    info = _safe_get_current_rate()
    if info is None:
        return None
    fetched_at = info.fetched_at
    fetched_label = ""
    if fetched_at is not None:
        # Приводим к МСК — у пользователей в России таймзоны разные,
        # но плашка по ТЗ показывает время МСК.
        try:
            from zoneinfo import ZoneInfo  # py3.9+
            msk = ZoneInfo("Europe/Moscow")
            local = (
                fetched_at.astimezone(msk)
                if fetched_at.tzinfo
                else fetched_at.replace(tzinfo=timezone.utc).astimezone(msk)
            )
            fetched_label = local.strftime("%H:%M")
        except Exception:
            fetched_label = fetched_at.strftime("%H:%M") if hasattr(fetched_at, "strftime") else ""
    return {
        "rate":          float(info.rate),
        "rate_date":     info.rate_date,
        "fetched_at":    fetched_at,
        "source":        info.source,
        "fetched_label": fetched_label,
    }


def to_rub(usd: Any) -> float:
    """Фильтр для шаблонов: USD-сумма → RUB по актуальному курсу.

    Использование:  {{ it.unit_usd | to_rub }}  →  округлённая сумма в ₽.
    Если курса нет — возвращаем 0 (плашка в UI всё равно покажет «—»,
    цены сами по себе на странице тоже редкий edge-case).
    """
    if usd is None:
        return 0.0
    try:
        usd_f = float(usd)
    except (TypeError, ValueError):
        return 0.0
    info = _safe_get_current_rate()
    if info is None:
        return 0.0
    return round(usd_f * float(info.rate), 2)


def fmt_rub(usd: Any) -> str:
    """Фильтр: USD → отформатированная строка в ₽ с разделителем тысяч.

    Используется в местах, где раньше было `'%.0f'|format(... | to_rub)`
    плюс ручной replace на пробелы. Один шаг короче и даёт ровно тот
    формат, к которому привыкли в шаблонах: «1 234 567»."""
    rub = to_rub(usd)
    return "{:,.0f}".format(rub).replace(",", " ")


# Регистрируем globals и фильтры на единственном шаблонном движке.
templates.env.globals["current_exchange_rate"] = current_exchange_rate
templates.env.filters["to_rub"] = to_rub
templates.env.filters["fmt_rub"] = fmt_rub
