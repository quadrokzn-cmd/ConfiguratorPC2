"""Выгрузка SKU без атрибутов в JSON-батчи для Claude Code.

Файлы пишутся в `enrichment/auctions/pending/<brand>_<batch_id>.json`.
Идемпотентность: SKU, уже представленные в pending/ и done/, повторно не
выгружаются. Архив не учитывается — повторная выгрузка после архивации
допустима (например, при ручной правке).

Этап 8 слияния (2026-05-08): корень обогащения переехал из QT-репо
`enrichment/` в C-PC2 `enrichment/auctions/` (рядом с существующим
C-PC2-enrichment workflow для ПК-компонентов). Таблица переименована
`nomenclature` → `printers_mfu`.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import text

from shared.db import engine

logger = logging.getLogger(__name__)

# app/services/auctions/catalog/enrichment/exporter.py → up 5 уровней = корень репо.
_REPO_ROOT = Path(__file__).resolve().parents[5]
ENRICHMENT_ROOT = _REPO_ROOT / "enrichment" / "auctions"

# brand-имя в файле — нормализованное (ascii lowercase). Сама `brand` в БД может
# быть на кириллице или с символами — храним и то, и другое.
_NAME_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_BATCH_FILE_RE = re.compile(r"^.+_(\d{3,})\.json$")


def _normalize_brand(brand: str) -> str:
    s = (brand or "unknown").strip().lower()
    s = _NAME_NORMALIZE_RE.sub("_", s).strip("_")
    return s or "unknown"


def _enrichment_dirs() -> tuple[Path, Path, Path]:
    return (
        ENRICHMENT_ROOT / "pending",
        ENRICHMENT_ROOT / "done",
        ENRICHMENT_ROOT / "archive",
    )


def _next_batch_number(pending_dir: Path, brand_norm: str) -> int:
    n = 0
    for p in pending_dir.glob(f"{brand_norm}_*.json"):
        m = _BATCH_FILE_RE.match(p.name)
        if m:
            try:
                n = max(n, int(m.group(1)))
            except ValueError:
                continue
    return n + 1


def _collect_known_skus() -> set[str]:
    """SKU, уже выгруженные в pending/ или done/."""
    pending, done, _archive = _enrichment_dirs()
    known: set[str] = set()
    for d in (pending, done):
        if not d.exists():
            continue
        for p in d.glob("*.json"):
            try:
                with p.open("r", encoding="utf-8") as f:
                    payload = json.load(f)
            except Exception as exc:
                logger.warning("Не удалось прочитать %s: %s", p, exc)
                continue
            for m in payload.get("models", []) or payload.get("results", []):
                sku = m.get("sku")
                if isinstance(sku, str):
                    known.add(sku)
    return known


def _select_pending_rows(brand: str | None) -> list[dict]:
    sql = """
        SELECT sku, mpn, brand, name, category
          FROM printers_mfu
         WHERE (attrs_jsonb IS NULL OR attrs_jsonb = '{}'::jsonb)
    """
    params: dict = {}
    if brand:
        sql += " AND lower(brand) = lower(:brand)"
        params["brand"] = brand
    sql += " ORDER BY brand NULLS LAST, sku"

    with engine.connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def export_pending(
    brand: str | None = None,
    batch_size: int = 30,
) -> list[Path]:
    """Выгружает SKU без атрибутов в pending-батчи.

    Группировка: один файл = один бренд + N моделей. Файлов на бренд может быть
    несколько, если моделей больше batch_size.

    Возвращает список путей к созданным файлам.
    """
    if batch_size < 1:
        raise ValueError(f"batch_size должен быть >= 1, передано {batch_size}")

    pending, _done, _archive = _enrichment_dirs()
    pending.mkdir(parents=True, exist_ok=True)

    rows = _select_pending_rows(brand)
    known = _collect_known_skus()

    rows_filtered = [r for r in rows if r["sku"] not in known]

    by_brand: dict[str, list[dict]] = {}
    for r in rows_filtered:
        by_brand.setdefault(r.get("brand") or "unknown", []).append(r)

    created: list[Path] = []
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for brand_value, items in by_brand.items():
        brand_norm = _normalize_brand(brand_value)
        next_n = _next_batch_number(pending, brand_norm)
        for i in range(0, len(items), batch_size):
            chunk = items[i:i + batch_size]
            batch_id = f"{next_n:03d}"
            payload = {
                "brand":        brand_value,
                "batch_id":     batch_id,
                "generated_at": generated_at,
                "models": [
                    {
                        "sku":      m["sku"],
                        "mpn":      m.get("mpn") or "",
                        "name":     m.get("name") or "",
                        "category": m.get("category") or "",
                    }
                    for m in chunk
                ],
            }
            out_path = pending / f"{brand_norm}_{batch_id}.json"
            with out_path.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            created.append(out_path)
            next_n += 1

    return created


def export_manual_single(sku: str) -> Path:
    """Выгружает один SKU как ручной батч (`<brand>_manual_<timestamp>.json`).

    Используется кнопкой «Обновить атрибуты» в UI справочника.
    """
    sql = "SELECT sku, mpn, brand, name, category FROM printers_mfu WHERE sku = :sku"
    with engine.connect() as conn:
        row = conn.execute(text(sql), {"sku": sku}).mappings().first()
    if row is None:
        raise ValueError(f"SKU не найден: {sku}")

    pending, _done, _archive = _enrichment_dirs()
    pending.mkdir(parents=True, exist_ok=True)

    brand_value = row.get("brand") or "unknown"
    brand_norm = _normalize_brand(brand_value)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    batch_id = f"manual_{timestamp}"

    payload = {
        "brand":        brand_value,
        "batch_id":     batch_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "models": [
            {
                "sku":      row["sku"],
                "mpn":      row.get("mpn") or "",
                "name":     row.get("name") or "",
                "category": row.get("category") or "",
            }
        ],
    }
    out_path = pending / f"{brand_norm}_{batch_id}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out_path
