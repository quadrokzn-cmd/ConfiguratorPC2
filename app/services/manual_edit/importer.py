# Импорт отредактированного CSV обратно в БД.
#
# Логика:
#   - читаем CSV, проходим построчно;
#   - для каждой строки читаем текущее состояние компонента из БД;
#   - для каждой заполненной ячейки валидируем значение и сверяем с БД;
#   - пишем только фактические изменения через apply_manual_override;
#   - dry-run не выполняет запись, но валидация и дифф работают полностью;
#   - итоговый отчёт: обновлено / без изменений / отклонено (с причинами) /
#     не найдено / ошибки уровня строки.

from __future__ import annotations

import csv
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from app.database import SessionLocal
from app.services.manual_edit.csv_io import parse_cell, serialize_cell
from app.services.manual_edit.persistence import apply_manual_override, fetch_row
from app.services.manual_edit.schema import (
    CSV_DELIMITER,
    REQUIRED_FIELDS,
    SYSTEM_COLS,
    all_fields,
    is_array_field,
)
from app.services.manual_edit.validators_extra import (
    ValidationError,
    is_known_field,
    validate_field,
)

logger = logging.getLogger(__name__)


def _empty_stats() -> dict:
    return {
        "rows_total":      0,
        "rows_updated":    0,
        "rows_unchanged":  0,
        "rows_not_found":  0,
        "rows_errors":     0,
        "fields_written":  0,
        "fields_cleared":  0,
        "fields_rejected": 0,
        "reject_reasons":  Counter(),
        "errors":          [],
    }


