# Операции для точечных правок через scripts/edit_component.py:
#   - show (по id или SKU);
#   - update одного поля;
#   - add нового компонента (интерактивно);
#   - delete компонента с подтверждением.

from __future__ import annotations

from typing import Any

from sqlalchemy import text

from shared.db import SessionLocal
from portal.services.configurator.enrichment.base import ALLOWED_TABLES, CATEGORY_TO_TABLE
from portal.services.configurator.manual_edit.csv_io import parse_cell
from portal.services.configurator.manual_edit.persistence import (
    apply_manual_override,
    delete_component,
    fetch_row,
    insert_new_component,
)
from portal.services.configurator.manual_edit.schema import (
    ALL_CATEGORIES,
    REQUIRED_FIELDS,
    SOURCE_MANUAL,
    all_fields,
    is_array_field,
)
from portal.services.configurator.manual_edit.validators_extra import (
    ValidationError,
    is_known_field,
    validate_field,
)


def find_component(
    identifier: str,
    *,
    category: str | None = None,
) -> tuple[str, dict] | None:
    """Ищет компонент по id (если identifier — число) или по sku.

    Если указана категория — ищет только в ней, иначе просматривает все таблицы.
    Возвращает (category, row_dict) или None, если не найден (либо если sku
    нашёлся в нескольких категориях — тогда бросает RuntimeError).
    """
    session = SessionLocal()
    try:
        is_id = identifier.isdigit()
        categories = [category] if category else ALL_CATEGORIES
        found: list[tuple[str, dict]] = []

        for cat in categories:
            table = CATEGORY_TO_TABLE[cat]
            assert table in ALLOWED_TABLES
            cols = ", ".join(["id", "model", "manufacturer", "sku"] + all_fields(cat))
            if is_id:
                row = session.execute(
                    text(f"SELECT {cols} FROM {table} WHERE id = :v"),
                    {"v": int(identifier)},
                ).mappings().first()
            else:
                row = session.execute(
                    text(f"SELECT {cols} FROM {table} WHERE sku = :v"),
                    {"v": identifier},
                ).mappings().first()
            if row:
                found.append((cat, dict(row)))

        if not found:
            return None
        if len(found) > 1:
            cats = ", ".join(c for c, _ in found)
            raise RuntimeError(
                f"SKU {identifier!r} найден в нескольких категориях: {cats}. "
                f"Уточните через --category."
            )
        return found[0]
    finally:
        session.close()


def format_component(category: str, row: dict) -> str:
    """Человекочитаемый вывод компонента со всеми полями и источниками."""
    lines: list[str] = []
    lines.append("=" * 72)
    lines.append(f"Категория:    {category}")
    lines.append(f"id:           {row.get('id')}")
    lines.append(f"manufacturer: {row.get('manufacturer')}")
    lines.append(f"model:        {row.get('model')}")
    lines.append(f"sku:          {row.get('sku')}")
    lines.append("-" * 72)

    # источники полей
    session = SessionLocal()
    try:
        src_rows = session.execute(
            text(
                "SELECT field_name, source, source_url FROM component_field_sources "
                "WHERE category = :c AND component_id = :id"
            ),
            {"c": category, "id": row.get("id")},
        ).all()
    finally:
        session.close()
    sources = {r.field_name: (r.source, r.source_url) for r in src_rows}

    required = set(REQUIRED_FIELDS.get(category, []))
    for fname in all_fields(category):
        val = row.get(fname)
        source, src_url = sources.get(fname, (None, None))
        marker = "*" if fname in required else " "
        src_suffix = ""
        if source:
            src_suffix = f"  [{source}]"
            if src_url:
                src_suffix += f" {src_url}"
        lines.append(f"  {marker} {fname:28} = {val!r}{src_suffix}")
    lines.append("=" * 72)
    lines.append("* — обязательное поле категории")
    return "\n".join(lines)


