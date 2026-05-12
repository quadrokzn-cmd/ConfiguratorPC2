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
#   python -m scripts.resurs_media_bootstrap_catalog \
#       --env-file .env.local.prod.resurs.v1 --allow-prod
#
# Флаги:
#   --env-file PATH  загрузить переменные окружения из указанного файла
#                    (через python-dotenv); все RESURS_MEDIA_* и
#                    DATABASE_URL читаются из этого файла. Без флага
#                    используется обычный .env в корне репо.
#   --force          переписывает уже заполненную таблицу
#                    resurs_media_catalog. По умолчанию отказывает.
#   --allow-prod     разрешить запуск против prod-URL (без 'test' в
#                    WSDL). Без флага — отказывает (exit 2). С флагом —
#                    печатает WARNING и спрашивает YES в stdin.
#
# На test-стенде ~25 729 позиций, ~15 сек. На prod — заранее не знаем;
# если попадём в rate-limit (Result=3), fetcher сам ждёт и retry'ит
# один раз (см. ResursMediaApiFetcher._call_with_rate_limit). После
# повторного Result=3 — RuntimeError, скрипт упадёт; запускающий
# подождёт минуту и попробует ещё раз.

from __future__ import annotations

import argparse
import logging
import os
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


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("resurs_media_bootstrap_catalog")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Разовая закачка полного каталога «Ресурс Медиа» через "
            "GetMaterialData без параметров. Идемпотентно (требует --force, "
            "если таблица не пуста)."
        ),
    )
    parser.add_argument(
        "--env-file",
        type=str,
        default=None,
        help=(
            "Путь к файлу окружения (dotenv). Если задан — он "
            "перекрывает обычный .env в корне репо. Удобно для prod-"
            "bootstrap'а: положить prod-кред в .env.local.prod.resurs.v1, "
            "пробросить через этот флаг, не тронув dev-окружение."
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
    parser.add_argument(
        "--allow-prod",
        action="store_true",
        help=(
            "Разрешить запуск против prod-URL (в WSDL нет 'test'). "
            "Дополнительно запросит подтверждение YES в stdin. "
            "Без флага — exit 2."
        ),
    )
    args = parser.parse_args(argv)

    # 1) Сначала грузим окружение (важно: ДО импортов shared.db и
    #    fetcher'а — они читают DATABASE_URL и RESURS_MEDIA_* в момент
    #    импорта/конструирования).
    from dotenv import load_dotenv  # noqa: E402  (lazy для тестируемости)
    if args.env_file:
        env_path = Path(args.env_file)
        if not env_path.exists():
            print(
                f"ERROR: --env-file путь не существует: {env_path}",
                file=sys.stderr,
            )
            return 2
        load_dotenv(env_path, override=True)
        logger.info("Загружен env-файл: %s", env_path)
    else:
        load_dotenv()

    # 2) Sanity-check: prod-URL без --allow-prod — отказываем.
    from scripts._resurs_media_safety import check_prod_safety  # noqa: E402
    wsdl_url = (os.environ.get("RESURS_MEDIA_WSDL_URL") or "").strip()
    check_prod_safety(wsdl_url, args.allow_prod)

    # 3) Тяжёлые импорты — после load_dotenv, чтобы shared.config поймал
    #    свежий DATABASE_URL из --env-file.
    from sqlalchemy import text  # noqa: E402
    from portal.services.configurator.auto_price.fetchers.resurs_media import (  # noqa: E402
        ResursMediaApiFetcher,
    )
    from portal.services.configurator.auto_price.resurs_media_catalog import (  # noqa: E402
        upsert_catalog,
    )
    from shared.db import engine  # noqa: E402

    def _is_catalog_empty() -> bool:
        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT COUNT(*) AS n FROM resurs_media_catalog")
            ).first()
        return int(row.n if row else 0) == 0

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
