"""Агрегация по тендеру: суммарная маржа primary, средняя `margin_pct`,
флаг `all_positions_covered` (есть primary у каждой позиции лота)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import Engine, text


@dataclass(frozen=True)
class TenderSummary:
    tender_id: str
    items_total: int
    items_with_primary: int
    primary_margin_total_rub: Decimal | None
    primary_margin_pct_avg: Decimal | None
    all_positions_covered: bool


def aggregate_tender(engine: Engine, tender_id: str) -> TenderSummary:
    """Агрегаты по одному тендеру. Считает только по primary."""
    sql_items = text("""
        SELECT count(*) AS items
        FROM tender_items
        WHERE tender_id = :tid AND ktru_code IS NOT NULL AND ktru_code != ''
    """)
    sql_primary = text("""
        SELECT
            count(DISTINCT m.tender_item_id) AS items_with_primary,
            COALESCE(SUM(m.margin_rub * COALESCE(ti.qty, 1)), 0) AS margin_total,
            AVG(m.margin_pct) AS margin_pct_avg
        FROM matches m
        JOIN tender_items ti ON ti.id = m.tender_item_id
        WHERE ti.tender_id = :tid AND m.match_type = 'primary'
    """)
    with engine.connect() as conn:
        items_row = conn.execute(sql_items, {"tid": tender_id}).first()
        prim_row = conn.execute(sql_primary, {"tid": tender_id}).first()

    items = items_row.items if items_row else 0
    items_with_primary = prim_row.items_with_primary if prim_row else 0
    margin_total = (
        Decimal(prim_row.margin_total).quantize(Decimal("0.01"))
        if prim_row and prim_row.margin_total is not None
        else None
    )
    margin_pct_avg = (
        Decimal(prim_row.margin_pct_avg).quantize(Decimal("0.01"))
        if prim_row and prim_row.margin_pct_avg is not None
        else None
    )
    all_covered = items > 0 and items_with_primary == items
    return TenderSummary(
        tender_id=tender_id,
        items_total=items,
        items_with_primary=items_with_primary,
        primary_margin_total_rub=margin_total,
        primary_margin_pct_avg=margin_pct_avg,
        all_positions_covered=all_covered,
    )


def margin_threshold_pct(engine: Engine) -> Decimal:
    """Читает `settings.margin_threshold_pct` (дефолт 15)."""
    sql = text("SELECT value FROM settings WHERE key = 'margin_threshold_pct'")
    with engine.connect() as conn:
        row = conn.execute(sql).first()
    if not row:
        return Decimal("15")
    try:
        return Decimal(row.value)
    except Exception:  # pragma: no cover
        return Decimal("15")
