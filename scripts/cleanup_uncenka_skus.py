"""Чистка существующего каталога от SKU-уценок (мини-этап 9a-uncenka).

Идея: regex-фильтр `is_uncenka` теперь применяется в orchestrator при
загрузке прайсов — но в БД уже накопились уценочные позиции из
прошлых заливок (например, G&G P2022W с подписью «незначительное
повреждение коробки»). Скрипт обходит каталог и помечает их
`is_hidden = TRUE` — физически НЕ удаляет, чтобы:
  - история создания SKU и связанные supplier_prices сохранились;
  - matcher автоматически перестал брать их в кандидаты (фильтр
    `is_hidden = FALSE` в `app/services/auctions/match/repository.py`);
  - конфигуратор тоже отбрасывает is_hidden=TRUE через
    `app/services/configurator/candidates.py`.

Покрывает 9 таблиц:
  - printers_mfu  — основной фокус (ингест аукционов смотрит сюда);
  - cpus / motherboards / rams / gpus / storages / cases / psus / coolers —
    8 ПК-таблиц конфигуратора.

Использование (PowerShell):
    # dry-run по умолчанию, БД не меняется, печатает план:
    python scripts/cleanup_uncenka_skus.py

    # реальное применение:
    python scripts/cleanup_uncenka_skus.py --apply

Идемпотентен: пропускает строки с is_hidden=TRUE.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable

# чтобы запуск из корня репозитория видел пакет `app`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import create_engine, text  # noqa: E402

from app.services.price_loaders.uncenka_filter import is_uncenka  # noqa: E402


_TABLES: list[tuple[str, str]] = [
    # (table_name, name_column) — printers_mfu использует name, ПК-таблицы
    # хранят основное «человеческое» имя в model.
    ("printers_mfu", "name"),
    ("cpus",         "model"),
    ("motherboards", "model"),
    ("rams",         "model"),
    ("gpus",         "model"),
    ("storages",     "model"),
    ("cases",        "model"),
    ("psus",         "model"),
    ("coolers",      "model"),
]

_SAMPLE_LIMIT = 10  # сколько примеров печатать на таблицу


def _connect():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL не задан. Скопируйте .env.example в .env "
            "и проставьте подключение."
        )
    return create_engine(
        db_url, future=True,
        connect_args={"client_encoding": "utf8"},
    )


def _fetch_visible(engine, table: str, name_column: str) -> list:
    """Возвращает список (id, name) для всех строк, где is_hidden=FALSE."""
    sql = text(
        f"SELECT id, {name_column} AS name "
        f"FROM {table} "
        f"WHERE is_hidden = FALSE "
        f"ORDER BY id ASC"
    )
    with engine.connect() as conn:
        return list(conn.execute(sql).all())


def _filter_uncenka(rows: Iterable) -> list:
    return [r for r in rows if is_uncenka(r.name or "")]


def _hide_rows(engine, table: str, ids: list[int]) -> int:
    """UPDATE table SET is_hidden=TRUE WHERE id IN (...) AND is_hidden=FALSE.

    Защита `AND is_hidden=FALSE` гарантирует идемпотентность: повторный
    запуск с тем же набором id ничего не изменит.
    """
    if not ids:
        return 0
    with engine.begin() as conn:
        res = conn.execute(
            text(
                f"UPDATE {table} SET is_hidden = TRUE "
                f"WHERE id = ANY(:ids) AND is_hidden = FALSE"
            ),
            {"ids": ids},
        )
        return res.rowcount or 0


def cleanup(engine, *, apply: bool) -> dict:
    """Главная процедура. Возвращает по таблице:
       {table: {found, hidden, samples: [str, ...]}}.

    apply=False — только отчёт, БД не менялась.
    apply=True  — UPDATE.
    """
    report: dict[str, dict] = {}
    total_found = 0
    total_hidden = 0
    for table, name_col in _TABLES:
        rows = _fetch_visible(engine, table, name_col)
        candidates = _filter_uncenka(rows)
        ids = [int(r.id) for r in candidates]
        samples = [
            f"id={r.id}: {r.name!r}"
            for r in candidates[:_SAMPLE_LIMIT]
        ]
        hidden = 0
        if apply and ids:
            hidden = _hide_rows(engine, table, ids)
        report[table] = {
            "found":   len(candidates),
            "hidden":  hidden,
            "samples": samples,
        }
        total_found  += len(candidates)
        total_hidden += hidden
    report["__total__"] = {
        "found":  total_found,
        "hidden": total_hidden,
    }
    return report


def _print_report(report: dict, *, applied: bool) -> None:
    mode = "APPLY" if applied else "DRY-RUN"
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = report.get("__total__", {})
    print(f"[{mode}] cleanup_uncenka_skus.py  ({today})")
    print(f"[{mode}] Всего кандидатов: {total.get('found', 0)}")
    if applied:
        print(f"[{mode}] Помечено is_hidden=TRUE: {total.get('hidden', 0)}")
    print()
    print(f"{'Таблица':<14} {'Найдено':>8} {'Помечено':>10}")
    print("-" * 36)
    by_table_total: Counter = Counter()
    for table, _name_col in _TABLES:
        info = report.get(table, {})
        found = info.get("found", 0)
        hidden = info.get("hidden", 0)
        print(f"{table:<14} {found:>8} {hidden:>10}")
        by_table_total[table] = found
    print()

    # Примеры по таблицам, где что-то найдено.
    for table, _name_col in _TABLES:
        info = report.get(table, {})
        if not info.get("samples"):
            continue
        print(f"-- Примеры из {table} (первые {len(info['samples'])} из {info['found']})")
        for s in info["samples"]:
            print(f"   {s}")
        print()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description="Помечает уценочные/повреждённые SKU is_hidden=TRUE."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Реально записать is_hidden=TRUE. Без флага — dry-run.",
    )
    args = parser.parse_args()

    engine = _connect()
    try:
        report = cleanup(engine, apply=args.apply)
    finally:
        engine.dispose()

    _print_report(report, applied=args.apply)
    total = report.get("__total__", {})
    if not args.apply and total.get("found", 0) > 500:
        print(
            "ВНИМАНИЕ: найдено больше 500 кандидатов. "
            "Сверьте список перед --apply."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
