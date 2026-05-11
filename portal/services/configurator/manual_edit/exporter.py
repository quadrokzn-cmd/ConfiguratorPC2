# Выгрузка компонентов в CSV для ручного редактирования.
#
# Формат CSV описан в schema.py: разделитель ';', массивы — '|',
# bool — 'true'/'false'. Файл пишется в UTF-8 с BOM, чтобы Excel на
# русской Windows корректно определял кодировку.

from __future__ import annotations

import csv
import logging
from pathlib import Path

from sqlalchemy import text

from app.database import SessionLocal
from portal.services.configurator.enrichment.base import CATEGORY_TO_TABLE
from portal.services.configurator.manual_edit.csv_io import serialize_cell
from portal.services.configurator.manual_edit.schema import (
    ALL_CATEGORIES,
    CSV_DELIMITER,
    REQUIRED_FIELDS,
    all_fields,
    csv_header,
    is_array_field,
)

logger = logging.getLogger(__name__)


def _build_select(category: str, only_null: bool) -> str:
    """SQL SELECT для выборки компонентов.

    Если only_null=True — выбираются только строки, где NULL хотя бы в одном
    обязательном поле категории. Иначе — все строки категории.
    """
    table = CATEGORY_TO_TABLE[category]
    required = REQUIRED_FIELDS.get(category, [])
    cols = ["id", "model", "manufacturer", "sku"] + all_fields(category)
    cols_sql = ", ".join(cols)

    if only_null and required:
        where = " OR ".join(f"{f} IS NULL" for f in required)
        return f"SELECT {cols_sql} FROM {table} WHERE {where} ORDER BY id"
    return f"SELECT {cols_sql} FROM {table} ORDER BY id"


def export_category(
    category: str,
    output_path: Path,
    *,
    only_null: bool = False,
) -> dict:
    """Выгружает одну категорию в CSV. Возвращает статистику."""
    if category not in REQUIRED_FIELDS:
        return {
            "category": category,
            "status":   "unknown_category",
            "rows":     0,
            "path":     str(output_path),
        }

    header = csv_header(category)
    fields = all_fields(category)
    sql = _build_select(category, only_null)

    session = SessionLocal()
    try:
        rows = session.execute(text(sql)).mappings().all()
    finally:
        session.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig = UTF-8 с BOM, Excel открывает без проблем с кодировкой.
    with output_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=CSV_DELIMITER)
        writer.writerow(header)
        for row in rows:
            line: list[str] = [
                str(row["id"]),
                category,
                row.get("model") or "",
                row.get("manufacturer") or "",
                row.get("sku") or "",
            ]
            for fname in fields:
                line.append(
                    serialize_cell(
                        row.get(fname),
                        is_array=is_array_field(category, fname),
                    )
                )
            writer.writerow(line)

    return {
        "category": category,
        "status":   "success",
        "rows":     len(rows),
        "path":     str(output_path),
        "only_null": only_null,
    }


def export_all(
    output_dir: Path,
    *,
    only_null: bool = False,
) -> list[dict]:
    """Выгружает все категории — по одному CSV на каждую."""
    results: list[dict] = []
    for cat in ALL_CATEGORIES:
        path = output_dir / f"manual_{cat}.csv"
        results.append(export_category(cat, path, only_null=only_null))
    return results
