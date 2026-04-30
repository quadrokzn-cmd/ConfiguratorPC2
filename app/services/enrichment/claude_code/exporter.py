# Выгрузка незаполненных позиций в JSON-батчи для Claude Code.
#
# На входе категория и размер батча. На выходе — набор файлов
# enrichment/pending/<category>/batch_NNN_<category>_<timestamp>.json (имя
# с timestamp введено на 11.6.2.1, чтобы новые батчи не сталкивались по
# имени со старыми batch_NNN.json из 2.5Б/2.5В) с описанием каждой
# позиции и списком полей, которые нужно заполнить.
#
# Идемпотентность: id компонентов, уже представленных в pending/, done/
# или archive/ этой категории, повторно не выгружаются. Это позволяет
# спокойно перезапускать экспорт по мере добавления новых компонентов.
#
# Этап 11.6.2.1 (поверх 2.5Б):
#   - В каждый item добавлены поля mpn (= sku), gtin и raw_names —
#     массив raw_name от всех поставщиков (контекст для AI: разные
#     поставщики дают разные имена, в одном из них может быть TDP, в
#     другом — частоты, в третьем — выходы).
#   - Поля, у которых в component_field_sources есть запись с
#     source_detail LIKE 'not_applicable_%', исключаются из to_fill (а
#     если у компонента других целевых полей нет — он не попадает в
#     batch вовсе). Это закрывает кейс с 1638 cases.included_psu_watts,
#     помеченными not_applicable_no_psu в derived_rules.

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

# Старый формат имени (этап 2.5Б): batch_001.json, batch_042.json.
# Новый формат (этап 11.6.2.1): batch_001_gpu_20260430T120512.json — с
# timestamp, чтобы исключить коллизии при параллельных прогонах и при
# перезапусках. Регэксп распознаёт оба варианта; нумерация сквозная.
_BATCH_FILE_RE = re.compile(r"^batch_(\d{3,})(?:_.+)?\.json$")


def _category_dirs(category: str) -> tuple[Path, Path, Path]:
    """Возвращает пути pending/done/archive для категории."""
    return (
        ENRICHMENT_ROOT / "pending"  / category,
        ENRICHMENT_ROOT / "done"     / category,
        ENRICHMENT_ROOT / "archive"  / category,
    )


def _next_batch_number(pending_dir: Path) -> int:
    """Следующий свободный номер batch_NNN.json в pending/.

    Учитывает и старый формат (batch_001.json), и новый
    (batch_001_<cat>_<ts>.json).
    """
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

    # Для всех категорий выгружаем id, model, manufacturer, sku, gtin
    # + текущие значения целевых полей (для блока "current" в JSON).
    cols = ["id", "model", "manufacturer", "sku", "gtin"] + target_fields
    cols_sql = ", ".join(cols)

    if category == "case" and _is_case_psu_pass(target_fields):
        where = "has_psu_included = TRUE AND included_psu_watts IS NULL"
    else:
        # NULL или пустой массив (для ARRAY-полей) считаем «не заполнено».
        clauses: list[str] = []
        for f in target_fields:
            if f in ("supported_sockets", "supported_form_factors"):
                clauses.append(f"({f} IS NULL OR cardinality({f}) = 0)")
            else:
                clauses.append(f"{f} IS NULL")
        where = " OR ".join(clauses)

    sql = f"SELECT {cols_sql} FROM {table} WHERE {where} ORDER BY id"
    return sql, {}


def _fetch_not_applicable(session, category: str) -> dict[int, set[str]]:
    """Карта component_id -> множество field_name, которые помечены
    not_applicable_* в component_field_sources.

    Эти поля исключаются из to_fill при экспорте: AI не должен тратить
    квоту на «честно неприменимые» характеристики (например,
    included_psu_watts у корпуса без БП).
    """
    rows = session.execute(text(
        "SELECT component_id, field_name "
        "  FROM component_field_sources "
        " WHERE category = :cat "
        "   AND source_detail LIKE 'not_applicable_%' "
    ), {"cat": category}).all()
    out: dict[int, set[str]] = {}
    for cid, fname in rows:
        out.setdefault(cid, set()).add(fname)
    return out


def _fetch_raw_names(
    session, category: str, component_ids: list[int]
) -> dict[int, list[str]]:
    """Карта component_id -> [raw_name, ...] от всех поставщиков (без
    дубликатов и пустых строк, в порядке свежести updated_at).

    raw_names используются как контекст для AI: разные поставщики
    дают разные имена позиции, в одном из них может встречаться TDP, в
    другом — частоты, в третьем — выходы.
    """
    if not component_ids:
        return {}
    rows = session.execute(text(
        "SELECT component_id, raw_name, updated_at "
        "  FROM supplier_prices "
        " WHERE category = :cat "
        "   AND component_id = ANY(:ids) "
        "   AND raw_name IS NOT NULL "
        "   AND raw_name <> '' "
        " ORDER BY component_id, updated_at DESC NULLS LAST"
    ), {"cat": category, "ids": component_ids}).all()
    out: dict[int, list[str]] = {}
    for cid, name, _ts in rows:
        bucket = out.setdefault(cid, [])
        if name not in bucket:
            bucket.append(name)
    return out