def update_one_field(
    component_id: int,
    field_name: str,
    raw_value: str,
    *,
    category: str | None = None,
) -> dict:
    """Обновляет одно поле компонента. Возвращает словарь с итогом.

    Если category не указана — определяется автоматически по id (пробуем все).
    """
    session = SessionLocal()
    try:
        cat: str | None = category
        current_row: dict | None = None

        if cat:
            current_row = fetch_row(session, cat, component_id, all_fields(cat))
        else:
            for c in ALL_CATEGORIES:
                current_row = fetch_row(session, c, component_id, all_fields(c))
                if current_row:
                    cat = c
                    break

        if current_row is None or cat is None:
            return {"status": "not_found", "category": None, "id": component_id}

        if not is_known_field(cat, field_name):
            return {
                "status":    "unknown_field",
                "category":  cat,
                "id":        component_id,
                "field":     field_name,
            }

        # парсим ячейку так же, как при CSV-импорте
        parsed, is_clear = parse_cell(
            raw_value, is_array=is_array_field(cat, field_name)
        )

        if is_clear:
            updates: dict[str, Any] = {}
            clears = {field_name}
        else:
            if parsed is None:
                return {
                    "status":   "empty_value",
                    "category": cat,
                    "id":       component_id,
                    "field":    field_name,
                }
            try:
                validated = validate_field(cat, field_name, parsed)
            except ValidationError as exc:
                return {
                    "status":   "rejected",
                    "category": cat,
                    "id":       component_id,
                    "field":    field_name,
                    "reason":   str(exc),
                }
            updates = {field_name: validated}
            clears = set()

        changed = apply_manual_override(
            session, cat, component_id, updates, clears, current_row
        )
        session.commit()
        return {
            "status":   "ok" if changed else "no_change",
            "category": cat,
            "id":       component_id,
            "field":    field_name,
            "changed":  changed,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def add_component_interactive(
    category: str,
    prompt_fn,
) -> dict:
    """Интерактивно создаёт новый компонент заданной категории.

    prompt_fn — функция-входная точка для ввода: принимает строку-приглашение,
    возвращает строку ответа пользователя. Это позволяет легко мокать ввод
    в тестах.

    Все введённые значения валидируются; обязательные поля проверяются на
    непустоту (кроме model и manufacturer — они в БД NOT NULL). Опциональные
    можно пропустить пустой строкой. После создания в component_field_sources
    появляются записи source='manual' для всех заполненных полей.
    """
    if category not in REQUIRED_FIELDS:
        raise ValueError(f"Неизвестная категория: {category}")

    # 1) model / manufacturer / sku
    base_values: dict[str, Any] = {}
    while not base_values.get("model"):
        base_values["model"] = prompt_fn("model: ").strip()
    while not base_values.get("manufacturer"):
        base_values["manufacturer"] = prompt_fn("manufacturer: ").strip()
    sku = prompt_fn("sku (можно пустой): ").strip()
    if sku:
        base_values["sku"] = sku

    # 2) остальные поля — по очереди
    validated_fields: dict[str, Any] = {}
    for fname in all_fields(category):
        required = fname in REQUIRED_FIELDS.get(category, [])
        suffix = "*" if required else ""
        hint = ""
        if is_array_field(category, fname):
            hint = " (через | )"
        raw = prompt_fn(f"{fname}{suffix}{hint}: ").strip()
        if not raw:
            if required:
                # обязательные поля допускаем пустыми — БД NOT NULL снят (миграция 002),
                # но предупреждаем пользователя.
                print(f"    предупреждение: {fname} — обязательное, оставлено пустым")
            continue

        parsed, is_clear = parse_cell(raw, is_array=is_array_field(category, fname))
        if is_clear:
            continue
        try:
            validated_fields[fname] = validate_field(category, fname, parsed)
        except ValidationError as exc:
            print(f"    отклонено: {exc}. Поле оставлено пустым.")

    # 3) создаём компонент и пишем источники
    session = SessionLocal()
    try:
        # insert базовой записи с model/manufacturer/sku и всеми валидированными
        # полями одним INSERT'ом
        all_values = {**base_values, **validated_fields}
        new_id = insert_new_component(session, category, all_values)

        # для полей характеристик — пишем source='manual' через общий механизм
        if validated_fields:
            # current_row — «пустой» (строка только что создана), поэтому
            # apply_manual_override запишет все эти поля как изменения
            empty_row = {f: None for f in all_fields(category)}
            apply_manual_override(
                session, category, new_id,
                validated_fields, clears=set(), current_row=empty_row,
            )
        session.commit()
        return {
            "status":    "ok",
            "category":  category,
            "id":        new_id,
            "written":   list(validated_fields.keys()),
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def delete_one(component_id: int, category: str | None = None) -> dict:
    """Удаляет компонент. Категорию определяет сам, если не задана."""
    session = SessionLocal()
    try:
        cat: str | None = category
        if not cat:
            for c in ALL_CATEGORIES:
                row = fetch_row(session, c, component_id, [])
                if row:
                    cat = c
                    break
        if not cat:
            return {"status": "not_found", "id": component_id}

        stats = delete_component(session, cat, component_id)
        session.commit()
        return {"status": "ok", "category": cat, "id": component_id, **stats}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
