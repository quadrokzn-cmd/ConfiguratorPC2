from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.core.db import get_engine
from app.modules.auctions.catalog.cost_base import recompute_cost_base
from app.modules.auctions.price_loaders.base import BasePriceLoader
from app.modules.auctions.price_loaders.matching import (
    EXISTING,
    MATCH_BRAND_MPN,
    MATCH_GTIN,
    NO_MATCH,
    MatchResult,
    resolve,
)
from app.modules.auctions.price_loaders.models import PriceRow

logger = logging.getLogger(__name__)


def _build_sku(row: PriceRow) -> str:
    if row.brand and row.mpn:
        return f"{row.brand.strip().lower()}:{row.mpn.strip()}"
    if row.mpn:
        return row.mpn.strip()
    if row.gtin:
        return f"gtin:{row.gtin.strip()}"
    return f"raw:{row.supplier_sku.strip()}"


@dataclass
class Counters:
    total_rows: int = 0
    processed: int = 0
    matched: int = 0
    inserted: int = 0
    skipped: int = 0
    errors: int = 0
    by_source: dict[str, int] = field(default_factory=dict)


def _get_supplier(conn: Connection, supplier_code: str) -> tuple[int, str]:
    row = conn.execute(
        text("SELECT id, name FROM suppliers WHERE code = :c LIMIT 1"),
        {"c": supplier_code},
    ).first()
    if row is None:
        raise ValueError(
            f"Поставщик с code='{supplier_code}' не найден в таблице suppliers. "
            f"Сначала прогоните миграции (migrations/0002_catalog.sql)."
        )
    return int(row.id), str(row.name)


def _ensure_unique_sku(conn: Connection, base_sku: str) -> str:
    """Возвращает sku, гарантированно свободный в nomenclature.
    При коллизии (редкий случай — у двух поставщиков один и тот же brand:mpn,
    но мы на этой стадии уже идентифицировали запись в matching) добавляет суффикс."""
    sku = base_sku
    n = 0
    while True:
        row = conn.execute(
            text("SELECT 1 FROM nomenclature WHERE sku = :s LIMIT 1"),
            {"s": sku},
        ).first()
        if row is None:
            return sku
        n += 1
        sku = f"{base_sku}#{n}"


def _insert_nomenclature(conn: Connection, row: PriceRow) -> int:
    sku = _ensure_unique_sku(conn, _build_sku(row))
    res = conn.execute(
        text(
            "INSERT INTO nomenclature (sku, mpn, gtin, brand, name, category) "
            "VALUES (:sku, :mpn, :gtin, :brand, :name, :category) "
            "RETURNING id"
        ),
        {
            "sku": sku,
            "mpn": row.mpn,
            "gtin": row.gtin,
            "brand": row.brand,
            "name": row.name,
            "category": row.our_category,
        },
    ).first()
    return int(res.id)


def _upsert_supplier_price(
    conn: Connection,
    *,
    supplier_id: int,
    nomenclature_id: int,
    row: PriceRow,
) -> None:
    conn.execute(
        text(
            "INSERT INTO supplier_prices "
            "    (supplier_id, nomenclature_id, supplier_sku, "
            "     price_rub, stock_qty, transit_qty, updated_at) "
            "VALUES "
            "    (:sid, :nid, :ssku, :price, :stock, :transit, NOW()) "
            "ON CONFLICT (supplier_id, nomenclature_id) DO UPDATE SET "
            "    supplier_sku = EXCLUDED.supplier_sku, "
            "    price_rub    = EXCLUDED.price_rub, "
            "    stock_qty    = EXCLUDED.stock_qty, "
            "    transit_qty  = EXCLUDED.transit_qty, "
            "    updated_at   = NOW()"
        ),
        {
            "sid": supplier_id,
            "nid": nomenclature_id,
            "ssku": row.supplier_sku or None,
            "price": row.price,
            "stock": row.stock,
            "transit": row.transit,
        },
    )


