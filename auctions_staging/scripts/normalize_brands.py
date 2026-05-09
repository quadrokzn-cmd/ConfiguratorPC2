"""CLI: миграция nomenclature.brand → каноничное написание.

Прогоняет каждый brand из nomenclature через canonical_brand() и UPDATE-ит
только изменившиеся записи. Идемпотентен: повторный --apply после первого
даёт 0 изменений.

До и после правок печатает 3 диагностических отчёта:
  1) Полное распределение brand → count (топ).
  2) Дубли по (mpn, brand) — задвоения SKU при одинаковом написании.
  3) Один MPN под РАЗНЫМИ brand — кандидаты на «настоящие» дубли SKU
     (после нормализации часть из них схлопнется до одного варианта brand,
     остальные — кандидаты для ручного объединения собственником).

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

from app.core.db import get_engine  # noqa: E402
from app.modules.auctions.catalog.brand_normalizer import canonical_brand  # noqa: E402

logger = logging.getLogger("normalize_brands")


SQL_BRAND_DISTRIBUTION = """
    SELECT brand, count(*) AS n
      FROM nomenclature
  GROUP BY brand
  ORDER BY n DESC, brand
"""

SQL_MPN_BRAND_DUPLICATES = """
    SELECT mpn,
           brand,
           count(*)        AS n,
           array_agg(sku ORDER BY sku) AS skus
      FROM nomenclature
     WHERE mpn IS NOT NULL
  GROUP BY mpn, brand
    HAVING count(*) > 1
  ORDER BY n DESC, mpn
"""

SQL_MPN_BRAND_VARIANTS = """
    SELECT mpn,
           count(DISTINCT brand) AS brand_variants,
           array_agg(DISTINCT brand ORDER BY brand) AS brands,
           array_agg(sku   ORDER BY sku)            AS skus
      FROM nomenclature
     WHERE mpn IS NOT NULL
  GROUP BY mpn
    HAVING count(DISTINCT brand) > 1
  ORDER BY brand_variants DESC, mpn
"""


def _print_distribution(conn, label: str) -> None:
    print(f"\n=== {label}: распределение brand (всего различных) ===")
    rows = conn.execute(text(SQL_BRAND_DISTRIBUTION)).all()
    print(f"distinct brand count: {len(rows)}")
    for r in rows:
        brand_repr = repr(r.brand) if r.brand is not None else "NULL"
        print(f"  {brand_repr:40s}  {r.n}")


def _print_mpn_brand_duplicates(conn, label: str) -> None:
    print(f"\n=== {label}: задвоения по (mpn, brand) ===")
    rows = conn.execute(text(SQL_MPN_BRAND_DUPLICATES)).all()
    print(f"pairs (mpn, brand) с count > 1: {len(rows)}")
    for r in rows:
        print(f"  mpn={r.mpn!r:30s}  brand={r.brand!r:25s}  n={r.n}  skus={list(r.skus)}")


def _print_mpn_brand_variants(conn, label: str) -> None:
    print(f"\n=== {label}: один MPN под РАЗНЫМИ brand (кандидаты на дубли SKU) ===")
    rows = conn.execute(text(SQL_MPN_BRAND_VARIANTS)).all()
    print(f"MPN с >1 вариантом brand: {len(rows)}")
    for r in rows:
        print(
            f"  mpn={r.mpn!r:30s}  variants={r.brand_variants}  "
            f"brands={list(r.brands)}  skus={list(r.skus)}"
        )


def _normalize_pass(conn, *, apply: bool) -> tuple[int, int]:
    """Один проход нормализации. Возвращает (rows_total, rows_changed).

    Если apply=False — UPDATE не выполняется, только лог.
    """
    rows = conn.execute(
        text("SELECT id, sku, brand FROM nomenclature ORDER BY id")
    ).all()

    rows_total = len(rows)
    rows_changed = 0

    for r in rows:
        old = r.brand
        new = canonical_brand(old) if old is not None else ""
        new_value: str | None = new if new else None

        if new_value == old:
            continue

        rows_changed += 1
        verb = "UPDATE" if apply else "WOULD-UPDATE"
        logger.info(
            "%s id=%d sku=%s brand: %r → %r", verb, r.id, r.sku, old, new_value,
        )

        if apply:
            conn.execute(
                text("UPDATE nomenclature SET brand = :b WHERE id = :id"),
                {"b": new_value, "id": r.id},
            )

    return rows_total, rows_changed


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Миграция nomenclature.brand -> каноничное написание."
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
    print(f"normalize_brands: режим = {mode}")

    engine = get_engine()

    # До: один общий read-only коннект под три SELECT-а.
    with engine.connect() as conn:
        _print_distribution(conn, "ДО")
        _print_mpn_brand_duplicates(conn, "ДО")
        _print_mpn_brand_variants(conn, "ДО")

    # Сам прогон. На apply — единая транзакция с коммитом; на dry-run — read-only коннект.
    if apply:
        with engine.begin() as conn:
            rows_total, rows_changed = _normalize_pass(conn, apply=True)
    else:
        with engine.connect() as conn:
            rows_total, rows_changed = _normalize_pass(conn, apply=False)

    print(
        f"\nИтог: rows_total={rows_total}, rows_changed={rows_changed}, mode={mode}"
    )

    if args.skip_after_report:
        return 0

    # После: повторим те же три SELECT-а.
    if apply:
        with engine.connect() as conn:
            _print_distribution(conn, "ПОСЛЕ")
            _print_mpn_brand_duplicates(conn, "ПОСЛЕ")
            _print_mpn_brand_variants(conn, "ПОСЛЕ")
    else:
        print(
            "\n(dry-run: «после»-отчёт не печатается — UPDATE-ы не применялись. "
            "Запустите с --apply для реальной миграции.)"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
