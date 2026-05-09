"""Расчёт `nomenclature.cost_base_rub`.

Логика: минимальная `price_rub` среди активных позиций `supplier_prices`
с `stock_qty > 0` для конкретной SKU. Если в наличии нет ничего — NULL.

Функция вызывается:
- агентом 1А-α после `load_price` (после успешной загрузки прайса);
- importer.py обогащения после применения изменений в `attrs_jsonb`
  (на случай если что-то пересеклось — дёшево);
- кнопкой пересчёта в UI (опционально).

Контракт согласован с агентом 1А-α: имя `recompute_cost_base`, два режима —
по nomenclature_id и по supplier_id (после загрузки прайса целиком).
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from app.core.db import get_engine

logger = logging.getLogger(__name__)


_SQL_BY_SKU = """
    UPDATE nomenclature n
       SET cost_base_rub = sub.min_price
      FROM (
            SELECT MIN(price_rub) AS min_price
              FROM supplier_prices sp
              JOIN nomenclature n2 ON n2.id = sp.nomenclature_id
             WHERE n2.sku = :sku AND sp.stock_qty > 0
           ) sub
     WHERE n.sku = :sku
"""

_SQL_BY_NOMENCLATURE_ID = """
    UPDATE nomenclature n
       SET cost_base_rub = sub.min_price
      FROM (
            SELECT MIN(price_rub) AS min_price
              FROM supplier_prices
             WHERE nomenclature_id = :nid AND stock_qty > 0
           ) sub
     WHERE n.id = :nid
"""

_SQL_BY_SUPPLIER = """
    UPDATE nomenclature n
       SET cost_base_rub = sub.min_price
      FROM (
            SELECT sp.nomenclature_id,
                   MIN(price_rub) FILTER (WHERE sp.stock_qty > 0) AS min_price
              FROM supplier_prices sp
             WHERE sp.nomenclature_id IN (
                       SELECT nomenclature_id
                         FROM supplier_prices
                        WHERE supplier_id = :supplier_id
                   )
          GROUP BY sp.nomenclature_id
           ) sub
     WHERE n.id = sub.nomenclature_id
"""

_SQL_ALL = """
    UPDATE nomenclature n
       SET cost_base_rub = sub.min_price
      FROM (
            SELECT nomenclature_id,
                   MIN(price_rub) FILTER (WHERE stock_qty > 0) AS min_price
              FROM supplier_prices
          GROUP BY nomenclature_id
           ) sub
     WHERE n.id = sub.nomenclature_id
"""


def recompute_cost_base(
    *,
    sku: str | None = None,
    nomenclature_id: int | None = None,
    supplier_id: int | None = None,
    all_rows: bool = False,
) -> int:
    """Пересчитывает cost_base_rub. Ровно один из аргументов должен быть задан.

    Возвращает число затронутых строк nomenclature.
    """
    args = [sku is not None, nomenclature_id is not None, supplier_id is not None, all_rows]
    if sum(1 for a in args if a) != 1:
        raise ValueError(
            "Укажите ровно один из: sku=, nomenclature_id=, supplier_id=, all_rows=True"
        )

    engine = get_engine()
    with engine.begin() as conn:
        if sku is not None:
            res = conn.execute(text(_SQL_BY_SKU), {"sku": sku})
        elif nomenclature_id is not None:
            res = conn.execute(text(_SQL_BY_NOMENCLATURE_ID), {"nid": nomenclature_id})
        elif supplier_id is not None:
            res = conn.execute(text(_SQL_BY_SUPPLIER), {"supplier_id": supplier_id})
        else:
            res = conn.execute(text(_SQL_ALL))
        return res.rowcount or 0
