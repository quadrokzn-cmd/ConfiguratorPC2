"""Переклассификация корпусных вентиляторов в таблице coolers (этап 11.6.2.3.1).

Зачем
-----
В категории `cooler` копятся корпусные/системные вентиляторы (ARCTIC P12/P14,
Aerocool Frost 12, Pure Wings, Sickleflow, Noctua NF-A12, HPE ProLiant Fan
Kits и т.п.) — они НЕ являются процессорными кулерами, у них нет ни
`max_tdp_watts`, ни `supported_sockets`, и они засоряют выдачу подбора.

В этапе 9Г.1 для скелета загрузчика был сделан фильтр
`shared.component_filters.is_likely_case_fan`. На этапе 11.6.2.3.1 фильтр
расширен сериями конкретных вендоров (см. component_filters.py). Этот
скрипт идемпотентно прогоняет обновлённый фильтр по всем УЖЕ существующим
видимым кулерам, помечает кандидатов `is_hidden = TRUE` и пишет запись в
audit_log.

Защитный слой
-------------
Если у кулера уже непустой `supported_sockets` или `max_tdp_watts NOT NULL`
— это однозначно процессорный кулер, его фильтр не трогает (даже если в
имени есть «вентилятор» или совпала серия). Ошибка регрессии лучше, чем
потеря CPU-кулера из каталога.

Запуск
------
  Dry-run (по умолчанию, только отчёт):
    python scripts/reclassify_case_fans.py
    python scripts/reclassify_case_fans.py --dry-run

  Боевой прогон (требует явный двойной флаг):
    python scripts/reclassify_case_fans.py --confirm --confirm-yes

Артефакты:
  scripts/reports/reclassify_case_fans_report.md
  scripts/reports/reclassify_case_fans_backup_YYYYMMDD.sql
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
from shared.component_filters import is_likely_case_fan


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


def _fetch_visible_coolers_with_raw_names(engine) -> list:
    """Возвращает все видимые кулеры с агрегированным массивом raw_names
    из supplier_prices. Кулеры без raw_names тоже включаются (LEFT JOIN)."""
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT c.id, c.model, c.manufacturer, c.sku, "
                "       c.supported_sockets, c.max_tdp_watts, "
                "       array_remove(array_agg(DISTINCT sp.raw_name), NULL) "
                "         AS raw_names "
                "FROM coolers c "
                "LEFT JOIN supplier_prices sp "
                "  ON sp.component_id = c.id AND sp.category = 'cooler' "
                "WHERE c.is_hidden = FALSE "
                "GROUP BY c.id "
                "ORDER BY c.id ASC"
            )
        ).all()
    return rows


def _is_candidate(row) -> bool:
    """True, если row выглядит как корпусной/системный/notebook вентилятор.

    Защита: если у компонента уже есть сокеты или TDP — НЕ трогаем.
    Иначе применяем is_likely_case_fan к конкатенации model + manufacturer
    + всех raw_names. Это важно: иногда название в БД (model) короткое,
    а в raw_name у поставщика — расширенное «Cooler ARCTIC P12 PWM PST».
    """
    has_sockets = (
        row.supported_sockets is not None and len(row.supported_sockets) > 0
    )
    has_tdp = row.max_tdp_watts is not None
    if has_sockets or has_tdp:
        return False

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
    return is_likely_case_fan(full, row.manufacturer)


def find_candidates(engine) -> list:
    rows = _fetch_visible_coolers_with_raw_names(engine)
    return [r for r in rows if _is_candidate(r)]


def _write_backup(rows: list, *, reports_dir: Path) -> Path:
    today = datetime.now().strftime("%Y%m%d")
    out_path = reports_dir / f"reclassify_case_fans_backup_{today}.sql"
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
        f"UPDATE coolers SET is_hidden = FALSE WHERE id IN ({chunks});\n",
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
    out_path = reports_dir / "reclassify_case_fans_report.md"
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    mode = "APPLY (записано в БД)" if applied else "DRY-RUN (БД не менялась)"
    lines.append(f"# Переклассификация корпусных вентиляторов — {mode}")
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
        lines.append("| ID | Производитель | Модель | SKU |")
        lines.append("|---:|---|---|---|")
        for r in sample:
            model = (r.model or "").replace("|", "\\|")
            mfg = (r.manufacturer or "").replace("|", "\\|")
            sku = (r.sku or "").replace("|", "\\|")
            lines.append(f"| {r.id} | {mfg} | {model} | {sku} |")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def reclassify(
    engine,
    *,
    apply: bool,
    reports_dir: Path | None = None,
) -> dict:
    """Главный сценарий. Возвращает {found, hidden, report_path, backup_path}."""
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
                    "UPDATE coolers SET is_hidden = TRUE "
                    "WHERE id = ANY(:ids) AND is_hidden = FALSE"
                ),
                {"ids": ids},
            )
            hidden_count = res.rowcount or 0
        # Один общий audit-event на массовое обновление, чтобы не плодить
        # 1 запись на каждый id. target_id = коннект-строка с количеством.
        write_audit(
            action=ACTION_COMPONENT_HIDE,
            service="configurator",
            user_login="reclassify_case_fans.py",
            target_type="cooler",
            target_id=f"bulk:{hidden_count}",
            payload={
                "stage":  "11.6.2.3.1",
                "reason": "case_fan_reclassification",
                "ids":    ids[:200],  # обрезаем, чтобы не раздуть JSON
                "total":  hidden_count,
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
            "Переклассифицирует корпусные вентиляторы в категории cooler "
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
