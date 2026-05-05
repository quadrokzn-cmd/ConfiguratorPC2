"""Переклассификация мусора в psus (этап 11.6.2.5.0b).

Зачем
-----
В категории psu могут оказываться позиции, которые не являются
полноценным ATX/SFX блоком питания: универсальные адаптеры (Gembird
NPA-AC, KS-is, ORIENT PU-C/SAP-, BURO BUM-*, GOPOWER, WAVLINK),
ноутбучные зарядки (FSP FSP040), PoE-инжекторы (Ubiquiti POE),
батарейные блоки питания для охранных систем (ББП Бастион РАПАН) и
прочие dock-станции / USB-PD-зарядки. Они засоряют выдачу PSU и
портят AI-обогащение (нет смысла искать у них power_watts / efficiency).

Скрипт идемпотентно прогоняет is_likely_psu_adapter из
shared.component_filters по всем УЖЕ существующим видимым psus и
помечает кандидатов is_hidden=TRUE. Один общий audit-event на массовое
обновление.

Защитный слой
-------------
Внутри детектора три защитных слоя: (1) форм-фактор PSU в имени
(ATX/SFX/TFX/EPS, 80+, модульный), (2) явная мощность ≥200W,
(3) серия настоящего PSU из whitelist (CBR ATX, Exegate UN/PPH/PPX,
Ginzzu CB/PC, XPG KYBER, Zalman ZM, Aerocool Mirage/Cylon/KCAS,
Powerman PM, 1STPLAYER NGDP, Thermaltake Smart, Formula VX/KCAS).
Если совпало — возвращаем False, даже при позитивных маркерах
адаптера. Это защищает повреждённые упаковкой настоящие PSU
(«Повреждение упаковки CBR ATX 600W…» и т. п.).

Запуск
------
  Локально (или через railway ssh) — dry-run по умолчанию:
    python scripts/reclassify_psu_misclassified.py
    python scripts/reclassify_psu_misclassified.py --dry-run

  Боевой прогон:
    python scripts/reclassify_psu_misclassified.py --apply

Артефакты:
  scripts/reports/reclassify_psu_misclassified_report.md
  scripts/reports/reclassify_psu_misclassified_backup_YYYYMMDD.sql
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
from shared.component_filters import is_likely_psu_adapter


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


def _fetch_visible_psus(engine) -> list:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT p.id, p.model, p.manufacturer, "
                "       array_remove(array_agg(DISTINCT sp.raw_name), NULL) "
                "         AS raw_names "
                "FROM psus p "
                "LEFT JOIN supplier_prices sp "
                "  ON sp.component_id = p.id AND sp.category = 'psu' "
                "WHERE p.is_hidden = FALSE "
                "GROUP BY p.id "
                "ORDER BY p.id ASC"
            )
        ).all()
    return rows


def _is_candidate(row) -> bool:
    """Применяем детектор к конкатенации model + всех raw_names.

    Это нужно потому что бренд иногда есть только в raw_name (например,
    «Gembird NPA-AC1D» — manufacturer=unknown, NPA-AC бренд-серия видна
    только в имени). Конкатенация повышает recall, защитные слои внутри
    детектора защищают от ложных срабатываний.
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
    return is_likely_psu_adapter(full, row.manufacturer)


def find_candidates(engine) -> list:
    rows = _fetch_visible_psus(engine)
    return [r for r in rows if _is_candidate(r)]


def _write_backup(rows: list, *, reports_dir: Path) -> Path:
    today = datetime.now().strftime("%Y%m%d")
    out_path = reports_dir / f"reclassify_psu_misclassified_backup_{today}.sql"
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
        f"UPDATE psus SET is_hidden = FALSE WHERE id IN ({chunks});\n",
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
    out_path = reports_dir / "reclassify_psu_misclassified_report.md"
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = "APPLY (записано в БД)" if applied else "DRY-RUN (БД не менялась)"

    lines: list[str] = [
        f"# Переклассификация мусора в psus — {mode}",
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
                    "UPDATE psus SET is_hidden = TRUE "
                    "WHERE id = ANY(:ids) AND is_hidden = FALSE"
                ),
                {"ids": ids},
            )
            hidden = res.rowcount or 0

        write_audit(
            action=ACTION_COMPONENT_HIDE,
            service="configurator",
            user_login="reclassify_psu_misclassified.py",
            target_type="psu",
            target_id=f"bulk:{hidden}",
            payload={
                "stage":  "11.6.2.5.0b",
                "reason": "psu_adapter_misclassification",
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
            "Переклассифицирует мусор в категории psu (адаптеры, "
            "POE-инжекторы, ноутбучные зарядки) → is_hidden=TRUE. "
            "Идемпотентен."
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
