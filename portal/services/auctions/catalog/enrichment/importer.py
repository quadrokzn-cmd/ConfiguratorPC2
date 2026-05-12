"""Импорт результатов обогащения от Claude Code в `printers_mfu.attrs_jsonb`.

Поток:
    1) сканируем все JSON-файлы в `enrichment/auctions/done/`;
    2) валидируем каждый по schema.PRINTER_MFU_ATTRS;
    3) если есть ошибки — файл пропускаем целиком и логируем;
    4) **Per-key merge** (Backlog #10, 2026-05-12): для каждого SKU
       читаем текущие `attrs_jsonb`/`attrs_source` и мерджим:
       - `attrs_jsonb`: n/a из done не затирает существующее не-n/a;
         не-n/a из done побеждает (см. `merge.py::merge_attrs`).
       - `attrs_source`: union через `+`-разделитель, дедуп,
         порядок появления сохранён (см. `merge.py::merge_source`).
       UPDATE выполняется только если merge изменил состояние —
       иначе SKU попадает в счётчик `skus_unchanged` (идемпотентность).
       Счётчик `skus_updated` инкрементируется только при реальном
       diff `attrs_jsonb` (фикс счётчика 2026-05-13): SKU, где
       UPDATE прошёл, но изменился только `attrs_source` (audit-trail),
       относится к `skus_unchanged`.
    5) обработанные файлы переносим в `enrichment/auctions/archive/<YYYY-MM-DD>/`.

Этап 8 слияния (2026-05-08): таблица переименована `nomenclature` → `printers_mfu`,
корень обогащения переехал в `enrichment/auctions/`.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from shared.db import engine
from portal.services.auctions.catalog.cost_base import recompute_cost_base
from portal.services.auctions.catalog.enrichment.exporter import ENRICHMENT_ROOT
from portal.services.auctions.catalog.enrichment.merge import (
    merge_attrs,
    merge_source,
)
from portal.services.auctions.catalog.enrichment.schema import (
    PRINTER_MFU_ATTRS,
    SOURCE_CLAUDE_CODE,
    validate_attrs,
)

logger = logging.getLogger(__name__)


def _empty_report() -> dict:
    return {
        "files_total":     0,
        "files_imported":  0,
        "files_rejected":  0,
        "skus_updated":    0,
        "skus_unchanged":  0,
        "skus_unknown":    0,
        "skus_invalid":    0,
        "reject_reasons":  Counter(),
        "errors":          [],
    }


def _archive_dir_for_today() -> Path:
    return ENRICHMENT_ROOT / "archive" / date.today().isoformat()


def _move_to_archive(path: Path) -> None:
    dst_dir = _archive_dir_for_today()
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / path.name
    if dst.exists():
        ts = datetime.now().strftime("%H%M%S")
        dst = dst_dir / f"{path.stem}__{ts}{path.suffix}"
    shutil.move(str(path), str(dst))


def _validate_payload_structure(payload) -> str | None:
    if not isinstance(payload, dict):
        return "корень JSON не объект"
    results = payload.get("results")
    if not isinstance(results, list):
        return "поле 'results' отсутствует или не список"
    for i, item in enumerate(results):
        if not isinstance(item, dict):
            return f"results[{i}] не объект"
        if "sku" not in item or not isinstance(item["sku"], str):
            return f"results[{i}].sku отсутствует или не строка"
        if "attrs" not in item or not isinstance(item["attrs"], dict):
            return f"results[{i}].attrs отсутствует или не объект"
    return None


def _process_file(path: Path, report: dict, *, dry_run: bool) -> None:
    report["files_total"] += 1
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception as exc:
        report["files_rejected"] += 1
        report["reject_reasons"]["unreadable_json"] += 1
        report["errors"].append(f"{path.name}: не удалось прочитать ({exc})")
        return

    struct_err = _validate_payload_structure(payload)
    if struct_err:
        report["files_rejected"] += 1
        report["reject_reasons"]["bad_structure"] += 1
        report["errors"].append(f"{path.name}: {struct_err}")
        return

    # Полная валидация всех результатов до записи в БД — атомарность по файлу.
    per_item_errors: list[tuple[str, list[str]]] = []
    for item in payload["results"]:
        attrs_errors = validate_attrs(item["attrs"])
        if attrs_errors:
            per_item_errors.append((item["sku"], attrs_errors))

    if per_item_errors:
        report["files_rejected"] += 1
        report["reject_reasons"]["invalid_attrs"] += 1
        report["skus_invalid"] += len(per_item_errors)
        for sku, errs in per_item_errors[:5]:
            report["errors"].append(f"{path.name}: SKU={sku}: {'; '.join(errs)}")
        return

    updated_skus: list[str] = []
    skus_unknown = 0
    skus_unchanged = 0
    try:
        with engine.begin() as conn:
            for item in payload["results"]:
                sku = item["sku"]
                incoming_attrs = item["attrs"]

                existing_row = conn.execute(
                    text(
                        "SELECT attrs_jsonb, attrs_source "
                        "FROM printers_mfu WHERE sku = :sku"
                    ),
                    {"sku": sku},
                ).first()
                if existing_row is None:
                    skus_unknown += 1
                    continue

                existing_attrs = existing_row.attrs_jsonb or {}
                existing_source = existing_row.attrs_source

                # Per-key merge: n/a из done не затирает существующее
                # не-n/a; не-n/a из done побеждает (claude_code считаем
                # авторитетнее regex/предыдущей claude-сессии). См.
                # merge.py для деталей и Backlog #10 для контекста.
                merged_attrs = merge_attrs(existing_attrs, incoming_attrs)
                merged_source = merge_source(existing_source, SOURCE_CLAUDE_CODE)

                attrs_changed  = merged_attrs  != existing_attrs
                source_changed = merged_source != existing_source

                if dry_run:
                    # В dry_run обновлёнными считаем только SKU с реальным
                    # diff attrs_jsonb. SKU, где изменился бы только source
                    # (audit-trail), считаем как unchanged — это то, что
                    # хочет видеть оператор в лог-отчёте.
                    if attrs_changed:
                        updated_skus.append(sku)
                    else:
                        skus_unchanged += 1
                    continue

                if not attrs_changed and not source_changed:
                    # Идемпотентность: повторный импорт того же содержимого
                    # → 0 строк меняется. Не дёргаем UPDATE, чтобы
                    # attrs_updated_at не скакал на каждом прогоне.
                    skus_unchanged += 1
                    continue

                conn.execute(
                    text(
                        """
                        UPDATE printers_mfu
                           SET attrs_jsonb       = CAST(:attrs AS JSONB),
                               attrs_source      = :source,
                               attrs_updated_at  = now()
                         WHERE sku = :sku
                        """
                    ),
                    {
                        "attrs":  json.dumps(merged_attrs, ensure_ascii=False),
                        "source": merged_source,
                        "sku":    sku,
                    },
                )
                if attrs_changed:
                    updated_skus.append(sku)
                else:
                    # Source-only UPDATE (например, n/a-incoming поверх
                    # regex_name-existing с не-n/a attrs): запись прошла,
                    # но attrs_jsonb не изменился — по DoD счётчика
                    # «SKU обновлено» такой SKU не считаем, относим к
                    # unchanged.
                    skus_unchanged += 1
    except Exception as exc:
        report["files_rejected"] += 1
        report["reject_reasons"]["db_write_failed"] += 1
        report["errors"].append(f"{path.name}: ошибка записи в БД ({exc})")
        return

    report["files_imported"] += 1
    report["skus_updated"] += len(updated_skus)
    report["skus_unchanged"] += skus_unchanged
    report["skus_unknown"] += skus_unknown

    if not dry_run:
        # cost_base_rub зависит от supplier_prices, не от attrs — пересчёт здесь
        # формальный, но дешёвый: SKU уже в обороте, индекс по component_id
        # отрабатывает мгновенно.
        for sku in updated_skus:
            try:
                recompute_cost_base(sku=sku)
            except Exception as exc:
                logger.warning("recompute_cost_base(%s) упал: %s", sku, exc)
        _move_to_archive(path)


def import_done(dry_run: bool = False) -> dict:
    """Импортирует все JSON-файлы из enrichment/auctions/done/. Возвращает отчёт."""
    report = _empty_report()
    done_dir = ENRICHMENT_ROOT / "done"
    if not done_dir.exists():
        return report

    files = sorted(done_dir.glob("*.json"))
    for path in files:
        _process_file(path, report, dry_run=dry_run)

    return report


def format_report(report: dict, *, dry_run: bool) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    title = "Импорт обогащения Claude Code: printers_mfu.attrs_jsonb"
    if dry_run:
        title += "   [DRY-RUN]"
    lines.append(title)
    lines.append("=" * 72)
    lines.append(f"Файлов в done/:           {report['files_total']}")
    lines.append(f"  импортировано:          {report['files_imported']}")
    lines.append(f"  отклонено:              {report['files_rejected']}")
    lines.append(f"SKU обновлено:            {report['skus_updated']}")
    lines.append(f"SKU без изменений:        {report.get('skus_unchanged', 0)}")
    lines.append(f"SKU не найдено в БД:      {report['skus_unknown']}")
    lines.append(f"SKU с невалидными attrs:  {report['skus_invalid']}")

    reasons = report.get("reject_reasons") or {}
    if reasons:
        lines.append("")
        lines.append("Причины отклонения файлов:")
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {reason:24} {n}")

    errors = report.get("errors") or []
    if errors:
        lines.append("")
        lines.append(f"Сообщения ({len(errors)}):")
        for e in errors[:20]:
            lines.append(f"  - {e}")
        if len(errors) > 20:
            lines.append(f"  … ещё {len(errors) - 20}")

    # На всякий случай: список ожидаемых полей — в подсказку оператору.
    lines.append("")
    lines.append(f"Ожидаемые ключи в attrs: {sorted(PRINTER_MFU_ATTRS.keys())}")
    return "\n".join(lines)
