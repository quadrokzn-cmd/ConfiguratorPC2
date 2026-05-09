"""Слой выборок для UI справочника `/nomenclature`.

Запросы построены так, чтобы один SELECT отдавал всё, что нужно для строки
таблицы: цена-минимум по поставщикам с наличием, суммарное наличие, имя
поставщика-донора. Это удобно показывать менеджеру без N+1.
"""

from __future__ import annotations

from sqlalchemy import text

from app.core.db import get_engine
from app.modules.auctions.catalog.enrichment.schema import PRINTER_MFU_ATTRS

LIST_LIMIT_DEFAULT = 200


_SQL_LIST = """
    WITH price_summary AS (
        SELECT sp.nomenclature_id,
               MIN(sp.price_rub) FILTER (WHERE sp.stock_qty > 0) AS min_price,
               SUM(sp.stock_qty)                                AS total_stock,
               (
                 SELECT s.name
                   FROM supplier_prices sp2
                   JOIN suppliers s ON s.id = sp2.supplier_id
                  WHERE sp2.nomenclature_id = sp.nomenclature_id
                    AND sp2.stock_qty > 0
                  ORDER BY sp2.price_rub ASC
                  LIMIT 1
               ) AS cheapest_supplier
          FROM supplier_prices sp
         GROUP BY sp.nomenclature_id
    )
    SELECT n.id,
           n.sku,
           n.mpn,
           n.brand,
           n.name,
           n.category,
           n.ktru_codes_array,
           n.attrs_jsonb,
           n.attrs_source,
           n.cost_base_rub,
           ps.min_price          AS min_price_rub,
           ps.total_stock        AS total_stock,
           ps.cheapest_supplier  AS cheapest_supplier
      FROM nomenclature n
      LEFT JOIN price_summary ps ON ps.nomenclature_id = n.id
     WHERE (:category IS NULL OR n.category = :category)
       AND (:brand    IS NULL OR n.brand    = :brand)
       AND (
            :search IS NULL
            OR n.name ILIKE :search_like
            OR n.mpn  ILIKE :search_like
            OR n.sku  ILIKE :search_like
           )
     ORDER BY n.brand NULLS LAST, n.name
     LIMIT :limit
"""


def list_nomenclature(
    *,
    category: str | None = None,
    brand: str | None = None,
    search: str | None = None,
    limit: int = LIST_LIMIT_DEFAULT,
) -> list[dict]:
    engine = get_engine()
    params = {
        "category":    category,
        "brand":       brand,
        "search":      search,
        "search_like": f"%{search}%" if search else None,
        "limit":       limit,
    }
    with engine.connect() as conn:
        rows = conn.execute(text(_SQL_LIST), params).mappings().all()
    return [dict(r) for r in rows]


def list_brands() -> list[str]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT brand FROM nomenclature WHERE brand IS NOT NULL ORDER BY brand")
        ).all()
    return [r[0] for r in rows]


def list_categories() -> list[str]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT category FROM nomenclature "
                "WHERE category IS NOT NULL ORDER BY category"
            )
        ).all()
    return [r[0] for r in rows]


def list_ktru_active() -> list[dict]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT code, note FROM ktru_watchlist WHERE is_active = TRUE ORDER BY code")
        ).mappings().all()
    return [dict(r) for r in rows]


def get_by_id(nomenclature_id: int) -> dict | None:
    engine = get_engine()
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT * FROM nomenclature WHERE id = :id"),
            {"id": nomenclature_id},
        ).mappings().first()
    return dict(row) if row else None


def update_cost_base_manual(nomenclature_id: int, cost_base_rub) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE nomenclature SET cost_base_rub = :v WHERE id = :id"
            ),
            {"v": cost_base_rub, "id": nomenclature_id},
        )


def update_attrs_manual(nomenclature_id: int, attrs: dict) -> None:
    """Запись правок атрибутов из UI: ставим `attrs_source='manual'`."""
    import json
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE nomenclature
                   SET attrs_jsonb       = CAST(:attrs AS JSONB),
                       attrs_source      = 'manual',
                       attrs_updated_at  = now()
                 WHERE id = :id
                """
            ),
            {"attrs": json.dumps(attrs, ensure_ascii=False), "id": nomenclature_id},
        )


def update_ktru_codes(nomenclature_id: int, ktru_codes: list[str]) -> None:
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE nomenclature SET ktru_codes_array = :codes WHERE id = :id"),
            {"codes": ktru_codes, "id": nomenclature_id},
        )


def get_attribute_schema() -> dict[str, str]:
    return dict(PRINTER_MFU_ATTRS)
