# Импорт результатов обогащения от Claude Code в БД.
#
# Поток:
#   1) сканируем enrichment/done/<category>/batch_*.json;
#   2) для каждого item валидируем поля по схеме (validators.py);
#   3) пишем в БД через apply_enrichment с source='claude_code' и URL источника;
#   4) обработанный файл перекладываем в enrichment/archive/<category>/;
#   5) собираем подробный отчёт (accepted / rejected / skipped по причинам).

from __future__ import annotations

import json
import logging
import shutil
from collections import Counter
from decimal import Decimal
from pathlib import Path

from sqlalchemy import text

from app.database import SessionLocal
from app.services.enrichment.base import CATEGORY_TO_TABLE, ExtractedField
from app.services.enrichment.claude_code.exporter import (
    ENRICHMENT_ROOT,
    _category_dirs,
)
from app.services.enrichment.claude_code.schema import (
    ALL_CATEGORIES,
    DEFAULT_CONFIDENCE,
    SOURCE_DETAIL_WEB_SEARCH,
    SOURCE_NAME,
    TARGET_FIELDS,
)
from app.services.enrichment.claude_code.validators import (
    ValidatedField,
    ValidationError,
    is_target_field,
    validate_field,
)
from app.services.enrichment.persistence import apply_enrichment

logger = logging.getLogger(__name__)


def _empty_stats(category: str) -> dict:
    return {
        "category":     category,
        "files_total":  0,
        "files_done":   0,
        "items_total":  0,
        "items_with_writes": 0,
        "fields_accepted":  0,
        "fields_rejected":  0,
        "fields_skipped_null":   0,   # value=null от Claude Code
        "fields_skipped_already": 0,  # поле уже заполнено в БД
        "reject_reasons":   Counter(),
        "accepted_per_field": Counter(),
        "errors":           [],       # критические ошибки уровня файла/item
    }


def _load_batch(path: Path) -> dict | None:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error("Не удалось прочитать %s: %s", path, exc)
        return None


def _fetch_current_row(session, category: str, component_id: int) -> dict | None:
    """Текущие значения целевых полей компонента из БД (для проверки 'NULL'-only)."""
    table = CATEGORY_TO_TABLE[category]
    fields = TARGET_FIELDS[category] + (["included_psu_watts"] if category == "case" else [])
    cols = ", ".join(["id"] + fields)
    row = session.execute(
        text(f"SELECT {cols} FROM {table} WHERE id = :id"),
        {"id": component_id},
    ).mappings().first()
    return dict(row) if row else None


def _cross_check_cpu(
    item_validated: dict[str, ValidatedField],
    current_row: dict,
) -> list[tuple[str, str]]:
    """Кросс-полевая проверка для CPU: turbo >= base. Возвращает список
    (field_name, reason) полей, которые нужно отклонить.
    """
    rejects: list[tuple[str, str]] = []
    base = item_validated.get("base_clock_ghz")
    turbo = item_validated.get("turbo_clock_ghz")
    base_v = base.value if base is not None else current_row.get("base_clock_ghz")
    turbo_v = turbo.value if turbo is not None else current_row.get("turbo_clock_ghz")
    if base_v is not None and turbo_v is not None:
        if Decimal(str(turbo_v)) < Decimal(str(base_v)):
            # отклоняем именно новые поля, а не уже лежащие в БД
            if turbo is not None:
                rejects.append(("turbo_clock_ghz", f"cross_check:turbo({turbo_v})<base({base_v})"))
            if base is not None:
                rejects.append(("base_clock_ghz", f"cross_check:base({base_v})>turbo({turbo_v})"))
    return rejects


