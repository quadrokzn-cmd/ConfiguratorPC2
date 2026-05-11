"""Расчёт `printers_mfu.cost_base_rub`.

Логика: минимальная `price` среди активных позиций `supplier_prices`
с `stock_qty > 0` и `category IN ('printer', 'mfu')` для конкретной SKU.
Если в наличии нет ничего — NULL.

Этап 8 слияния (2026-05-08): таблица переименована из `nomenclature` (QT)
в `printers_mfu` (C-PC2 миграция 031). У C-PC2 `supplier_prices` —
универсальная (одна на все категории), связь с компонентом через пару
`(category, component_id)`. Поэтому везде ниже добавлен фильтр
`category IN ('printer', 'mfu')`, чтобы не подменять cost_base записями
из ПК-таблиц (cpus, motherboards, etc.). Колонка `price_rub` (QT) →
`price` (C-PC2); валюта импортированных Stage 6 строк всегда RUB,
дополнительной фильтрации по currency не делаем, чтобы не усложнять.

Функция вызывается:
- агентом 1А-α после `load_price` (после успешной загрузки прайса);
- importer.py обогащения после применения изменений в `attrs_jsonb`
  (на случай если что-то пересеклось — дёшево);
- кнопкой пересчёта в UI (опционально).

Контракт: имя `recompute_cost_base`, режимы — по sku, по nomenclature_id
(идентификатор printers_mfu), по supplier_id, all_rows.
"""

from __future__ import annotations

import logging

from sqlalchemy import text

from shared.db import engine

logger = logging.getLogger(__name__)


_SQL_BY_SKU = """
    UPDATE printers_mfu n
       SET cost_base_rub = sub.min_price
      FROM (
            SELECT MIN(sp.price) AS min_price
              FROM supplier_prices sp
              JOIN printers_mfu n2 ON n2.id = sp.component_id
             WHERE n2.sku = :sku
               AND sp.category IN ('printer', 'mfu')
               AND sp.stock_qty > 0
           ) sub
     WHERE n.sku = :sku
"""

_SQL_BY_NOMENCLATURE_ID = """
    UPDATE printers_mfu n
       SET cost_base_rub = sub.min_price
      FROM (
            SELECT MIN(price) AS min_price
              FROM supplier_prices
             WHERE component_id = :nid
               AND category IN ('printer', 'mfu')
               AND stock_qty > 0
           ) sub
     WHERE n.id = :nid
"""

_SQL_BY_SUPPLIER = """
    UPDATE printers_mfu n
       SET cost_base_rub = sub.min_price
      FROM (
            SELECT sp.component_id,
                   MIN(price) FILTER (WHERE sp.stock_qty > 0) AS min_price
              FROM supplier_prices sp
             WHERE sp.category IN ('printer', 'mfu')
               AND sp.component_id IN (
                       SELECT component_id
                         FROM supplier_prices
                        WHERE supplier_id = :supplier_id
                          AND category IN ('printer', 'mfu')
                   )
          GROUP BY sp.component_id
           ) sub
     WHERE n.id = sub.component_id
"""

_SQL_ALL = """
    UPDATE printers_mfu n
       SET cost_base_rub = sub.min_price
      FROM (
            SELECT component_id,
                   MIN(price) FILTER (WHERE stock_qty > 0) AS min_price
              FROM supplier_prices
             WHERE category IN ('printer', 'mfu')
          GROUP BY component_id
           ) sub
     WHERE n.id = sub.component_id
"""


def recompute_cost_base(
    *,
    sku: str | None = None,
    nomenclature_id: int | None = None,
    supplier_id: int | None = None,
    all_rows: bool = False,
) -> int:
    """Пересчитывает cost_base_rub. Ровно один из аргументов должен быть задан.

    `nomenclature_id` — id строки `printers_mfu` (название аргумента сохранено
    для совместимости с QT-кодом, который вызывал функцию).

    Возвращает число затронутых строк printers_mfu.
    """
    args = [sku is not None, nomenclature_id is not None, supplier_id is not None, all_rows]
    if sum(1 for a in args if a) != 1:
        raise ValueError(
            "Укажите ровно один из: sku=, nomenclature_id=, supplier_id=, all_rows=True"
        )

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
