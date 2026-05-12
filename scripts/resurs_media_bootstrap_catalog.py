# Разовый bootstrap локального каталога «Ресурс Медиа».
#
# Зачем. По spec API_РМ_v7.5 рекомендуется однократно скачать весь
# каталог через GetMaterialData (одним вызовом без параметров — РМ
# вернёт все позиции) и держать его локально (таблица
# resurs_media_catalog). Дальше regular GetMaterialData зовут только
# по дельте — новые + stale > 30 дней. См. мини-этап 2026-05-12
# (plans/2026-04-23-platforma-i-aukciony.md).
#
# Запуск:
#   python -m scripts.resurs_media_bootstrap_catalog
#   python -m scripts.resurs_media_bootstrap_catalog --force
#
# По умолчанию скрипт идемпотентен — повторный запуск с непустой
# таблицей resurs_media_catalog отказывает. --force переписывает
# существующие строки (synced_at обновится у всех).
#
# На test-стенде ~25 729 позиций, ~15 сек. На prod — заранее не знаем;
# если попадём в rate-limit (Result=3), fetcher сам ждёт и retry'ит
# один раз (см. ResursMediaApiFetcher._call_with_rate_limit). После
# повторного Result=3 — RuntimeError, скрипт упадёт; запускающий
# подождёт минуту и попробует ещё раз.

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

# Чтобы `python scripts/resurs_media_bootstrap_catalog.py` работал
# в том же стиле, что и apply_migrations.py.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import text  # noqa: E402

from portal.services.configurator.auto_price.fetchers.resurs_media import (  # noqa: E402
    ResursMediaApiFetcher,
)
from portal.services.configurator.auto_price.resurs_media_catalog import (  # noqa: E402
    upsert_catalog,
)
from shared.db import engine  # noqa: E402


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("resurs_media_bootstrap_catalog")


def _is_catalog_empty() -> bool:
    with engine.begin() as conn:
        row = conn.execute(
            text("SELECT COUNT(*) AS n FROM resurs_media_catalog")
        ).first()
    return int(row.n if row else 0) == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Разовая закачка полного каталога «Ресурс Медиа» через "
            "GetMaterialData без параметров. Идемпотентно (требует --force, "
            "если таблица не пуста)."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Запустить даже если resurs_media_catalog не пуст. "
            "Все позиции будут перезаписаны (synced_at обновится у всех)."
        ),
    )
    args = parser.parse_args(argv)

    if not _is_catalog_empty() and not args.force:
        print(
            "resurs_media_catalog уже содержит данные. Запустите с --force, "
            "если действительно нужно переписать весь каталог. "
            "В обычном режиме обновление идёт через дельту (см. runner).",
            file=sys.stderr,
        )
        return 1

    logger.info("Создаю fetcher и зову GetMaterialData без параметров…")
    fetcher = ResursMediaApiFetcher()
    client = fetcher._get_client()

    started = time.monotonic()
    # Вызов без MaterialID_Tab = «весь каталог» по spec v7.5. _call_with_rate_limit
    # сам обработает Result=3 (rate-limit) — sleep + один retry; на повторном
    # Result=3 поднимет RuntimeError, чтобы скрипт упал и можно было руками
    # перезапустить позже.
    response = fetcher._call_with_rate_limit(
        client,
        "GetMaterialData",
        WithCharacteristics=False,
        WithBarCodes=False,
        WithCertificates=False,
        WithImages=False,
    )
    elapsed = time.monotonic() - started
    logger.info("GetMaterialData завершён за %.1f сек.", elapsed)

    logger.info("Запускаю upsert_catalog…")
    counters = upsert_catalog(engine, response)
    logger.info(
        "Готово: inserted=%d updated=%d errors=%d",
        counters["inserted"], counters["updated"], counters["errors"],
    )

    if counters["inserted"] == 0 and counters["updated"] == 0:
        print(
            "GetMaterialData вернул 0 позиций. Это подозрительно — "
            "проверьте кредентиалы и доступность стенда РМ.",
            file=sys.stderr,
        )
        return 2

    return 0


if __name__ == "__main__":
    sys.exit(main())