def _record_upload(
    conn: Connection,
    *,
    supplier_id: int,
    filename: str,
    counters: Counters,
    uploaded_by: str | None,
) -> tuple[int, str]:
    rows_matched = counters.matched + counters.inserted
    rows_unmatched = counters.skipped + counters.errors

    if counters.errors > 0 and rows_matched == 0:
        status = "failed"
    elif counters.errors > 0 or counters.skipped > 0:
        status = "partial"
    else:
        status = "success"

    notes = (
        f"matched={counters.matched}, inserted={counters.inserted}, "
        f"skipped={counters.skipped}, errors={counters.errors}, "
        f"by_source={counters.by_source}"
    )
    res = conn.execute(
        text(
            "INSERT INTO price_uploads "
            "    (supplier_id, filename, uploaded_by, "
            "     rows_total, rows_matched, rows_unmatched, status, notes) "
            "VALUES "
            "    (:sid, :fn, :ub, :rt, :rm, :ru, :st, :nt) "
            "RETURNING id"
        ),
        {
            "sid": supplier_id,
            "fn": filename,
            "ub": uploaded_by,
            "rt": counters.total_rows,
            "rm": rows_matched,
            "ru": rows_unmatched,
            "st": status,
            "nt": notes,
        },
    ).first()
    return int(res.id), status


def _process_row(
    conn: Connection,
    *,
    supplier_id: int,
    row: PriceRow,
    counters: Counters,
) -> None:
    if row.our_category == "ignore":
        counters.skipped += 1
        return
    if row.currency.upper() != "RUB":
        counters.skipped += 1
        return
    if row.price is None or Decimal(row.price) <= 0:
        counters.skipped += 1
        return

    counters.processed += 1

    res: MatchResult = resolve(conn, row, supplier_id=supplier_id)
    counters.by_source[res.source] = counters.by_source.get(res.source, 0) + 1

    if res.source in (EXISTING, MATCH_BRAND_MPN, MATCH_GTIN):
        nid = res.nomenclature_id
        assert nid is not None
        _upsert_supplier_price(
            conn,
            supplier_id=supplier_id,
            nomenclature_id=nid,
            row=row,
        )
        counters.matched += 1
        return

    if res.source == NO_MATCH:
        nid = _insert_nomenclature(conn, row)
        _upsert_supplier_price(
            conn,
            supplier_id=supplier_id,
            nomenclature_id=nid,
            row=row,
        )
        counters.inserted += 1
        return


def load_price(
    filepath: str,
    *,
    supplier_code: str | None = None,
    loader: BasePriceLoader | None = None,
    uploaded_by: str | None = None,
) -> dict:
    if loader is None:
        if not supplier_code:
            raise ValueError("Нужно передать supplier_code или готовый loader.")
        from app.modules.auctions.price_loaders import get_loader

        loader = get_loader(supplier_code)

    counters = Counters()
    engine = get_engine()
    filename = os.path.basename(filepath)

    with engine.begin() as conn:
        supplier_id, supplier_name = _get_supplier(conn, loader.supplier_code)

        rows_iter: Iterator[PriceRow] = loader.iter_rows(filepath)
        for row in rows_iter:
            counters.total_rows += 1
            sp = conn.begin_nested()
            try:
                _process_row(
                    conn,
                    supplier_id=supplier_id,
                    row=row,
                    counters=counters,
                )
                sp.commit()
            except Exception as exc:
                sp.rollback()
                logger.error(
                    "%s row %s (supplier_sku=%r): error — %s",
                    supplier_name,
                    row.row_number,
                    row.supplier_sku,
                    exc,
                )
                counters.errors += 1

        upload_id, status = _record_upload(
            conn,
            supplier_id=supplier_id,
            filename=filename,
            counters=counters,
            uploaded_by=uploaded_by,
        )

    recompute_cost_base(supplier_id=supplier_id)

    return {
        "supplier_code": loader.supplier_code,
        "supplier_name": supplier_name,
        "filename": filename,
        "total_rows": counters.total_rows,
        "processed": counters.processed,
        "matched": counters.matched,
        "inserted": counters.inserted,
        "skipped": counters.skipped,
        "errors": counters.errors,
        "by_source": dict(counters.by_source),
        "status": status,
        "upload_id": upload_id,
    }
