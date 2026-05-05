"""Переклассификация мусора в storages (этап 11.6.2.6.0b).

Зачем
-----
По итогам аудита 6.0a в категории storages оказались позиции, не
являющиеся накопителями: рамки-переходники 2.5"→3.5" (id=782 Kingston
SNA-BR2/35, id=1133 Digma DGBRT2535). Скрипт прогоняет детектор
`shared.component_filters.is_likely_non_storage` и помечает кандидатов
`is_hidden=TRUE`.

Детектор имеет защитные слои:
  1. capacity_gb ≥ 32 → НЕ помечать (передаётся из БД);
  2. storage_type не пустой → НЕ помечать (передаётся из БД);
  3. форм-факторные маркеры в имени (NVMe / M.2 / 2280 / mSATA / U.2)
     → НЕ помечать.

Идемпотентен: один audit-event на массовое обновление, общий
бэкап-rollback. По образцу `reclassify_psu_misclassified.py`.

Запуск
------
  Локально (или через railway ssh) — dry-run по умолчанию:
    python scripts/reclassify_storage_misclassified.py
    python scripts/reclassify_storage_misclassified.py --dry-run

  Боевой прогон:
    python scripts/reclassify_storage_misclassified.py --apply

Артефакты:
  scripts/reports/reclassify_storage_misclassified_report.md
  scripts/reports/reclassify_storage_misclassified_backup_YYYYMMDD.sql
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text

from shared.audit import write_audit
from shared.audit_actions import ACTION_COMPONENT_HIDE
from shared.component_filters import is_likely_non_storage


def _connect():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL не задан. Скопируйте .env.example в .env "
            "и проставьте подключение."
        )
    return create_engine(
        db_url, future=True,
        connect_args={"client_encoding": "utf8"},
    )


def _fetch_visible_storages(engine) -> list:
    """Видимые storages + агрегированные raw_name из supplier_prices.
    capacity_gb / storage_type выбираем явно — они нужны детектору как
    защитные слои (если уже заполнены, позиция не мусор)."""
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT s.id, s.model, s.manufacturer, "
                "       s.capacity_gb, s.storage_type, "
                "       array_remove(array_agg(DISTINCT sp.raw_name), NULL) "
                "         AS raw_names "
                "FROM storages s "
                "LEFT JOIN supplier_prices sp "
                "  ON sp.component_id = s.id AND sp.category = 'storage' "
                "WHERE s.is_hidden = FALSE "
                "GROUP BY s.id "
                "ORDER BY s.id ASC"
            )
        ).all()
    return rows


def _is_candidate(row) -> bool:
    """Прогоняем детектор по конкатенации model + raw_names. Защитные
    слои внутри детектора (capacity_gb / storage_type / форм-факторные
    маркеры) защищают от ложных срабатываний на полноценных накопителях.

    Bare-слова «SSD»/«HDD» в имени не блокируют срабатывание (см.
    docstring is_likely_non_storage), потому что они появляются в самих
    триггер-фразах вида «крепления для SSD/HDD».
    """
    parts: list[str] = []
    if row.model:
        parts.append(str(row.model))
    if row.manufacturer:
        parts.append(str(row.manufacturer))
    for n in (row.raw_names or []):
        if n:
            parts.append(str(n))
    full = " | ".join(parts)
    if not full.strip():
        return False
    return is_likely_non_storage(
        full,
        row.manufacturer,
        capacity_gb=row.capacity_gb,
        storage_type=row.storage_type,
    )


def find_candidates(engine) -> list:
    rows = _fetch_visible_storages(engine)
    return [r for r in rows if _is_candidate(r)]


def _write_backup(rows: list, *, reports_dir: Path) -> Path:
    today = datetime.now().strftime("%Y%m%d")
    out_path = reports_dir / f"reclassify_storage_misclassified_backup_{today}.sql"
    ids = [int(r.id) for r in rows]
    if not ids:
        out_path.write_text(
            "-- backup: список пуст, откатывать нечего.\n",
            encoding="utf-8",
        )
        return out_path
    chunks = ", ".join(str(i) for i in ids)
    out_path.write_text(
        "-- Откат: вернуть найденные id в is_hidden = FALSE.\n"
        f"UPDATE storages SET is_hidden = FALSE WHERE id IN ({chunks});\n",
        encoding="utf-8",
    )
    return out_path


def _write_report(
    candidates: list,
    *,
    applied: bool,
    reports_dir: Path,
    backup_path: Path | None,
) -> Path:
    out_path = reports_dir / "reclassify_storage_misclassified_report.md"
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = "APPLY (записано в БД)" if applied else "DRY-RUN (БД не менялась)"

    lines: list[str] = [
        f"# Переклассификация мусора в storages — {mode}",
        "",
        f"Дата запуска: {today}",
        "",
        f"Всего кандидатов: **{len(candidates)}**",
    ]
    if applied:
        lines.append(f"Помечено `is_hidden = TRUE`: **{len(candidates)}**")
    if backup_path is not None:
        lines.append(
            f"Бэкап для отката: `{backup_path.relative_to(reports_dir.parent.parent)}`"
        )
    lines.append("")

    by_mfg: Counter = Counter(r.manufacturer or "—" for r in candidates)
    if by_mfg:
        lines += [
            "## Топ-производителей среди кандидатов",
            "",
            "| Производитель | Кол-во |",
            "|---|---:|",
        ]
        for mfg, n in by_mfg.most_common(20):
            lines.append(f"| {mfg} | {n} |")
        lines.append("")

    sample = candidates[:50]
    if sample:
        lines += [
            f"## Примеры (первые {len(sample)} из {len(candidates)})",
            "",
            "| ID | Производитель | Модель |",
            "|---:|---|---|",
        ]
        for r in sample:
            model = (r.model or "").replace("|", "\\|")
            if len(model) > 100:
                model = model[:97] + "..."
            mfg = (r.manufacturer or "").replace("|", "\\|")
            lines.append(f"| {r.id} | {mfg} | {model} |")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def reclassify(
    engine,
    *,
    apply: bool,
    reports_dir: Path | None = None,
) -> dict:
    if reports_dir is None:
        reports_dir = Path(__file__).resolve().parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    candidates = find_candidates(engine)

    backup_path: Path | None = None
    hidden = 0
    if apply and candidates:
        backup_path = _write_backup(candidates, reports_dir=reports_dir)
        ids = [int(r.id) for r in candidates]
        with engine.begin() as conn:
            res = conn.execute(
                text(
                    "UPDATE storages SET is_hidden = TRUE "
                    "WHERE id = ANY(:ids) AND is_hidden = FALSE"
                ),
                {"ids": ids},
            )
            hidden = res.rowcount or 0

        write_audit(
            action=ACTION_COMPONENT_HIDE,
            service="configurator",
            user_login="reclassify_storage_misclassified.py",
            target_type="storage",
            target_id=f"bulk:{hidden}",
            payload={
                "stage":  "11.6.2.6.0b",
                "reason": "non_storage_misclassification",
                "ids":    ids[:200],
                "total":  hidden,
            },
        )

    report_path = _write_report(
        candidates,
        applied=apply,
        reports_dir=reports_dir,
        backup_path=backup_path,
    )
    return {
        "found":       len(candidates),
        "hidden":      hidden,
        "report_path": report_path,
        "backup_path": backup_path,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Переклассифицирует мусор в категории storage (рамки, "
            "card-reader, USB-hub) → is_hidden=TRUE. Идемпотентен."
        )
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run", action="store_true",
        help="Только показать кандидатов и сохранить отчёт. "
             "Поведение по умолчанию.",
    )
    group.add_argument(
        "--apply", action="store_true",
        help="Записать изменения в БД.",
    )
    parser.add_argument(
        "--report-path", type=Path, default=None,
        help="Каталог для отчётов (по умолчанию scripts/reports).",
    )
    args = parser.parse_args()

    apply = bool(args.apply)
    engine = _connect()
    try:
        result = reclassify(engine, apply=apply, reports_dir=args.report_path)
    finally:
        engine.dispose()

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] Кандидатов: {result['found']}")
    if apply:
        print(f"[{mode}] Помечено is_hidden=TRUE: {result['hidden']}")
        if result["backup_path"]:
            print(f"[{mode}] Бэкап для отката: {result['backup_path']}")
    print(f"[{mode}] Отчёт: {result['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
