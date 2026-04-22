# Выгрузка незаполненных позиций в JSON-батчи для Claude Code.
#
# На входе категория и размер батча. На выходе — набор файлов
# enrichment/pending/<category>/batch_NNN.json с описанием каждой позиции
# и списком полей, которые нужно заполнить.
#
# Идемпотентность: id компонентов, уже представленных в pending/, done/
# или archive/ этой категории, повторно не выгружаются. Это позволяет
# спокойно перезапускать экспорт по мере добавления новых компонентов.

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from app.database import SessionLocal
from app.services.enrichment.base import CATEGORY_TO_TABLE
from app.services.enrichment.claude_code.schema import (
    ALL_CATEGORIES,
    CASE_PSU_WATTS_FIELD,
    DEFAULT_BATCH_SIZES,
    TARGET_FIELDS,
)

logger = logging.getLogger(__name__)

# Корневая папка обогащения относительно корня проекта.
_REPO_ROOT = Path(__file__).resolve().parents[4]
ENRICHMENT_ROOT = _REPO_ROOT / "enrichment"

_BATCH_FILE_RE = re.compile(r"^batch_(\d{3,})\.json$")


def _category_dirs(category: str) -> tuple[Path, Path, Path]:
    """Возвращает пути pending/done/archive для категории."""
    return (
        ENRICHMENT_ROOT / "pending"  / category,
        ENRICHMENT_ROOT / "done"     / category,
        ENRICHMENT_ROOT / "archive"  / category,
    )


def _next_batch_number(pending_dir: Path) -> int:
    """Следующий свободный номер batch_NNN.json в pending/."""
    n = 0
    for p in pending_dir.glob("batch_*.json"):
        m = _BATCH_FILE_RE.match(p.name)
        if m:
            n = max(n, int(m.group(1)))
    return n + 1


