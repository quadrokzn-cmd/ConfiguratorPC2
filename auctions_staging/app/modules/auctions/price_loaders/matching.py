from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.modules.auctions.price_loaders.models import PriceRow


EXISTING = "existing"
MATCH_BRAND_MPN = "brand_mpn"
MATCH_GTIN = "gtin"
NO_MATCH = "no_match"


@dataclass
class MatchResult:
    source: str
    nomenclature_id: int | None = None


def _find_by_supplier_sku(
    conn: Connection, *, supplier_id: int, supplier_sku: str | None
) -> int | None:
    if not supplier_sku:
        return None
    row = conn.execute(
        text(
            "SELECT nomenclature_id FROM supplier_prices "
            "WHERE supplier_id = :sid AND supplier_sku = :ssku LIMIT 1"
        ),
        {"sid": supplier_id, "ssku": supplier_sku},
    ).first()
    return int(row.nomenclature_id) if row else None


def _find_by_brand_mpn(
    conn: Connection, *, brand: str | None, mpn: str | None
) -> int | None:
    if not mpn:
        return None
    if brand:
        row = conn.execute(
            text(
                "SELECT id FROM nomenclature "
                "WHERE LOWER(brand) = LOWER(:brand) AND mpn = :mpn "
                "ORDER BY id LIMIT 1"
            ),
            {"brand": brand, "mpn": mpn},
        ).first()
    else:
        row = conn.execute(
            text(
                "SELECT id FROM nomenclature "
                "WHERE mpn = :mpn ORDER BY id LIMIT 1"
            ),
            {"mpn": mpn},
        ).first()
    return int(row.id) if row else None


def _find_by_gtin(conn: Connection, *, gtin: str | None) -> int | None:
    if not gtin:
        return None
    row = conn.execute(
        text("SELECT id FROM nomenclature WHERE gtin = :gtin ORDER BY id LIMIT 1"),
        {"gtin": gtin},
    ).first()
    return int(row.id) if row else None


def resolve(conn: Connection, row: PriceRow, *, supplier_id: int) -> MatchResult:
    existing = _find_by_supplier_sku(
        conn, supplier_id=supplier_id, supplier_sku=row.supplier_sku
    )
    if existing is not None:
        return MatchResult(source=EXISTING, nomenclature_id=existing)

    by_bm = _find_by_brand_mpn(conn, brand=row.brand, mpn=row.mpn)
    if by_bm is not None:
        return MatchResult(source=MATCH_BRAND_MPN, nomenclature_id=by_bm)

    by_gtin = _find_by_gtin(conn, gtin=row.gtin)
    if by_gtin is not None:
        return MatchResult(source=MATCH_GTIN, nomenclature_id=by_gtin)

    return MatchResult(source=NO_MATCH)
