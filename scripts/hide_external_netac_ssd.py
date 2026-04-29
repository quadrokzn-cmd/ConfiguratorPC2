"""Разовая чистка 4 внешних Netac USB-C SSD в таблице storages (этап 9Г.1).

Контекст
--------
В каталоге висят портативные Netac USB-C SSD (1.8" Z9 / Z Slim, 1TB/2TB),
которые не подходят под схему storages (внешние, USB-C, не SATA/NVMe).
Конфигуратор подбирает только внутренние накопители, поэтому эти 4
позиции просто шумят в каталоге и могут случайно попасть в подбор
через fuzzy-поиск NLU.

Что делает
----------
SELECT id, manufacturer, model FROM storages
WHERE manufacturer ILIKE '%netac%'
  AND (model ILIKE '%USB%' OR model ILIKE '%external%' OR model ILIKE '%portable%')
  AND is_hidden = FALSE;
→ показывает кандидатов, при --apply ставит is_hidden=TRUE.

Идемпотентно: повторный запуск только дополнит чистку, если в каталог
попадут новые внешние Netac (но при появлении большего числа таких
позиций имеет смысл расширить shared/component_filters.is_likely_external_storage
и подключить её в orchestrator — см. docs/enrichment_techdebt.md §2 / §9).

Запуск
------
  Dry-run (по умолчанию):
    python scripts/hide_external_netac_ssd.py

  Реально применить:
    python scripts/hide_external_netac_ssd.py --apply

ВНИМАНИЕ: на проде запускается отдельной ручной операцией админа после
деплоя (через Railway Shell или psql с прод-кредами). Не привязан ни к
APScheduler, ни к скриптам деплоя.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text


def _connect():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL не задан. Проставьте подключение в .env."
        )
    return create_engine(
        db_url, future=True,
        connect_args={"client_encoding": "utf8"},
    )


_FIND_SQL = text(
    "SELECT id, manufacturer, model "
    "FROM storages "
    "WHERE manufacturer ILIKE '%netac%' "
    "  AND (model ILIKE '%USB%' "
    "       OR model ILIKE '%external%' "
    "       OR model ILIKE '%portable%') "
    "  AND is_hidden = FALSE "
    "ORDER BY id ASC"
)


def find_candidates(engine) -> list:
    """Возвращает список кандидатов на скрытие (внешние Netac SSD)."""
    with engine.begin() as conn:
        return conn.execute(_FIND_SQL).all()


def apply_hide(engine, ids: list[int]) -> int:
    """UPDATE storages.is_hidden = TRUE для указанных id. Возвращает rowcount."""
    if not ids:
        return 0
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "UPDATE storages SET is_hidden = TRUE "
                "WHERE id = ANY(:ids) AND is_hidden = FALSE"
            ),
            {"ids": ids},
        )
        return res.rowcount or 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description="Скрывает внешние Netac USB-C SSD в таблице storages."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Реально записать is_hidden=TRUE. Без флага — только dry-run.",
    )
    args = parser.parse_args()

    engine = _connect()
    try:
        rows = find_candidates(engine)
        print(f"Найдено кандидатов: {len(rows)}")
        for r in rows:
            print(f"  id={r.id} | {r.manufacturer} | {r.model}")

        if args.apply:
            ids = [int(r.id) for r in rows]
            updated = apply_hide(engine, ids)
            print(f"Помечено is_hidden=TRUE: {updated}")
        else:
            print("(dry-run; запустите с --apply, чтобы применить)")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
