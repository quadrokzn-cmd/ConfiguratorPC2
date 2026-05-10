"""Слой выборок для UI справочника `/nomenclature` (после Этапа 9 — `/auctions/...`).

Запросы построены так, чтобы один SELECT отдавал всё, что нужно для строки
таблицы: цена-минимум по поставщикам с наличием, суммарное наличие, имя
поставщика-донора. Это удобно показывать менеджеру без N+1.

Этап 8 слияния (2026-05-08): таблица переименована `nomenclature` →
`printers_mfu`. C-PC2 `supplier_prices` универсальная — фильтруем по
`category IN ('printer', 'mfu')`, иначе попадут ПК-цены. `nomenclature_id`
(QT-овский) → `component_id` (C-PC2).
"""

from __future__ import annotations

from sqlalchemy import text

from app.database import engine
from app.services.auctions.catalog.enrichment.schema import PRINTER_MFU_ATTRS

LIST_LIMIT_DEFAULT = 200
PAGE_SIZE_DEFAULT = 50


_SQL_LIST = """
    WITH price_summary AS (
        SELECT sp.component_id AS nomenclature_id,
               MIN(sp.price) FILTER (WHERE sp.stock_qty > 0) AS min_price,
               SUM(sp.stock_qty)                              AS total_stock,
               (
                 SELECT s.name
                   FROM supplier_prices sp2
                   JOIN suppliers s ON s.id = sp2.supplier_id
                  WHERE sp2.component_id = sp.component_id
                    AND sp2.category IN ('printer', 'mfu')
                    AND sp2.stock_qty > 0
                  ORDER BY sp2.price ASC
                  LIMIT 1
               ) AS cheapest_supplier
          FROM supplier_prices sp
         WHERE sp.category IN ('printer', 'mfu')
         GROUP BY sp.component_id
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
      FROM printers_mfu n
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
     LIMIT :limit OFFSET :offset
"""


_SQL_COUNT = """
    SELECT COUNT(*)
      FROM printers_mfu n
     WHERE (:category IS NULL OR n.category = :category)
       AND (:brand    IS NULL OR n.brand    = :brand)
       AND (
            :search IS NULL
            OR n.name ILIKE :search_like
            OR n.mpn  ILIKE :search_like
            OR n.sku  ILIKE :search_like
           )
"""


def list_nomenclature(
    *,
    category: str | None = None,
    brand: str | None = None,
    search: str | None = None,
    limit: int = LIST_LIMIT_DEFAULT,
    offset: int = 0,
) -> list[dict]:
    params = {
        "category":    category,
        "brand":       brand,
        "search":      search,
        "search_like": f"%{search}%" if search else None,
        "limit":       limit,
        "offset":      offset,
    }
    with engine.connect() as conn:
        rows = conn.execute(text(_SQL_LIST), params).mappings().all()
    return [dict(r) for r in rows]


def list_nomenclature_paginated(
    *,
    category: str | None = None,
    brand: str | None = None,
    search: str | None = None,
    page: int = 1,
    per_page: int = PAGE_SIZE_DEFAULT,
) -> dict:
    """Постраничный список SKU + общий count под текущие фильтры.

    Возвращает {'rows': [...], 'total': N, 'page': P, 'per_page': PP,
                'total_pages': TP}. Page нумеруется с 1; если запрошена
    страница больше total_pages — rows придёт пустым (UI покажет «нет
    данных»), но page/total_pages останутся валидными для пагинатора."""
    page = max(1, int(page))
    per_page = max(1, int(per_page))
    count_params = {
        "category":    category,
        "brand":       brand,
        "search":      search,
        "search_like": f"%{search}%" if search else None,
    }
    with engine.connect() as conn:
        total = int(conn.execute(text(_SQL_COUNT), count_params).scalar() or 0)
    total_pages = max(1, (total + per_page - 1) // per_page)
    offset = (page - 1) * per_page
    rows = list_nomenclature(
        category=category, brand=brand, search=search,
        limit=per_page, offset=offset,
    )
    return {
        "rows":        rows,
        "total":       total,
        "page":        page,
        "per_page":    per_page,
        "total_pages": total_pages,
    }


def list_brands() -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT DISTINCT brand FROM printers_mfu WHERE brand IS NOT NULL ORDER BY brand")
        ).all()
    return [r[0] for r in rows]


def list_categories() -> list[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT DISTINCT category FROM printers_mfu "
                "WHERE category IS NOT NULL ORDER BY category"
            )
        ).all()
    return [r[0] for r in rows]


def list_ktru_active() -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT code, note FROM ktru_watchlist WHERE is_active = TRUE ORDER BY code")
        ).mappings().all()
    return [dict(r) for r in rows]


def get_by_id(nomenclature_id: int) -> dict | None:
    """Возвращает строку printers_mfu + cheapest_supplier (поставщик с
    самой низкой ценой при stock_qty>0 для category IN ('printer','mfu')).
    Используется модалкой SKU details на карточке лота (9a-fixes-3 #2)."""
    with engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT n.*,
                       (
                         SELECT s.name
                           FROM supplier_prices sp
                           JOIN suppliers s ON s.id = sp.supplier_id
                          WHERE sp.component_id = n.id
                            AND sp.category IN ('printer', 'mfu')
                            AND sp.stock_qty > 0
                          ORDER BY sp.price ASC
                          LIMIT 1
                       ) AS cheapest_supplier
                  FROM printers_mfu n
                 WHERE n.id = :id
                """
            ),
            {"id": nomenclature_id},
        ).mappings().first()
    return dict(row) if row else None


def update_cost_base_manual(nomenclature_id: int, cost_base_rub) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE printers_mfu SET cost_base_rub = :v WHERE id = :id"
            ),
            {"v": cost_base_rub, "id": nomenclature_id},
        )


def update_attrs_manual(nomenclature_id: int, attrs: dict) -> None:
    """Запись правок атрибутов из UI: ставим `attrs_source='manual'`."""
    import json
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                UPDATE printers_mfu
                   SET attrs_jsonb       = CAST(:attrs AS JSONB),
                       attrs_source      = 'manual',
                       attrs_updated_at  = now()
                 WHERE id = :id
                """
            ),
            {"attrs": json.dumps(attrs, ensure_ascii=False), "id": nomenclature_id},
        )


def update_ktru_codes(nomenclature_id: int, ktru_codes: list[str]) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE printers_mfu SET ktru_codes_array = :codes WHERE id = :id"),
            {"codes": ktru_codes, "id": nomenclature_id},
        )


def get_attribute_schema() -> dict[str, str]:
    return dict(PRINTER_MFU_ATTRS)
