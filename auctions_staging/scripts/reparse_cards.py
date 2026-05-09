"""Перепарсинг сохранённых `tenders.raw_html` без повторного запроса к zakupki.

Зачем: после фикса `card_parser._parse_items` (диагностика Волны 2 — мини-фикс
парсеров, 2026-05-07) нужно обновить уже сохранённые `tender_items` —
в частности, заполнить `nmck_per_unit` для multi-position лотов, у которых
`derive_single_position_nmck` ничего не делал.

Идемпотентен: повторный запуск даст те же цифры. Использует уже существующий
`upsert_tender` (full DELETE+INSERT для tender_items в одной транзакции),
поэтому не оставляет «полу-обновлённого» состояния. Флаги пересчитываются
на свежих items через `compute_flags`.

Использование (PowerShell):
    $env:DATABASE_URL_LOCAL = "postgresql+psycopg2://postgres@localhost:5432/quadrotech"
    python scripts/reparse_cards.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402

from app.modules.auctions.ingest.card_parser import parse_card  # noqa: E402
from app.modules.auctions.ingest.filters import compute_flags  # noqa: E402
from app.modules.auctions.ingest.repository import (  # noqa: E402
    load_settings,
    upsert_tender,
)


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_LOCAL")
    if not url:
        print("ERROR: DATABASE_URL (or DATABASE_URL_LOCAL) is not set", file=sys.stderr)
        sys.exit(2)
    return url


def _stats(engine, label: str) -> dict[str, int]:
    sql = text(
        """
        SELECT
            count(*)                                                                     AS items_total,
            count(*) FILTER (WHERE nmck_per_unit IS NULL)                                AS items_no_per_unit,
            count(*) FILTER (WHERE nmck_per_unit IS NOT NULL)                            AS items_with_per_unit
        FROM tender_items
        """
    )
    sql_multi = text(
        """
        WITH cnt AS (
            SELECT tender_id, count(*) AS n FROM tender_items GROUP BY tender_id
        )
        SELECT
            count(DISTINCT ti.tender_id)                                          AS multi_tenders,
            count(*)                                                              AS multi_items,
            count(*) FILTER (WHERE ti.nmck_per_unit IS NULL)                      AS multi_no_per_unit
        FROM tender_items ti JOIN cnt ON cnt.tender_id = ti.tender_id
        WHERE cnt.n > 1
        """
    )
    with engine.connect() as conn:
        row = conn.execute(sql).first()
        m = conn.execute(sql_multi).first()
    out = {
        "items_total":         row.items_total,
        "items_with_per_unit": row.items_with_per_unit,
        "items_no_per_unit":   row.items_no_per_unit,
        "multi_tenders":       m.multi_tenders,
        "multi_items":         m.multi_items,
        "multi_no_per_unit":   m.multi_no_per_unit,
    }
    print(f"[{label}]", out)
    return out


def main() -> int:
    url = _resolve_database_url()
    engine = create_engine(url, future=True)

    before = _stats(engine, "before")
    settings = load_settings(engine)

    sql_select = text(
        "SELECT reg_number, url, raw_html FROM tenders "
        "WHERE raw_html IS NOT NULL AND length(raw_html) > 0 "
        "ORDER BY reg_number"
    )
    with engine.connect() as conn:
        rows = list(conn.execute(sql_select))

    total = len(rows)
    parsed_ok = 0
    failed = 0
    inserted = 0
    updated = 0
    print(f"reparse plan: {total} tenders with raw_html")

    for r in rows:
        try:
            card = parse_card(r.reg_number, r.url, r.raw_html)
        except Exception as exc:
            failed += 1
            print(f"  parse fail {r.reg_number}: {exc}", file=sys.stderr)
            continue
        flags = compute_flags(card, settings)
        try:
            result = upsert_tender(engine, card, flags)
        except Exception as exc:
            failed += 1
            print(f"  upsert fail {r.reg_number}: {exc}", file=sys.stderr)
            continue
        parsed_ok += 1
        if result.inserted:
            inserted += 1
        else:
            updated += 1

    print()
    print(f"reparse done: {parsed_ok}/{total} parsed; updated={updated}, inserted={inserted}, failed={failed}")
    print()
    after = _stats(engine, "after")

    delta = after["items_with_per_unit"] - before["items_with_per_unit"]
    print()
    print(f"items with nmck_per_unit: before={before['items_with_per_unit']} -> after={after['items_with_per_unit']} (Δ={delta:+})")
    print(
        f"multi-position items without per-unit: "
        f"before={before['multi_no_per_unit']} -> after={after['multi_no_per_unit']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