def _process_item(session, category: str, item: dict, stats: dict, *, dry_run: bool) -> None:
    component_id = item.get("id")
    fields_raw = item.get("fields") or {}

    if not isinstance(component_id, int) or not isinstance(fields_raw, dict):
        stats["errors"].append(f"bad_item_structure:{item!r}")
        return

    current_row = _fetch_current_row(session, category, component_id)
    if current_row is None:
        stats["errors"].append(f"unknown_component:{category}#{component_id}")
        return

    # 1) Валидируем каждое поле; собираем валидные и отклонённые отдельно.
    validated: dict[str, ValidatedField] = {}
    for fname, raw in fields_raw.items():
        if not is_target_field(category, fname):
            stats["fields_rejected"] += 1
            stats["reject_reasons"][f"unknown_field:{fname}"] += 1
            continue
        # «Уже заполнено в БД» — пропускаем без записи и без причины-ошибки.
        if current_row.get(fname) is not None:
            stats["fields_skipped_already"] += 1
            continue
        try:
            vf = validate_field(category, fname, raw)
        except ValidationError as exc:
            msg = str(exc)
            if msg == "null_value":
                stats["fields_skipped_null"] += 1
            else:
                stats["fields_rejected"] += 1
                stats["reject_reasons"][msg.split(":")[0]] += 1
            continue
        validated[fname] = vf

    # 2) Кросс-полевые проверки.
    if category == "cpu":
        for fname, reason in _cross_check_cpu(validated, current_row):
            validated.pop(fname, None)
            stats["fields_rejected"] += 1
            stats["reject_reasons"][reason.split(":")[0]] += 1

    if not validated:
        return

    # 3) Преобразуем в ExtractedField и пишем через apply_enrichment.
    ef_fields = {
        fname: ExtractedField(
            value=vf.value,
            source=SOURCE_NAME,
            confidence=DEFAULT_CONFIDENCE,
            source_url=vf.source_url,
        )
        for fname, vf in validated.items()
    }

    if dry_run:
        written = list(ef_fields.keys())
    else:
        savepoint = session.begin_nested()
        try:
            written = apply_enrichment(
                session, category, component_id, ef_fields, current_row,
                source_detail=SOURCE_DETAIL_WEB_SEARCH,
            )
            savepoint.commit()
        except Exception as exc:
            savepoint.rollback()
            logger.error(
                "id=%s/%s: ошибка записи в БД — %s",
                category, component_id, exc,
            )
            stats["errors"].append(f"db_write_failed:{category}#{component_id}:{exc}")
            return

    if written:
        stats["items_with_writes"] += 1
        for fname in written:
            stats["fields_accepted"] += 1
            stats["accepted_per_field"][fname] += 1


def _move_to_archive(path: Path, category: str) -> None:
    archive_dir = ENRICHMENT_ROOT / "archive" / category
    archive_dir.mkdir(parents=True, exist_ok=True)
    target = archive_dir / path.name
    # Если файл с таким именем уже есть — добавляем суффикс с timestamp,
    # чтобы не потерять предыдущий артефакт.
    if target.exists():
        stem = path.stem
        from datetime import datetime
        target = archive_dir / f"{stem}__{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    shutil.move(str(path), str(target))


def import_category(
    category: str, *, dry_run: bool = False, keep_source: bool = False,
) -> dict:
    """Импортирует все batch_*.json из enrichment/done/<category>/.

    keep_source (этап 11.6.2.3.3) — если True, файлы остаются в done/,
    не перемещаются в archive/. Use case: импорт на локали для теста, а
    затем повторный импорт на проде через railway ssh теми же файлами
    (раньше для повторного импорта файлы копировали из archive/ обратно
    в done/ руками).
    """
    if category not in TARGET_FIELDS:
        return {**_empty_stats(category), "status": "unknown_category"}

    stats = _empty_stats(category)
    stats["status"] = "success"

    _pending, done_dir, _archive = _category_dirs(category)
    if not done_dir.exists():
        return stats

    batch_files = sorted(done_dir.glob("batch_*.json"))
    stats["files_total"] = len(batch_files)

    if not batch_files:
        return stats

    session = SessionLocal()
    try:
        for path in batch_files:
            payload = _load_batch(path)
            if payload is None:
                stats["errors"].append(f"unreadable:{path.name}")
                continue
            if payload.get("category") != category:
                stats["errors"].append(
                    f"category_mismatch:{path.name}:{payload.get('category')}!={category}"
                )
                continue

            items = payload.get("items") or []
            for item in items:
                stats["items_total"] += 1
                _process_item(session, category, item, stats, dry_run=dry_run)

            if not dry_run:
                session.commit()  # коммит после каждого файла — атомарность по батчу
                if not keep_source:
                    _move_to_archive(path, category)

            stats["files_done"] += 1

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return stats


