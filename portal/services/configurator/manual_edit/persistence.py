# Запись ручных правок в БД.
#
# Отличие от app/services/enrichment/persistence.apply_enrichment:
#   - apply_manual_override МОЖЕТ перезаписывать уже заполненные значения;
#   - всегда пишет source='manual', source_url=NULL, confidence=1.0;
#   - возвращает список реально изменённых полей.
#
# Сравнение значений выполняется после нормализации валидатором — то есть
# если в БД "ATX" и в CSV "atx", апдейта не будет.

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text

from portal.services.configurator.enrichment.base import ALLOWED_TABLES, CATEGORY_TO_TABLE
from portal.services.configurator.manual_edit.schema import SOURCE_MANUAL


def _values_equal(a: Any, b: Any) -> bool:
    """Мягкое сравнение значений для разных типов (bool/int/str/list/Decimal)."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        try:
            return Decimal(str(a)) == Decimal(str(b))
        except Exception:
            return False
    if isinstance(a, list) and isinstance(b, list):
        return a == b
    return a == b


def apply_manual_override(
    session,
    category: str,
    component_id: int,
    updates: dict[str, Any],
    clears: set[str],
    current_row: dict,
) -> list[str]:
    """Записывает ручные правки в БД.

    - updates:  {field_name: validated_value} — новые значения, уже прошедшие
                валидацию. Пишутся только те, что реально отличаются от
                значения в current_row.
    - clears:   {field_name, ...} — поля, которые нужно обнулить в БД.
                Обнуляются, только если текущее значение != NULL.

    Возвращает список имён полей, которые реально были записаны.
    """
    table = CATEGORY_TO_TABLE[category]
    assert table in ALLOWED_TABLES, f"Недопустимая таблица: {table}"

    # 1) Собираем реально меняющиеся поля
    to_write: dict[str, Any] = {}
    for fname, new_value in updates.items():
        if not _values_equal(current_row.get(fname), new_value):
            to_write[fname] = new_value

    to_null: list[str] = [
        fname for fname in clears if current_row.get(fname) is not None
    ]

    if not to_write and not to_null:
        return []

    # 2) UPDATE компонента
    set_parts: list[str] = []
    params: dict[str, Any] = {"id": component_id}
    for fname, v in to_write.items():
        set_parts.append(f"{fname} = :{fname}")
        params[fname] = v
    for fname in to_null:
        set_parts.append(f"{fname} = NULL")

    session.execute(
        text(f"UPDATE {table} SET {', '.join(set_parts)} WHERE id = :id"),
        params,
    )

    # 3) upsert в component_field_sources для всех затронутых полей
    changed = list(to_write.keys()) + to_null
    for fname in changed:
        session.execute(
            text(
                "INSERT INTO component_field_sources "
                "    (category, component_id, field_name, source, confidence, source_url, updated_at) "
                "VALUES "
                "    (:category, :component_id, :field_name, :source, :confidence, NULL, NOW()) "
                "ON CONFLICT (category, component_id, field_name) DO UPDATE SET "
                "    source     = EXCLUDED.source, "
                "    confidence = EXCLUDED.confidence, "
                "    source_url = NULL, "
                "    updated_at = NOW()"
            ),
            {
                "category":     category,
                "component_id": component_id,
                "field_name":   fname,
                "source":       SOURCE_MANUAL,
                "confidence":   1.0,
            },
        )

    return changed


def fetch_row(session, category: str, component_id: int, fields: list[str]) -> dict | None:
    """Читает текущие значения перечисленных полей компонента. None, если не найден."""
    table = CATEGORY_TO_TABLE[category]
    assert table in ALLOWED_TABLES, f"Недопустимая таблица: {table}"
    cols = ", ".join(["id", "model", "manufacturer", "sku"] + fields)
    row = session.execute(
        text(f"SELECT {cols} FROM {table} WHERE id = :id"),
        {"id": component_id},
    ).mappings().first()
    return dict(row) if row else None


def insert_new_component(
    session,
    category: str,
    values: dict[str, Any],
) -> int:
    """Создаёт новую строку в таблице категории.

    values ДОЛЖЕН содержать как минимум model и manufacturer. Остальные поля —
    по желанию; все уже прошли валидацию в editor.py. Возвращает id созданной
    записи. В component_field_sources ничего не пишет — это делает вызывающий
    через apply_manual_override c clears=set() и полным updates.
    """
    table = CATEGORY_TO_TABLE[category]
    assert table in ALLOWED_TABLES, f"Недопустимая таблица: {table}"
    if not values.get("model") or not values.get("manufacturer"):
        raise ValueError("Для создания компонента обязательны model и manufacturer")

    cols = list(values.keys())
    col_sql = ", ".join(cols)
    param_sql = ", ".join(f":{c}" for c in cols)
    row = session.execute(
        text(f"INSERT INTO {table} ({col_sql}) VALUES ({param_sql}) RETURNING id"),
        values,
    ).fetchone()
    return row.id


def delete_component(session, category: str, component_id: int) -> dict:
    """Удаляет компонент и связанные записи.

    Возвращает статистику: {'deleted_component': 0|1, 'deleted_prices': N,
    'deleted_sources': N}.
    """
    table = CATEGORY_TO_TABLE[category]
    assert table in ALLOWED_TABLES, f"Недопустимая таблица: {table}"

    stats = {"deleted_component": 0, "deleted_prices": 0, "deleted_sources": 0}

    # 1) supplier_prices
    r = session.execute(
        text(
            "DELETE FROM supplier_prices "
            "WHERE category = :category AND component_id = :id"
        ),
        {"category": category, "id": component_id},
    )
    stats["deleted_prices"] = r.rowcount or 0

    # 2) component_field_sources
    r = session.execute(
        text(
            "DELETE FROM component_field_sources "
            "WHERE category = :category AND component_id = :id"
        ),
        {"category": category, "id": component_id},
    )
    stats["deleted_sources"] = r.rowcount or 0

    # 3) сам компонент
    r = session.execute(
        text(f"DELETE FROM {table} WHERE id = :id"),
        {"id": component_id},
    )
    stats["deleted_component"] = r.rowcount or 0

    return stats
