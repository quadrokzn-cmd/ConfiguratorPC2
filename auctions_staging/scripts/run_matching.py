"""Прогон матчинга на всей БД.

Использование (PowerShell):
    $env:DATABASE_URL_LOCAL = "postgresql+psycopg2://postgres@localhost:5432/quadrotech"
    python scripts/run_matching.py

Идемпотентен: каждый запуск пересчитывает `matches` для всех релевантных позиций.
Печатает сводную статистику (см. MatchingStats.as_dict).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# чтобы запуск из корня репозитория видел пакет `app`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine  # noqa: E402

from app.modules.auctions.match.service import run_matching  # noqa: E402


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_LOCAL")
    if not url:
        print("ERROR: DATABASE_URL (or DATABASE_URL_LOCAL) is not set", file=sys.stderr)
        sys.exit(2)
    return url


def main() -> int:
    url = _resolve_database_url()
    engine = create_engine(url, future=True)

    stats = run_matching(engine, full_recompute=True)
    print()
    print("=== Matching stats ===")
    print(f"Items total considered:          {stats.items_total}")
    print(f"  with KTRU code:                {stats.items_with_ktru}")
    print(f"  no nmck_per_unit:              {stats.items_no_nmck_per_unit}")
    print(f"  no SKU candidates by KTRU:     {stats.items_no_candidates}")
    print(f"  dropped or no cost_base:       {stats.items_no_cost_or_dropped}")
    print(f"  with primary match:            {stats.items_with_primary}")
    print()
    print(f"Matches inserted:                {stats.matches_inserted}")
    print(f"Matched tenders:                 {stats.matched_tenders}")
    print(f"Margin threshold (settings):     {stats.margin_threshold_pct}%")
    print(f"Tenders passing threshold:       {stats.tenders_passing_margin_threshold}")
    print()
    print(f"Derive: SKU KTRU filled:         {stats.sku_ktru_filled}")
    print(f"Derive: nmck_per_unit derived:   {stats.nmck_per_unit_derived}")
    print()
    if stats.margin_pct_distribution:
        d = stats.margin_pct_distribution
        print("Tender primary-margin% distribution:")
        print(f"  count={d['count']}, min={d['min']}, p25={d['p25']}, median={d['median']}, p75={d['p75']}, max={d['max']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