def _row_to_item(
    row: dict,
    target_fields: list[str],
    *,
    raw_names: list[str],
    not_applicable_fields: set[str],
) -> dict | None:
    """Превращает строку БД в элемент items[] выгружаемого JSON.

    Возвращает None, если после фильтра not_applicable у компонента не
    осталось ни одного поля, которое нужно заполнить — такие позиции в
    batch не попадают.
    """
    current = {f: row.get(f) for f in target_fields}
    to_fill: list[str] = []
    for f, v in current.items():
        if v is not None:
            # массивы тоже считаем заполненными, если они непустые
            if isinstance(v, list) and not v:
                pass
            else:
                continue
        if f in not_applicable_fields:
            continue
        to_fill.append(f)

    if not to_fill:
        return None

    current_present = {
        f: v for f, v in current.items()
        if v is not None and not (isinstance(v, list) and not v)
    }
    return {
        "id":           row["id"],
        "manufacturer": row.get("manufacturer") or "",
        "sku":          row.get("sku") or "",          # legacy-совместимое имя
        "mpn":          row.get("sku") or "",          # 11.6.2.1: alias для AI
        "gtin":         row.get("gtin") or "",
        "model":        row.get("model") or "",
        "raw_names":    raw_names,
        "current":      current_present,
        "to_fill":      to_fill,
    }


def _make_batch_filename(n: int, category: str) -> str:
    """batch_NNN_<category>_<UTC-timestamp>.json (формат 11.6.2.1)."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"batch_{n:03d}_{category}_{ts}.json"


def export_category(
    category: str,
    *,
    batch_size: int | None = None,
    case_psu_pass: bool = False,
    max_batches: int | None = None,
) -> dict:
    """Выгружает незаполненные позиции категории в batch-файлы.

    case_psu_pass=True переключает Case во второй прогон: выгружаются только
    позиции с has_psu_included=TRUE и пустым included_psu_watts; целевое поле
    единственное — included_psu_watts.

    max_batches ограничивает максимальное число выгружаемых файлов за
    один прогон (полезно для отладки и для запуска параллельной AI-обработки
    в несколько чатов: первый чат берёт 5 батчей, второй — следующие 5 и т.д.).
    """
    if category not in TARGET_FIELDS:
        return {
            "category":  category,
            "status":    "unknown_category",
            "candidates": 0, "skipped_known": 0, "exported": 0,
            "filtered_not_applicable": 0,
            "batches":   [],
        }

    if case_psu_pass:
        if category != "case":
            raise ValueError("case_psu_pass=True допустим только для category='case'")
        target_fields = [CASE_PSU_WATTS_FIELD]
    else:
        target_fields = list(TARGET_FIELDS[category])

    if batch_size is None:
        batch_size = DEFAULT_BATCH_SIZES.get(category, 30)
    if batch_size < 1:
        raise ValueError(f"batch_size должен быть >= 1, передано {batch_size}")
    if max_batches is not None and max_batches < 1:
        raise ValueError(f"max_batches должен быть >= 1, передано {max_batches}")

    pending, _done, _archive = _category_dirs(category)
    pending.mkdir(parents=True, exist_ok=True)

    sql, params = _build_select_sql(category, target_fields)
    known_ids = _collect_known_ids(category, case_psu_pass=case_psu_pass)

    session = SessionLocal()
    try:
        rows = session.execute(text(sql), params).mappings().all()
        candidate_ids = [int(r["id"]) for r in rows]
        not_applicable = _fetch_not_applicable(session, category)
        raw_names_map = _fetch_raw_names(session, category, candidate_ids)
    finally:
        session.close()

    items_all: list[dict] = []
    skipped_known = 0
    filtered_not_applicable = 0
    for row in rows:
        cid = row["id"]
        if cid in known_ids:
            skipped_known += 1
            continue
        item = _row_to_item(
            dict(row),
            target_fields,
            raw_names=raw_names_map.get(cid, []),
            not_applicable_fields=not_applicable.get(cid, set()),
        )
        if item is None:
            filtered_not_applicable += 1
            continue
        items_all.append(item)

    batches_created: list[str] = []
    next_n = _next_batch_number(pending)
    written_batches = 0
    exported_count = 0
    for i in range(0, len(items_all), batch_size):
        if max_batches is not None and written_batches >= max_batches:
            break
        batch_items = items_all[i:i + batch_size]
        fname = _make_batch_filename(next_n, category)
        batch_payload = {
            "category":     category,
            "batch_id":     f"batch_{next_n:03d}",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "target_fields": target_fields,
            "case_psu_pass": case_psu_pass if category == "case" else False,
            "items":        batch_items,
        }
        out_path = pending / fname
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(batch_payload, f, ensure_ascii=False, indent=2)
        batches_created.append(out_path.name)
        next_n += 1
        written_batches += 1
        exported_count += len(batch_items)

    return {
        "category":      category,
        "status":        "success",
        "candidates":    len(rows),
        "skipped_known": skipped_known,
        "filtered_not_applicable": filtered_not_applicable,
        "exported":      exported_count,
        "batches":       batches_created,
        "batch_size":    batch_size,
        "case_psu_pass": case_psu_pass if category == "case" else False,
    }


def export_all(
    *,
    batch_size: int | None = None,
    max_batches: int | None = None,
) -> list[dict]:
    """Прогоняет export_category по всем категориям из ALL_CATEGORIES.

    Для case дополнительно ничего не делает: второй прогон (psu_watts) запускается
    отдельно вручную после импорта первого, когда has_psu_included заполнено.
    """
    results: list[dict] = []
    for cat in ALL_CATEGORIES:
        results.append(export_category(
            cat, batch_size=batch_size, max_batches=max_batches,
        ))
    return results
