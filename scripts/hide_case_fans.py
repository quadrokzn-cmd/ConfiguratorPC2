"""Массовое скрытие корпусных вентиляторов в таблице coolers (этап 9А.2.1).

Контекст
--------
В прошлом загрузчики прайсов клали в категорию `cooler` и CPU-кулеры,
и корпусные вентиляторы (case fans) — это не проверяется ни на схеме,
ни в orchestrator. Корпусные вентиляторы засоряют выдачу подбора и
могут попасть в кандидатов CPU-кулера, хотя у них нет socket'а.

Скрипт ищет такие позиции по эвристикам (regex по name + отсутствие
socket_supported и max_tdp_watts) и помечает их `is_hidden = TRUE`.

Запуск
------
  Dry-run (по умолчанию, только отчёт, без записи):
    python scripts/hide_case_fans.py

  Реальное применение (запись + бэкап + отчёт):
    python scripts/hide_case_fans.py --apply

Артефакты:
  scripts/reports/case_fans_hidden_report.md   — итоговый отчёт
  scripts/reports/case_fans_backup_YYYYMMDD.sql — UPDATE для отката
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import create_engine, text


# Признаки «корпусного» вентилятора в названии:
#   - явные слова про корпус;
#   - модели типа AF120 / SP120 / PWM 120 / 140 — типичные размеры
#     корпусных вентиляторов без радиатора.
# Применяется только если у позиции одновременно:
#   - supported_sockets IS NULL (или пустой массив);
#   - max_tdp_watts IS NULL.
_CASE_FAN_KEYWORDS = re.compile(
    r"(корпусн[ыо]|case[\s\-]?fan|вентилятор\s+для\s+корпуса)",
    flags=re.IGNORECASE,
)
# Модельные паттерны (вне зависимости от слова «вентилятор»):
#   AF120, SP140, PWM 120 и т.п. встречаются в названиях корпусных
#   вентиляторов на 120/140 мм без радиатора.
_CASE_FAN_MODELS = re.compile(
    r"\b(?:AF|SP|PWM|ARGB|RGB)[\-\s]*1[24]0\b",
    flags=re.IGNORECASE,
)
# «Вентилятор» без явных CPU-маркеров (для отсева жёстких кулеров).
_GENERIC_FAN = re.compile(r"вентилятор", flags=re.IGNORECASE)
# Слова, которые точно говорят, что это CPU-кулер.
_CPU_COOLER_HINTS = re.compile(
    r"(процессор|cpu[\s\-]?cooler|башенн|tower|радиатор|heat[\s\-]?sink|"
    r"liquid|aio|жидкост|охлад\.\s*проц|water\s*cooling)",
    flags=re.IGNORECASE,
)


def _is_case_fan_candidate(row) -> bool:
    """Возвращает True, если позиция выглядит как корпусной вентилятор.

    Защитная логика: если есть socket_supported или max_tdp_watts —
    это CPU-кулер, и мы его НЕ трогаем (даже если в названии слово
    «вентилятор» — у некоторых производителей CPU-кулеры тоже так
    называются).
    """
    name = (row.model or "") + " " + (row.manufacturer or "")
    has_sockets = row.supported_sockets is not None and len(row.supported_sockets) > 0
    has_tdp = row.max_tdp_watts is not None
    if has_sockets or has_tdp:
        return False
    # Явные ключевые слова — самый надёжный сигнал.
    if _CASE_FAN_KEYWORDS.search(name):
        return True
    # Модель вида AF120/SP140/PWM 120 — корпусные вентиляторы.
    if _CASE_FAN_MODELS.search(name):
        return True
    # Просто «вентилятор» без CPU-маркеров и без socket — тоже корпусной.
    if _GENERIC_FAN.search(name) and not _CPU_COOLER_HINTS.search(name):
        return True
    return False


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


def _fetch_all_coolers(engine) -> list:
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, model, manufacturer, sku, supported_sockets, "
                "       max_tdp_watts, is_hidden "
                "FROM coolers "
                "WHERE is_hidden = FALSE "
                "ORDER BY id ASC"
            )
        ).all()
    return rows


def _write_backup(rows: list, *, reports_dir: Path) -> Path:
    """Пишет SQL для отката: UPDATE coolers SET is_hidden = FALSE WHERE id IN (...)."""
    today = datetime.now().strftime("%Y%m%d")
    out_path = reports_dir / f"case_fans_backup_{today}.sql"
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
    """Markdown-отчёт: статистика + 30-50 примеров + топ-производителей."""
    out_path = reports_dir / "case_fans_hidden_report.md"
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []
    mode = "APPLY (записано в БД)" if applied else "DRY-RUN (БД не менялась)"
    lines.append(f"# Скрытие корпусных вентиляторов — {mode}")
    lines.append("")
    lines.append(f"Дата запуска: {today}")
    lines.append("")
    lines.append(f"Всего найдено кандидатов: **{len(candidates)}**")
    if applied:
        lines.append(f"Помечено `is_hidden = TRUE`: **{len(candidates)}**")
    if backup_path:
        lines.append(f"Бэкап для отката: `{backup_path.relative_to(reports_dir.parent.parent)}`")
    lines.append("")

    # Топ-производителей.
    by_mfg: Counter = Counter()
    for r in candidates:
        by_mfg[r.manufacturer or "—"] += 1
    if by_mfg:
        lines.append("## Топ-производителей среди скрытых")
        lines.append("")
        lines.append("| Производитель | Кол-во |")
        lines.append("|---|---:|")
        for mfg, n in by_mfg.most_common(15):
            lines.append(f"| {mfg} | {n} |")
        lines.append("")

    # 30-50 примеров.
    sample = candidates[:50]
    if sample:
        lines.append(f"## Примеры (первые {len(sample)} из {len(candidates)})")
        lines.append("")
        lines.append("| ID | Производитель | Модель | SKU | sockets | TDP |")
        lines.append("|---:|---|---|---|---|---:|")
        for r in sample:
            socks = (
                ",".join(r.supported_sockets) if r.supported_sockets else "—"
            )
            tdp = r.max_tdp_watts if r.max_tdp_watts is not None else "—"
            model = (r.model or "").replace("|", "\\|")
            mfg = (r.manufacturer or "").replace("|", "\\|")
            sku = (r.sku or "").replace("|", "\\|")
            lines.append(
                f"| {r.id} | {mfg} | {model} | {sku} | {socks} | {tdp} |"
            )
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def find_case_fan_candidates(engine) -> list:
    """Применяет эвристики к coolers и возвращает список кандидатов."""
    rows = _fetch_all_coolers(engine)
    return [r for r in rows if _is_case_fan_candidate(r)]


def hide_case_fans(
    engine,
    *,
    apply: bool,
    reports_dir: Path | None = None,
) -> dict:
    """Главный сценарий. Возвращает {found, hidden, report_path, backup_path}.

    apply=False — только отчёт, БД не менялась.
    apply=True  — бэкап + UPDATE + отчёт.
    """
    if reports_dir is None:
        reports_dir = Path(__file__).resolve().parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    candidates = find_case_fan_candidates(engine)
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
        description="Скрывает корпусные вентиляторы в таблице coolers."
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Реально записать is_hidden=TRUE и сделать бэкап. "
             "Без флага — только dry-run и отчёт.",
    )
    args = parser.parse_args()

    engine = _connect()
    try:
        result = hide_case_fans(engine, apply=args.apply)
    finally:
        engine.dispose()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] Кандидатов: {result['found']}")
    if args.apply:
        print(f"[{mode}] Помечено is_hidden=TRUE: {result['hidden']}")
        if result["backup_path"]:
            print(f"[{mode}] Бэкап для отката: {result['backup_path']}")
    print(f"[{mode}] Отчёт: {result['report_path']}")
    if result["found"] > 500 and not args.apply:
        print(
            "ВНИМАНИЕ: найдено больше 500 кандидатов. "
            "Проверьте отчёт перед --apply."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
