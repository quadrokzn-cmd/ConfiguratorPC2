# Запись результатов обогащения в БД.
#
# Отвечает за две вещи:
#   1) UPDATE таблицы компонента — только по тем полям, которые сейчас NULL.
#   2) upsert в component_field_sources — фиксирует источник ('regex', 'derived'
#      и т.д.) для каждого реально записанного поля.
#
# Политика идемпотентности: значения, уже проставленные в таблице компонента,
# ни при каких условиях не перезаписываются. Повторный запуск enrich_regex
# затронет только те позиции, где соответствующее поле всё ещё NULL.

from sqlalchemy import text

from app.services.enrichment.base import (
    ALLOWED_TABLES,
    CATEGORY_TO_TABLE,
    ExtractedField,
)


def apply_enrichment(
    session,
    category: str,
    component_id: int,
    fields: dict[str, ExtractedField],
    current_row: dict,
) -> list[str]:
    """Записывает извлечённые поля в БД. Возвращает имена реально записанных полей.

    - session:      активная SQLAlchemy-сессия.
    - category:     'cpu', 'motherboard', ... — ключ из CATEGORY_TO_TABLE.
    - component_id: id компонента в соответствующей таблице.
    - fields:       результат экстрактора.
    - current_row:  текущие значения полей компонента (dict из SELECT).
                    Используется для проверки «поле сейчас NULL».
    """
    table = CATEGORY_TO_TABLE[category]
    assert table in ALLOWED_TABLES, f"Недопустимая таблица: {table}"

    # Политика: пишем только там, где сейчас NULL
    to_write = {
        fname: ef
        for fname, ef in fields.items()
        if current_row.get(fname) is None and ef.value is not None
    }
    if not to_write:
        return []

    # 1) UPDATE <table> SET f1=:f1, f2=:f2, ... WHERE id=:id
    # Имена полей берутся только из результата экстрактора, а не из
    # пользовательского ввода — подстановка в SQL безопасна.
    set_clause = ", ".join(f"{f} = :{f}" for f in to_write)
    params = {f: ef.value for f, ef in to_write.items()}
    params["id"] = component_id
    session.execute(
        text(f"UPDATE {table} SET {set_clause} WHERE id = :id"),
        params,
    )

    # 2) upsert в component_field_sources — по одной записи на поле
    for fname, ef in to_write.items():
        session.execute(
            text(
                "INSERT INTO component_field_sources "
                "    (category, component_id, field_name, source, confidence, updated_at) "
                "VALUES "
                "    (:category, :component_id, :field_name, :source, :confidence, NOW()) "
                "ON CONFLICT (category, component_id, field_name) DO UPDATE SET "
                "    source     = EXCLUDED.source, "
                "    confidence = EXCLUDED.confidence, "
                "    updated_at = NOW()"
            ),
            {
                "category":     category,
                "component_id": component_id,
                "field_name":   fname,
                "source":       ef.source,
                "confidence":   ef.confidence,
            },
        )

    return list(to_write.keys())
