# Пакет автозагрузок прайсов поставщиков (этап 12.3, начало блока 12.x).
#
# Структура:
#   base.py        — BaseAutoFetcher + регистр @register_fetcher.
#   runner.py      — оркестратор: лочит частые ручные вызовы, апдейтит
#                     auto_price_loads / auto_price_load_runs, отсылает
#                     ошибки в Sentry.
#   fetchers/      — реализации каналов:
#                     fetchers/treolan.py     — REST API + JWT (12.3).
#                     fetchers/ocs_imap.py    — IMAP (12.1).
#                     fetchers/merlion_imap.py — IMAP, ZIP-вложение (12.1).
#                     fetchers/netlab_http.py — прямая HTTP-ссылка (12.2).
#                     В будущем: fetchers/resurs_media.py (12.4),
#                     fetchers/green_place.py (12.4).
#
# Подключение в APScheduler — в portal/scheduler.py: ежедневный cron
# 04:00 МСК (после бекапа в 03:00) тянет run_auto_load() по всем
# slug'ам с enabled=TRUE.

from __future__ import annotations

# Импорт fetchers/* регистрирует классы через @register_fetcher.
# Делаем это здесь, а не лениво в runner — иначе импорт runner.py
# в тестах monkeypatch'нул бы не зарегистрированный fetcher.
from app.services.auto_price import base as _base  # noqa: F401
from app.services.auto_price.fetchers import treolan as _treolan  # noqa: F401
# 12.1: IMAP-канал. Импорты регистрируют OCSImapFetcher и MerlionImapFetcher
# через @register_fetcher.
from app.services.auto_price.fetchers import ocs_imap as _ocs_imap  # noqa: F401
from app.services.auto_price.fetchers import merlion_imap as _merlion_imap  # noqa: F401
# 12.2: HTTP-канал Netlab (прямая ссылка → ZIP с DealerD.xlsx).
from app.services.auto_price.fetchers import netlab_http as _netlab_http  # noqa: F401

__all__ = ["base"]
