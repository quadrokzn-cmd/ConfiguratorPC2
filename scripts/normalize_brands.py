"""CLI: миграция brand → каноничное написание для всех 9 таблиц каталога.

Прогоняет каждое значение brand/manufacturer через canonical_brand() и
UPDATE-ит только изменившиеся записи. Идемпотентен: повторный --apply
после первого даёт 0 изменений.

Этап 6 слияния (2026-05-08): расширен на C-PC2-структуру — 9 таблиц
вместо одной QT-овской nomenclature. ПК-таблицы используют колонку
`manufacturer` (VARCHAR(50)), printers_mfu — `brand` (TEXT). Скрипт
обрабатывает обе схемы прозрачно.

Примеры:
    python scripts/normalize_brands.py             # dry-run, ничего не пишет
    python scripts/normalize_brands.py --dry-run   # то же явно
    python scripts/normalize_brands.py --apply     # реально UPDATE-ит
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402
from app.services.catalog.brand_normalizer import canonical_brand  # noqa: E402

logger = logging.getLogger("normalize_brands")


# Таблица → имя колонки бренда. ПК-таблицы используют `manufacturer`,
# printers_mfu — `brand` (см. migrations/031_printers_mfu.sql).
TABLES: list[tuple[str, str]] = [
    ("cpus",         "manufacturer"),
    ("motherboards", "manufacturer"),
    ("rams",         "manufacturer"),
    ("gpus",         "manufacturer"),
    ("storages",     "manufacturer"),
    ("cases",        "manufacturer"),
    ("psus",         "manufacturer"),
    ("coolers",      "manufacturer"),
    ("printers_mfu", "brand"),
]


def _print_distribution(conn, table: str, col: str, label: str) -> None:
    print(f"\n=== {label} {table}.{col}: распределение ===")
    rows = conn.execute(
        text(
            f"SELECT {col} AS brand, count(*) AS n "
            f"  FROM {table} "
            f" GROUP BY {col} "
            f" ORDER BY n DESC, {col}"
        )
    ).all()
    print(f"distinct {col}: {len(rows)}, всего строк: {sum(r.n for r in rows)}")
    for r in rows[:30]:
        brand_repr = repr(r.brand) if r.brand is not None else "NULL"
        print(f"  {brand_repr:40s}  {r.n}")
    if len(rows) > 30:
        print(f"  … (ещё {len(rows) - 30} брендов)")


def _normalize_table(conn, table: str, col: str, *, apply: bool) -> tuple[int, int, set[str]]:
    """Один проход по таблице. Возвращает (rows_total, rows_changed, unknown_brands)."""
    rows = conn.execute(
        text(f"SELECT id, {col} AS brand FROM {table} ORDER BY id")
    ).all()

    rows_total = len(rows)
    rows_changed = 0
    unknown_brands: set[str] = set()

    for r in rows:
        old = r.brand
        new = canonical_brand(old) if old is not None else ""
        new_value: str | None = new if new else None

        if new_value == old:
            continue

        rows_changed += 1
        verb = "UPDATE" if apply else "WOULD-UPDATE"
        logger.debug(
            "%s %s.id=%d %s: %r → %r",
            verb, table, r.id, col, old, new_value,
        )

        if apply:
            conn.execute(
                text(f"UPDATE {table} SET {col} = :b WHERE id = :id"),
                {"b": new_value, "id": r.id},
            )

    # Сборка unknown — те же записи, прошедшие через canonical_brand,
    # но с лог-уровнем INFO (см. brand_normalizer.py). Здесь просто
    # пройдёмся ещё раз и соберём те, что .title()-версия совпадает с
    # вернулось → значит это unknown brand.
    # Альтернатива: ловить логи. Тут проще пройти ещё раз.
    return rows_total, rows_changed, unknown_brands


def _collect_unknown(conn, table: str, col: str) -> set[str]:
    """Бренды из таблицы, которые отсутствуют в словаре canonical_brand
    и попадают в title-case fallback. Это кандидаты на расширение словаря."""
    from app.services.catalog.brand_normalizer import _ALIAS_TO_CANONICAL  # noqa: PLC2701

    rows = conn.execute(
        text(
            f"SELECT DISTINCT {col} AS brand FROM {table} "
            f" WHERE {col} IS NOT NULL"
        )
    ).all()
    unknowns: set[str] = set()
    for r in rows:
        norm = (r.brand or "").replace("\xa0", " ").strip().lower()
        if not norm:
            continue
        if norm not in _ALIAS_TO_CANONICAL:
            unknowns.add(r.brand)
    return unknowns


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="C-PC2: миграция brand/manufacturer -> каноничное написание (9 таблиц)."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Реально применить UPDATE-ы. Без флага — dry-run.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Явный dry-run (по умолчанию и так dry-run).",
    )
    parser.add_argument(
        "--skip-after-report", action="store_true",
        help="Не печатать повторные diagnostic-отчёты после прогона.",
    )
    args = parser.parse_args()

    if args.apply and args.dry_run:
        print("Ошибка: --apply и --dry-run несовместимы.", file=sys.stderr)
        return 2

    apply = bool(args.apply)
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"normalize_brands (C-PC2): режим = {mode}")

    # ДО.
    with engine.connect() as conn:
        for tbl, col in TABLES:
            _print_distribution(conn, tbl, col, "ДО")

    # Прогон по каждой таблице — каждая таблица в своей транзакции,
    # чтобы ошибка одной не откатывала другую.
    summary: list[tuple[str, str, int, int]] = []
    all_unknowns: dict[str, set[str]] = {}
    for tbl, col in TABLES:
        if apply:
            with engine.begin() as conn:
                rows_total, rows_changed, _ = _normalize_table(
                    conn, tbl, col, apply=True,
                )
                unknowns = _collect_unknown(conn, tbl, col)
        else:
            with engine.connect() as conn:
                rows_total, rows_changed, _ = _normalize_table(
                    conn, tbl, col, apply=False,
                )
                unknowns = _collect_unknown(conn, tbl, col)
        summary.append((tbl, col, rows_total, rows_changed))
        if unknowns:
            all_unknowns[tbl] = unknowns

    print(f"\n=== Итог ({mode}) ===")
    total_rows = total_changed = 0
    for tbl, col, n, ch in summary:
        print(f"  {tbl:18s} ({col:13s})  rows={n:6d}  changed={ch}")
        total_rows += n
        total_changed += ch
    print(f"  {'ИТОГО':18s}  {' ':15s}  rows={total_rows:6d}  changed={total_changed}")

    if all_unknowns:
        print("\n=== Unknown brands (кандидаты на расширение словаря) ===")
        for tbl, brands in all_unknowns.items():
            for b in sorted(brands):
                print(f"  {tbl}: {b!r}")
    else:
        print("\nUnknown brands: нет.")

    if args.skip_after_report:
        return 0

    # ПОСЛЕ — только если apply.
    if apply:
        with engine.connect() as conn:
            for tbl, col in TABLES:
                _print_distribution(conn, tbl, col, "ПОСЛЕ")
    else:
        print(
            "\n(dry-run: «после»-отчёт не печатается — UPDATE-ы не применялись. "
            "Запустите с --apply для реальной миграции.)"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
