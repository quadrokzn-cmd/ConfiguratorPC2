# Подбор похожих кандидатов для /admin/mapping.
#
# Когда админ разбирает запись из unmapped_supplier_items, ему нужны
# варианты «на что это могло бы объединиться». Берём токены из raw_name
# и ищем по таблице соответствующей категории — с той же нормализацией,
# что использует fuzzy_lookup для запросов менеджеров. Это даёт
# одинаковое поведение в двух разных контекстах.

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.enrichment.base import ALLOWED_TABLES, CATEGORY_TO_TABLE
from app.services.nlu.fuzzy_lookup import (
    normalize_query,
    rerank_by_exact_match,
)


def _table_for(category: str) -> str:
    if category not in CATEGORY_TO_TABLE:
        raise ValueError(f"Неизвестная категория: {category!r}")
    table = CATEGORY_TO_TABLE[category]
    if table not in ALLOWED_TABLES:
        raise RuntimeError(f"Таблица {table} вне whitelist")
    return table


def find_candidates(
    session: Session,
    *,
    category: str,
    raw_name: str,
    brand: str | None = None,
    exclude_id: int | None = None,
    limit: int = 10,
) -> list[dict]:
    """Возвращает список кандидатов по убыванию релевантности:
    1) сортировка — минимальная цена (NULL в конец, уже задано БД);
    2) rerank — точное совпадение номера модели поднимает вверх;
    3) exclude_id — обычно это скелет, созданный для этой же строки,
       его не показываем (иначе админ объединит запись саму с собой).
    """
    tokens = normalize_query(raw_name, category=category)
    table = _table_for(category)

    # Если нет значимых токенов — возвращаем первые 10 компонентов
    # этой категории по цене. Это хуже, чем пустой список, — админ
    # хотя бы увидит ассортимент.
    if not tokens:
        sql = (
            f"SELECT c.id, c.model, c.sku, c.manufacturer, c.gtin, "
            f"       MIN(sp.price) FILTER (WHERE sp.stock_qty > 0) AS min_price "
            f"FROM {table} c "
            f"LEFT JOIN supplier_prices sp "
            f"  ON sp.category = :cat AND sp.component_id = c.id "
            f"{'WHERE c.id <> :exclude_id ' if exclude_id else ''}"
            f"GROUP BY c.id, c.model, c.sku, c.manufacturer, c.gtin "
            f"ORDER BY min_price NULLS LAST, c.id "
            f"LIMIT {int(limit)}"
        )
        params: dict = {"cat": category}
        if exclude_id:
            params["exclude_id"] = exclude_id
        rows = session.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]

    # Основной путь: все токены должны встречаться в model.
    where_parts: list[str] = []
    params: dict[str, object] = {"cat": category}
    for i, tok in enumerate(tokens):
        key = f"tok{i}"
        where_parts.append(f"UPPER(c.model) LIKE :{key}")
        params[key] = f"%{tok}%"
    if exclude_id:
        where_parts.append("c.id <> :exclude_id")
        params["exclude_id"] = exclude_id
    where_sql = " AND ".join(where_parts)

    sql = (
        f"SELECT c.id, c.model, c.sku, c.manufacturer, c.gtin, "
        f"       MIN(sp.price) FILTER (WHERE sp.stock_qty > 0) AS min_price "
        f"FROM {table} c "
        f"LEFT JOIN supplier_prices sp "
        f"  ON sp.category = :cat AND sp.component_id = c.id "
        f"WHERE {where_sql} "
        f"GROUP BY c.id, c.model, c.sku, c.manufacturer, c.gtin "
        f"ORDER BY min_price NULLS LAST, c.id "
        f"LIMIT {int(limit)}"
    )
    rows = [dict(r) for r in session.execute(text(sql), params).mappings().all()]
    if not rows:
        return []

    rows = rerank_by_exact_match(rows, query_upper=(raw_name or "").upper())

    # Если бренд известен — мягко поднимаем кандидатов с совпадающим
    # manufacturer в начало (стабильная сортировка сохранит внутренний порядок).
    if brand:
        brand_up = brand.upper()
        rows.sort(
            key=lambda r: 0 if (r.get("manufacturer") or "").upper() == brand_up else 1
        )
    return rows