def _collect_known_ids(category: str, *, case_psu_pass: bool = False) -> set[int]:
    """id, которые уже фигурируют в pending/done/archive — не выгружаем повторно.

    Для категории 'case' первый и второй прогоны не пересекаются по полям,
    поэтому учитываются раздельно: при case_psu_pass=True берутся только
    батчи 2-го прогона, иначе — только 1-го.
    """
    known: set[int] = set()
    pending, done, archive = _category_dirs(category)
    for d in (pending, done, archive):
        if not d.exists():
            continue
        for p in d.glob("batch_*.json"):
            try:
                with p.open("r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as exc:
                logger.warning("Не удалось прочитать %s: %s", p, exc)
                continue
            if category == "case":
                payload_psu_pass = bool(payload.get("case_psu_pass"))
                if payload_psu_pass != case_psu_pass:
                    continue
            for item in payload.get("items", []):
                cid = item.get("id")
                if isinstance(cid, int):
                    known.add(cid)
    return known


def _is_case_psu_pass(target_fields: list[str]) -> bool:
    """True, если выгружаем второй прогон Case (только included_psu_watts)."""
    return target_fields == [CASE_PSU_WATTS_FIELD]


def _build_select_sql(category: str, target_fields: list[str]) -> tuple[str, dict]:
    """SQL для выборки кандидатов на обогащение.

    Логика выбора:
    - case + второй прогон (только included_psu_watts):
        has_psu_included = TRUE AND included_psu_watts IS NULL
    - в остальных случаях:
        хотя бы одно из target_fields = NULL.
    """
    table = CATEGORY_TO_TABLE[category]

    # Для всех категорий выгружаем id, model, manufacturer, sku +
    # текущие значения целевых полей (для блока "current" в JSON).
    cols = ["id", "model", "manufacturer", "sku"] + target_fields
    cols_sql = ", ".join(cols)

    if category == "case" and _is_case_psu_pass(target_fields):
        where = "has_psu_included = TRUE AND included_psu_watts IS NULL"
    else:
        where = " OR ".join(f"{f} IS NULL" for f in target_fields)

    sql = f"SELECT {cols_sql} FROM {table} WHERE {where} ORDER BY id"
    return sql, {}


def _row_to_item(row: dict, target_fields: list[str]) -> dict:
    """Превращает строку БД в элемент items[] выгружаемого JSON."""
    current = {f: row.get(f) for f in target_fields}
    # PostgreSQL TEXT[] прилетает как list — JSON-сериализуем как есть.
    # NULL-поля идут в "to_fill", non-NULL — в "current" для подсказки.
    to_fill = [f for f, v in current.items() if v is None]
    current_present = {f: v for f, v in current.items() if v is not None}
    return {
        "id":           row["id"],
        "manufacturer": row.get("manufacturer") or "",
        "sku":          row.get("sku") or "",
        "model":        row.get("model") or "",
        "current":      current_present,
        "to_fill":      to_fill,
    }


def export_category(
    category: str,
    *,
    batch_size: int | None = None,
    case_psu_pass: bool = False,
) -> dict:
    """Выгружает незаполненные позиции категории в batch-файлы.

    case_psu_pass=True переключает Case во второй прогон: выгружаются только
    позиции с has_psu_included=TRUE и пустым included_psu_watts; целевое поле
    единственное — included_psu_watts.
    """
    if category not in TARGET_FIELDS:
        return {
            "category":  category,
            "status":    "unknown_category",
            "candidates": 0, "skipped_known": 0, "exported": 0,
            "batches":   [],
        }

    if case_psu_pass:
        if category != "case":
            raise ValueError("case_psu_pass=True допустим только для category='case'")
        target_fields = [CASE_PSU_WATTS_FIELD]
    else:
        target_fields = list(TARGET_FIELDS[category])

    if batch_size is None:
        batch_size = DEFAULT_BATCH_SIZES.get(category, 40)
    if batch_size < 1:
        raise ValueError(f"batch_size должен быть >= 1, передано {batch_size}")

    pending, _done, _archive = _category_dirs(category)
    pending.mkdir(parents=True, exist_ok=True)

    sql, params = _build_select_sql(category, target_fields)
    known_ids = _collect_known_ids(category, case_psu_pass=case_psu_pass)

    session = SessionLocal()
    try:
        rows = session.execute(text(sql), params).mappings().all()
    finally:
        session.close()

    items_all: list[dict] = []
    skipped_known = 0
    for row in rows:
        if row["id"] in known_ids:
            skipped_known += 1
            continue
        items_all.append(_row_to_item(dict(row), target_fields))

    batches_created: list[str] = []
    next_n = _next_batch_number(pending)
    for i in range(0, len(items_all), batch_size):
        batch_items = items_all[i:i + batch_size]
        batch_payload = {
            "category":     category,
            "batch_id":     f"batch_{next_n:03d}",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "target_fields": target_fields,
            "case_psu_pass": case_psu_pass if category == "case" else False,
            "items":        batch_items,
        }
        out_path = pending / f"batch_{next_n:03d}.json"
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(batch_payload, f, ensure_ascii=False, indent=2)
        batches_created.append(out_path.name)
        next_n += 1

    return {
        "category":      category,
        "status":        "success",
        "candidates":    len(rows),
        "skipped_known": skipped_known,
        "exported":      len(items_all),
        "batches":       batches_created,
        "batch_size":    batch_size,
        "case_psu_pass": case_psu_pass if category == "case" else False,
    }


def export_all(*, batch_size: int | None = None) -> list[dict]:
    """Прогоняет export_category по всем категориям из ALL_CATEGORIES.

    Для case дополнительно ничего не делает: второй прогон (psu_watts) запускается
    отдельно вручную после импорта первого, когда has_psu_included заполнено.
    """
    results: list[dict] = []
    for cat in ALL_CATEGORIES:
        results.append(export_category(cat, batch_size=batch_size))
    return results
