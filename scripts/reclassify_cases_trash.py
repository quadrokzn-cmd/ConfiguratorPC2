"""Переклассификация мусора в таблице cases (этап 11.6.2.4.0).

Зачем
-----
В категории `case` могут оказываться:
  1. Самостоятельные корпусные/120-мм вентиляторы (например, Aerocool
     Core Plus 120 мм) — `is_likely_loose_case_fan`.
  2. Отдельные корзины 3.5"/2.5" / mobile rack / drive cage — пока в
     БД таких единиц нет, детектор работает как профилактика upstream
     (`is_likely_drive_cage`).
  3. Отдельные PCIe-райзеры (riser cable / card / extender) —
     `is_likely_pcie_riser`.
  4. Отдельные сменные боковые панели / стёкла / пылевые фильтры —
     `is_likely_case_panel_or_filter`.
  5. Антипровисные кронштейны/стойки для GPU —
     `is_likely_gpu_support_bracket`.

Все эти позиции НЕ являются корпусом ПК; они засоряют выдачу подбора
и портят AI-обогащение (нет смысла искать у них form-factor / has_psu).

Скрипт идемпотентно прогоняет 5 эвристик из shared.component_filters
по всем УЖЕ существующим видимым cases и помечает кандидатов
`is_hidden = TRUE`. Один общий audit-event на массовое обновление.

Защитный слой
-------------
Все 5 эвристик внутри проверяют `_CASE_HOUSING_HINTS`: если в имени
есть «midi tower» / «full tower» / «корпус ПК» / «JBOD» /
«rack-mount» / «PC case» / «ATX case» — детектор возвращает False,
даже если в имени присутствуют слова «riser cable» или «dust filter».
Это защищает полноценные корпуса с предустановленными аксессуарами
(Lian Li SUP01X с PCIe-райзером в комплекте, Lian Li A3-mATX с Bottom
Dust Filter, AIC J2024 JBOD-шасси и т. п.).

Запуск
------
  Dry-run (по умолчанию, только отчёт):
    python scripts/reclassify_cases_trash.py
    python scripts/reclassify_cases_trash.py --dry-run

  Боевой прогон (требует явный двойной флаг):
    python scripts/reclassify_cases_trash.py --confirm --confirm-yes

Артефакты:
  scripts/reports/reclassify_cases_trash_report.md
  scripts/reports/reclassify_cases_trash_backup_YYYYMMDD.sql
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
from shared.component_filters import (
    is_likely_case_panel_or_filter,
    is_likely_drive_cage,
    is_likely_gpu_support_bracket,
    is_likely_loose_case_fan,
    is_likely_pcie_riser,
)


def _connect():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL не задан. Скопируйте .env.example в .env и "
            "проставьте подключение."
        )
    return create_engine(
        db_url, future=True,
        connect_args={"client_encoding": "utf8"},
    )


def _fetch_visible_cases_with_raw_names(engine) -> list:
    """Видимые cases + агрегированный массив raw_names из supplier_prices."""
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT c.id, c.model, c.manufacturer, c.sku, "
                "       c.supported_form_factors, c.has_psu_included, "
                "       c.included_psu_watts, "
                "       array_remove(array_agg(DISTINCT sp.raw_name), NULL) "
                "         AS raw_names "
                "FROM cases c "
                "LEFT JOIN supplier_prices sp "
                "  ON sp.component_id = c.id AND sp.category = 'case' "
                "WHERE c.is_hidden = FALSE "
                "GROUP BY c.id "
                "ORDER BY c.id ASC"
            )
        ).all()
    return rows


# Все 5 эвристик, проверяются по очереди — какая сработала первой.
_DETECTORS: tuple[tuple[str, callable], ...] = (
    ("loose_case_fan",  is_likely_loose_case_fan),
    ("drive_cage",      is_likely_drive_cage),
    ("pcie_riser",      is_likely_pcie_riser),
    ("panel_or_filter", is_likely_case_panel_or_filter),
    ("gpu_support",     is_likely_gpu_support_bracket),
)


def _classify_row(row) -> str | None:
    """Возвращает имя сработавшего детектора или None.

    Применяем детекторы к конкатенации model + manufacturer + всех raw_names.
    Если у компонента уже заполнены и `supported_form_factors`, и
    `has_psu_included` — это однозначно корпус, не трогаем (даже если
    совпала серия). Это защита от регрессий: лучше не пометить мусор,
    чем потерять валидный корпус.
    """
    has_form_factors = (
        row.supported_form_factors is not None
        and len(row.supported_form_factors) > 0
    )
    has_psu_known = row.has_psu_included is not None
    if has_form_factors and has_psu_known:
        return None

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
        return None
    for name, detector in _DETECTORS:
        if detector(full, row.manufacturer):
            return name
    return None


def _is_candidate(row) -> bool:
    return _classify_row(row) is not None


def find_candidates(engine) -> list:
    rows = _fetch_visible_cases_with_raw_names(engine)
    return [r for r in rows if _is_candidate(r)]


def find_candidates_by_detector(engine) -> dict[str, list]:
    rows = _fetch_visible_cases_with_raw_names(engine)
    grouped: dict[str, list] = {name: [] for name, _ in _DETECTORS}
    for r in rows:
        d = _classify_row(r)
        if d is not None:
            grouped[d].append(r)
    return grouped


def _write_backup(rows: list, *, reports_dir: Path) -> Path:
    today = datetime.now().strftime("%Y%m%d")
    out_path = reports_dir / f"reclassify_cases_trash_backup_{today}.sql"
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
        f"UPDATE cases SET is_hidden = FALSE WHERE id IN ({chunks});\n",
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
    out_path = reports_dir / "reclassify_cases_trash_report.md"
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    mode = "APPLY (записано в БД)" if applied else "DRY-RUN (БД не менялась)"
    lines.append(f"# Переклассификация мусора в cases — {mode}")
    lines.append("")
    lines.append(f"Дата запуска: {today}")
    lines.append("")
    lines.append(f"Всего кандидатов: **{len(candidates)}**")
    if applied:
        lines.append(f"Помечено `is_hidden = TRUE`: **{len(candidates)}**")
    if backup_path is not None:
        lines.append(
            "Бэкап для отката: "
            f"`{backup_path.relative_to(reports_dir.parent.parent)}`"
        )
    lines.append("")

    by_det: Counter = Counter()
    for r in candidates:
        d = _classify_row(r)
        if d:
            by_det[d] += 1
    if by_det:
        lines.append("## По детектору")
        lines.append("")
        lines.append("| Детектор | Кол-во |")
        lines.append("|---|---:|")
        for d_name, _ in _DETECTORS:
            n = by_det.get(d_name, 0)
            if n:
                lines.append(f"| {d_name} | {n} |")
        lines.append("")

    by_mfg: Counter = Counter()
    for r in candidates:
        by_mfg[r.manufacturer or "—"] += 1
    if by_mfg:
        lines.append("## Топ-производителей среди кандидатов")
        lines.append("")
        lines.append("| Производитель | Кол-во |")
        lines.append("|---|---:|")
        for mfg, n in by_mfg.most_common(20):
            lines.append(f"| {mfg} | {n} |")
        lines.append("")

    sample = candidates[:50]
    if sample:
        lines.append(f"## Примеры (первые {len(sample)} из {len(candidates)})")
        lines.append("")
        lines.append("| ID | Детектор | Производитель | Модель | SKU |")
        lines.append("|---:|---|---|---|---|")
        for r in sample:
            d = _classify_row(r) or ""
            model = (r.model or "").replace("|", "\\|")
            mfg = (r.manufacturer or "").replace("|", "\\|")
            sku = (r.sku or "").replace("|", "\\|")
            lines.append(f"| {r.id} | {d} | {mfg} | {model} | {sku} |")
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
    hidden_count = 0
    if apply and candidates:
        backup_path = _write_backup(candidates, reports_dir=reports_dir)
        ids = [int(r.id) for r in candidates]
        with engine.begin() as conn:
            res = conn.execute(
                text(
                    "UPDATE cases SET is_hidden = TRUE "
                    "WHERE id = ANY(:ids) AND is_hidden = FALSE"
                ),
                {"ids": ids},
            )
            hidden_count = res.rowcount or 0

        by_det: dict[str, list[int]] = {name: [] for name, _ in _DETECTORS}
        for r in candidates:
            d = _classify_row(r)
            if d:
                by_det[d].append(int(r.id))

        write_audit(
            action=ACTION_COMPONENT_HIDE,
            service="configurator",
            user_login="reclassify_cases_trash.py",
            target_type="case",
            target_id=f"bulk:{hidden_count}",
            payload={
                "stage":   "11.6.2.4.0",
                "reason":  "non_case_reclassification",
                "by_detector": {k: len(v) for k, v in by_det.items()},
                "ids":     ids[:200],
                "total":   hidden_count,
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
        "hidden":      hidden_count,
        "report_path": report_path,
        "backup_path": backup_path,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Переклассифицирует мусор в категории case "
            "(is_hidden=TRUE). Идемпотентный."
        )
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Только показать кандидатов и сохранить отчёт. "
             "Поведение по умолчанию.",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="Сообщить, что вы готовы реально записать is_hidden=TRUE. "
             "Дополнительно требуется --confirm-yes.",
    )
    parser.add_argument(
        "--confirm-yes", action="store_true",
        help="Подтверждение исполнения вместе с --confirm. "
             "Без обоих флагов скрипт работает в режиме dry-run.",
    )
    args = parser.parse_args()

    apply = bool(args.confirm and args.confirm_yes)
    if args.confirm and not args.confirm_yes:
        print(
            "ВНИМАНИЕ: --confirm указан без --confirm-yes. "
            "Скрипт продолжит в режиме DRY-RUN.",
            file=sys.stderr,
        )

    engine = _connect()
    try:
        result = reclassify(engine, apply=apply)
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
