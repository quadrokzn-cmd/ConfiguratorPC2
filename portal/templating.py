# Шаблоны портала.
#
# Этап 9Б.1: минимальная регистрация (только globals portal_url/configurator_url
# и static_url).
# Этап 9Б.2: фильтры для дашборда (ru_date, ru_datetime_short, days_ago).
# Этап 9Б.2.1: курс ЦБ — общий партиал shared/templates/_partials/fx_widget.html
# импортирует Jinja-global current_exchange_rate(), регистрируем здесь же.
# Реализацию переиспользуем из app/templating.py — функция уже умеет
# открывать SessionLocal и парсить fetched_at в МСК.

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import settings


# 9Б.2.1: добавляем shared/templates как fallback. Партиалы
# (`_partials/fx_widget.html`) одни и те же для обоих сервисов;
# свои шаблоны портала находятся первыми.
templates = Jinja2Templates(directory=["portal/templates", "shared/templates"])


_STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"


def static_url(rel_path: str) -> str:
    """Cache-busting URL для статики. Аналог app/templating.py:static_url —
    ?v=<mtime файла>, чтобы пересборка CSS не упиралась в кеш браузера."""
    rel = rel_path.lstrip("/")
    if rel.startswith("static/"):
        rel_inside_static = rel[len("static/"):]
    else:
        rel_inside_static = rel
    full = _STATIC_ROOT / rel_inside_static
    base_url = "/static/" + rel_inside_static
    try:
        mtime = int(full.stat().st_mtime)
    except OSError:
        return base_url
    return f"{base_url}?v={mtime}"


templates.env.globals["static_url"] = static_url
templates.env.globals["portal_url"] = settings.portal_url
templates.env.globals["configurator_url"] = settings.configurator_url

# 9Б.2: фильтры для дашборда — русское форматирование дат и «N дней назад».
from portal.services import dashboard as _dashboard

templates.env.filters["ru_date"] = _dashboard.format_ru_date
templates.env.filters["ru_datetime_short"] = _dashboard.format_ru_datetime_short
templates.env.filters["days_ago"] = _dashboard.format_days_ago

# 9Б.2.1: курс ЦБ — переиспользуем готовую функцию app/templating.py.
# Она открывает свою сессию SessionLocal и форматирует fetched_at в МСК.
# Импорт лежит здесь, а не на уровне модуля, чтобы не падать в тестах,
# где app.templating может тянуть тяжёлые зависимости (scheduler и пр.).
from app.templating import current_exchange_rate as _current_exchange_rate

templates.env.globals["current_exchange_rate"] = _current_exchange_rate