def import_csv(file_path: Path, *, dry_run: bool = False) -> dict:
    """Применяет CSV к БД. Возвращает отчёт."""
    stats = _empty_stats()

    if not file_path.exists():
        stats["errors"].append(f"file_not_found:{file_path}")
        return stats

    # utf-8-sig автоматически снимает BOM, если он есть.
    with file_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=CSV_DELIMITER)
        try:
            header = next(reader)
        except StopIteration:
            stats["errors"].append("empty_csv")
            return stats

        rows = list(reader)

    header_idx = {name: i for i, name in enumerate(header)}
    missing_sys = [c for c in SYSTEM_COLS if c not in header_idx]
    if missing_sys:
        stats["errors"].append(f"missing_system_cols:{','.join(missing_sys)}")
        return stats

    session = SessionLocal()
    try:
        for line_no, raw_row in enumerate(rows, start=2):  # считаем с 2: 1-я — заголовок
            if not raw_row or all((c or "").strip() == "" for c in raw_row):
                continue
            stats["rows_total"] += 1

            def _get(col: str) -> str:
                idx = header_idx.get(col)
                if idx is None or idx >= len(raw_row):
                    return ""
                return (raw_row[idx] or "").strip()

            id_raw   = _get("id")
            category = _get("category")

            if not id_raw or not category:
                stats["rows_errors"] += 1
                stats["errors"].append(f"row {line_no}: missing id or category")
                continue

            try:
                component_id = int(id_raw)
            except ValueError:
                stats["rows_errors"] += 1
                stats["errors"].append(f"row {line_no}: bad id {id_raw!r}")
                continue

            if category not in REQUIRED_FIELDS:
                stats["rows_errors"] += 1
                stats["errors"].append(f"row {line_no}: unknown category {category!r}")
                continue

            fields = all_fields(category)
            current_row = fetch_row(session, category, component_id, fields)
            if current_row is None:
                stats["rows_not_found"] += 1
                stats["errors"].append(
                    f"row {line_no}: component {category}#{component_id} not found"
                )
                continue

            # 1) Валидируем все заполненные ячейки строки
            updates: dict[str, Any] = {}
            clears: set[str] = set()
            row_rejected = False

            for fname in fields:
                if not is_known_field(category, fname):
                    continue  # колонка в CSV есть, но мы её не обрабатываем
                raw_cell = _get(fname)
                is_arr = is_array_field(category, fname)
                value, is_clear = parse_cell(raw_cell, is_array=is_arr)
                if is_clear:
                    clears.add(fname)
                    continue
                if value is None:
                    continue  # пустая ячейка — пропуск

                # Оптимизация: если ячейка CSV идентична сериализованному
                # значению из БД, поле ассистент не трогал — пропускаем без
                # валидации. Это важно, потому что в БД бывают «устаревшие»
                # значения (например, групповой сокет LGA115X из 2.5А),
                # которые наши валидаторы могут не принять.
                current_serialized = serialize_cell(
                    current_row.get(fname), is_array=is_arr
                )
                if raw_cell.strip() == current_serialized:
                    continue

                try:
                    validated = validate_field(category, fname, value)
                except ValidationError as exc:
                    stats["fields_rejected"] += 1
                    code = str(exc).split(":")[0]
                    stats["reject_reasons"][f"{category}.{fname}:{code}"] += 1
                    stats["errors"].append(
                        f"row {line_no} ({category}#{component_id}): "
                        f"field {fname}={raw_cell!r} — {exc}"
                    )
                    row_rejected = True
                    continue

                updates[fname] = validated

            # 2) Если что-то отклонено — строка с ошибкой, но другие валидные
            # поля всё равно применяем (ассистент увидит ошибки в отчёте и
            # исправит конкретные ячейки).
            if not updates and not clears:
                if row_rejected:
                    stats["rows_errors"] += 1
                else:
                    stats["rows_unchanged"] += 1
                continue

            # 3) Пишем в БД (или считаем, что написали бы, в dry-run)
            if dry_run:
                # эмулируем поведение apply_manual_override по current_row
                changed: list[str] = []
                from app.services.manual_edit.persistence import _values_equal
                for fname, v in updates.items():
                    if not _values_equal(current_row.get(fname), v):
                        changed.append(fname)
                for fname in clears:
                    if current_row.get(fname) is not None:
                        changed.append(fname)
            else:
                savepoint = session.begin_nested()
                try:
                    changed = apply_manual_override(
                        session, category, component_id,
                        updates, clears, current_row,
                    )
                    savepoint.commit()
                except Exception as exc:
                    savepoint.rollback()
                    stats["rows_errors"] += 1
                    stats["errors"].append(
                        f"row {line_no} ({category}#{component_id}): db write failed — {exc}"
                    )
                    continue

            if changed:
                stats["rows_updated"] += 1
                for f in changed:
                    if f in clears:
                        stats["fields_cleared"] += 1
                    else:
                        stats["fields_written"] += 1
            else:
                if row_rejected:
                    stats["rows_errors"] += 1
                else:
                    stats["rows_unchanged"] += 1

        if not dry_run:
            session.commit()

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return stats


def format_report(stats: dict, *, dry_run: bool) -> str:
    """Человекочитаемый отчёт по итогам импорта."""
    lines: list[str] = []
    lines.append("=" * 72)
    title = "Импорт правок из CSV"
    if dry_run:
        title += "   [DRY-RUN: запись в БД не выполнялась]"
    lines.append(title)
    lines.append("=" * 72)
    lines.append(f"Строк всего:          {stats['rows_total']}")
    lines.append(f"  обновлено:          {stats['rows_updated']}")
    lines.append(f"  без изменений:      {stats['rows_unchanged']}")
    lines.append(f"  компонент не найден:{stats['rows_not_found']}")
    lines.append(f"  ошибок в строках:   {stats['rows_errors']}")
    lines.append(f"Полей записано:       {stats['fields_written']}")
    lines.append(f"Полей обнулено:       {stats['fields_cleared']}")
    lines.append(f"Полей отклонено:      {stats['fields_rejected']}")

    reasons = stats.get("reject_reasons") or {}
    if reasons:
        lines.append("")
        lines.append("Причины отклонения:")
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {reason:40} {n}")

    errors = stats.get("errors") or []
    if errors:
        lines.append("")
        lines.append(f"Событий с диагностикой: {len(errors)} (первые 20):")
        for e in errors[:20]:
            lines.append(f"  - {e}")
        if len(errors) > 20:
            lines.append(f"  … ещё {len(errors) - 20}")

    return "\n".join(lines)