def import_all(
    *, dry_run: bool = False, keep_source: bool = False,
) -> list[dict]:
    return [
        import_category(c, dry_run=dry_run, keep_source=keep_source)
        for c in ALL_CATEGORIES
    ]


def import_file(
    path: Path, *, dry_run: bool = False, keep_source: bool = False,
) -> dict:
    """Импорт одного конкретного batch-файла (этап 11.6.2.1).

    Применяется, когда чат Claude Code положил результат не в
    enrichment/done/<category>/, а вернул отдельным файлом, который
    хочется накатить точечно.

    Категория определяется по полю payload['category']; директория
    archive/ берётся из неё же. Если файл изначально лежит вне
    enrichment/done/<category>/, после импорта он всё равно
    переезжает в archive/ соответствующей категории.
    """
    if not path.exists():
        return {**_empty_stats("?"), "status": "file_not_found",
                "errors": [f"file_not_found:{path}"]}

    payload = _load_batch(path)
    if payload is None:
        return {**_empty_stats("?"), "status": "unreadable",
                "errors": [f"unreadable:{path}"]}

    category = payload.get("category")
    if category not in TARGET_FIELDS:
        return {**_empty_stats(category or "?"),
                "status": "unknown_category",
                "errors": [f"unknown_category:{category}"]}

    stats = _empty_stats(category)
    stats["status"] = "success"
    stats["files_total"] = 1

    items = payload.get("items") or []
    session = SessionLocal()
    try:
        for item in items:
            stats["items_total"] += 1
            _process_item(session, category, item, stats, dry_run=dry_run)
        if not dry_run:
            session.commit()
            if not keep_source:
                _move_to_archive(path, category)
        stats["files_done"] = 1
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return stats


def format_report(stats: dict, *, dry_run: bool) -> str:
    """Человекочитаемый отчёт по итогам импорта одной категории."""
    cat = stats.get("category", "?")
    lines: list[str] = []
    lines.append("=" * 72)
    title = f"Импорт Claude Code: {cat.upper()}"
    if dry_run:
        title += "   [DRY-RUN: запись в БД не выполнялась]"
    lines.append(title)
    lines.append("=" * 72)

    if stats.get("status") == "unknown_category":
        lines.append("Неизвестная категория — пропущено.")
        return "\n".join(lines)

    lines.append(f"Файлов в done/:               {stats.get('files_total', 0)}")
    lines.append(f"  обработано:                 {stats.get('files_done', 0)}")
    lines.append(f"Всего items в батчах:         {stats.get('items_total', 0)}")
    lines.append(f"  items с записью в БД:       {stats.get('items_with_writes', 0)}")
    lines.append(f"Полей принято:                {stats.get('fields_accepted', 0)}")
    lines.append(f"Полей отклонено:              {stats.get('fields_rejected', 0)}")
    lines.append(f"Полей пропущено (null):       {stats.get('fields_skipped_null', 0)}")
    lines.append(f"Полей пропущено (уже есть):   {stats.get('fields_skipped_already', 0)}")

    accepted_per_field = stats.get("accepted_per_field") or {}
    if accepted_per_field:
        lines.append("")
        lines.append("Принято по полям:")
        for f, n in sorted(accepted_per_field.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {f:28} {n}")

    reasons = stats.get("reject_reasons") or {}
    if reasons:
        lines.append("")
        lines.append("Причины отклонения:")
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {reason:28} {n}")

    errors = stats.get("errors") or []
    if errors:
        lines.append("")
        lines.append(f"Критических ошибок: {len(errors)}")
        for e in errors[:10]:
            lines.append(f"  - {e}")
        if len(errors) > 10:
            lines.append(f"  … ещё {len(errors) - 10}")

    return "\n".join(lines)
