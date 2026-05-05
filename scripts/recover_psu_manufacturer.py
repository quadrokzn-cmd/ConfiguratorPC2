"""Восстановление psus.manufacturer для bucket 'unknown' (этап 11.6.2.5.0b).

Зачем
-----
По итогам аудита 5.0a в категории psu обнаружились 234 NULL power_watts,
из которых 232 — в bucket manufacturer='unknown'. AI-обогащение не
заполнит power_watts, пока бренд неизвестен. Этот скрипт восстанавливает
manufacturer regex'ом по supplier_prices.raw_name (в supplier_prices нет
отдельной колонки бренда, проверено в ШАГ 1 этапа 5.0b).

Стратегия
---------
1. Сначала позиции, которые is_likely_psu_adapter (Gembird NPA-AC,
   KS-is, ORIENT PU-C/SAP-, BURO BUM-*, Ubiquiti POE, FSP FSP040,
   Бастион РАПАН и т. д.), пропускаются — они будут помечены
   is_hidden=TRUE отдельным скриптом reclassify_psu_misclassified.py.
2. Для оставшихся компонентов из bucket 'unknown' собираем
   raw_names + model в один текст и снимаем префикс «Повреждение
   упаковки» / «Поврежденная упаковка» / «Повреждение упраковки» (типичная
   опечатка поставщика).
3. По очереди прогоняем regex-паттерны 25 PSU-брендов из спеца этапа
   5.0b. Какой первый совпал — тот и записываем в manufacturer.
   Порядок паттернов выбран от более специфичных (Cooler Master,
   1STPLAYER, PcCooler, be quiet!) к простым (CBR, FSP, ACD), чтобы
   составные бренды не съели первое слово.
4. Если ни один паттерн не совпал — оставляем 'unknown' (никакой
   рандомной дефолт-простановки). Эти строки уйдут в техдолг 5.0c.

Запуск
------
  Локально (или через railway ssh) — dry-run по умолчанию:
    python scripts/recover_psu_manufacturer.py
    python scripts/recover_psu_manufacturer.py --dry-run

  Боевой прогон:
    python scripts/recover_psu_manufacturer.py --apply

Артефакты:
  scripts/reports/recover_psu_manufacturer_report.md
  scripts/reports/recover_psu_manufacturer_backup_YYYYMMDD.sql
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

from shared.audit import write_audit
from shared.audit_actions import ACTION_COMPONENT_UPDATE
from shared.component_filters import is_likely_psu_adapter


# Префиксы типа «Повреждение упаковки», «Повреждение упраковки»,
# «Поврежденная упаковка» — косметический брак, не относится к идентификации
# бренда. Удаляем перед матчингом.
_DAMAGED_PREFIX_RE = re.compile(
    r"^(?:Повреждение\s+упр?аковки|Поврежденная\s+упаковк[аи])\s+",
    flags=re.IGNORECASE,
)


# Список (canonical_name, regex). Порядок важен: длинные/составные бренды
# должны проверяться раньше коротких, иначе «Cooler Master» съест «Cooler».
# Канонические написания подобраны по самой частой форме в текущей БД
# (см. SELECT manufacturer, COUNT(*) FROM psus). Нормализация существующих
# вариантов регистра (DEEPCOOL/Deepcool, ZALMAN/Zalman) — отдельный этап
# 5.0c, в этом скрипте не делается.
_BRAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Cooler Master", re.compile(r"\bCooler\s*Master\b", re.IGNORECASE)),
    ("1STPLAYER",     re.compile(r"\b1\s*ST\s*PLAYER\b", re.IGNORECASE)),
    ("PCCOOLER",      re.compile(r"\bPC[\s\-]?Cooler\b", re.IGNORECASE)),
    ("be quiet!",     re.compile(r"\bbe\s*quiet!?", re.IGNORECASE)),
    ("Powercase",     re.compile(r"\bPowercase\b", re.IGNORECASE)),
    ("POWERMAN",      re.compile(r"\bPower[\s\-]?man\b", re.IGNORECASE)),
    ("CHIEFTEC",      re.compile(r"\bChie[fF]i?tec\b", re.IGNORECASE)),
    ("SEASONIC",      re.compile(r"\bSea\s*Sonic\b|\bSeasonic\b", re.IGNORECASE)),
    ("Thermaltake",   re.compile(r"\bThermaltake\b", re.IGNORECASE)),
    ("Deepcool",      re.compile(r"\bDeep\s*Cool\b", re.IGNORECASE)),
    ("ExeGate",       re.compile(r"\bExe\s*[Gg]ate\b", re.IGNORECASE)),
    ("Aerocool",      re.compile(r"\bAerocool\b", re.IGNORECASE)),
    ("Zalman",        re.compile(r"\bZalman\b", re.IGNORECASE)),
    ("Ginzzu",        re.compile(r"\bGinzzu\b", re.IGNORECASE)),
    ("Formula",       re.compile(r"\bFormula\b", re.IGNORECASE)),
    ("Foxline",       re.compile(r"\bFoxline\b", re.IGNORECASE)),
    ("Forza",         re.compile(r"\bForza\b", re.IGNORECASE)),
    ("Hiper",         re.compile(r"\bHiper\b", re.IGNORECASE)),
    ("Corsair",       re.compile(r"\bCorsair\b", re.IGNORECASE)),
    ("EVGA",          re.compile(r"\bEVGA\b", re.IGNORECASE)),
    ("Crown",         re.compile(r"\bCrown\b", re.IGNORECASE)),
    ("FSP",           re.compile(r"\bFSP\b", re.IGNORECASE)),
    ("XPG",           re.compile(r"\bXPG\b", re.IGNORECASE)),
    ("ACD",           re.compile(r"\bACD\b", re.IGNORECASE)),
    ("CBR",           re.compile(r"\bCBR\b", re.IGNORECASE)),
)


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


def _fetch_unknown_psus(engine) -> list:
    """Видимые psus с manufacturer='unknown'/NULL и (опционально)
    ещё незаполненной мощностью. Скрипт не ограничивается NULL
    power_watts, потому что unknown-bucket мог появиться раньше из-за
    отдельного бага загрузчика — даже если мощность уже AI-восстановили,
    бренд починить полезно."""
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT p.id, p.model, p.manufacturer, p.power_watts, "
                "       array_remove(array_agg(DISTINCT sp.raw_name), NULL) "
                "         AS raw_names "
                "FROM psus p "
                "LEFT JOIN supplier_prices sp "
                "  ON sp.component_id = p.id AND sp.category = 'psu' "
                "WHERE p.is_hidden = FALSE "
                "  AND ( p.manufacturer IS NULL "
                "        OR LOWER(p.manufacturer) = 'unknown' ) "
                "GROUP BY p.id "
                "ORDER BY p.id ASC"
            )
        ).all()
    return rows


def _strip_damaged_prefix(text_: str) -> str:
    """Убирает префикс «Повреждение упаковки» из текста имени.

    Префикс может встречаться 2-3 раза подряд (если составили имя из
    нескольких источников), поэтому применяем sub в цикле.
    """
    prev = None
    cur = text_
    while cur != prev:
        prev = cur
        cur = _DAMAGED_PREFIX_RE.sub("", cur, count=1)
    return cur


def _build_match_text(row) -> str:
    """Конкатенирует model + raw_names в одну строку для regex-матчинга,
    предварительно очистив каждый кусок от префикса «Повреждение упаковки».
    Manufacturer 'unknown' в матч-текст не добавляем — он не помогает.
    """
    parts: list[str] = []
    if row.model:
        parts.append(_strip_damaged_prefix(str(row.model)))
    for n in (row.raw_names or []):
        if n:
            parts.append(_strip_damaged_prefix(str(n)))
    return " | ".join(parts)


def _detect_brand(match_text: str) -> str | None:
    """Возвращает каноническое имя бренда или None."""
    for canonical, pattern in _BRAND_PATTERNS:
        if pattern.search(match_text):
            return canonical
    return None


def _is_eligible(row) -> bool:
    """True, если строку имеет смысл обрабатывать.

    False для адаптеров/POE/charger/dock-station — они уйдут в
    reclassify_psu_misclassified.py (is_hidden=TRUE), бренд им не нужен.
    """
    return not is_likely_psu_adapter(row.model, row.manufacturer)


def find_recoveries(engine) -> list[dict]:
    """Список словарей {id, old, new, model, raw_names_sample}
    для всех компонентов, у которых удалось восстановить бренд."""
    rows = _fetch_unknown_psus(engine)
    out: list[dict] = []
    for r in rows:
        if not _is_eligible(r):
            continue
        full = _build_match_text(r)
        if not full.strip():
            continue
        brand = _detect_brand(full)
        if brand is None:
            continue
        out.append({
            "id":      int(r.id),
            "old":     r.manufacturer,
            "new":     brand,
            "model":   r.model,
            "raw_sample": (r.raw_names or [None])[0],
        })
    return out


def _write_backup(rows: list[dict], *, reports_dir: Path) -> Path:
    today = datetime.now().strftime("%Y%m%d")
    out_path = reports_dir / f"recover_psu_manufacturer_backup_{today}.sql"
    if not rows:
        out_path.write_text(
            "-- backup: список пуст, откатывать нечего.\n",
            encoding="utf-8",
        )
        return out_path
    lines = ["-- Откат: вернуть восстановленные manufacturer в исходные значения.\n"]
    for r in rows:
        old = r["old"]
        if old is None:
            lines.append(
                f"UPDATE psus SET manufacturer = NULL WHERE id = {r['id']};\n"
            )
        else:
            esc = str(old).replace("'", "''")
            lines.append(
                f"UPDATE psus SET manufacturer = '{esc}' WHERE id = {r['id']};\n"
            )
    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path


def _write_report(
    recoveries: list[dict],
    *,
    applied: bool,
    reports_dir: Path,
    backup_path: Path | None,
) -> Path:
    out_path = reports_dir / "recover_psu_manufacturer_report.md"
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = "APPLY (записано в БД)" if applied else "DRY-RUN (БД не менялась)"

    lines: list[str] = [
        f"# Восстановление psus.manufacturer — {mode}",
        "",
        f"Дата запуска: {today}",
        "",
        f"Всего восстановлено: **{len(recoveries)}**",
    ]
    if backup_path is not None:
        lines.append(
            f"Бэкап для отката: `{backup_path.relative_to(reports_dir.parent.parent)}`"
        )
    lines.append("")

    by_brand: Counter = Counter(r["new"] for r in recoveries)
    if by_brand:
        lines += [
            "## Топ восстановленных брендов",
            "",
            "| Бренд | Кол-во |",
            "|---|---:|",
        ]
        for brand, n in by_brand.most_common(20):
            lines.append(f"| {brand} | {n} |")
        lines.append("")

    sample = recoveries[:50]
    if sample:
        lines += [
            f"## Примеры (первые {len(sample)} из {len(recoveries)})",
            "",
            "| ID | Стало | Модель |",
            "|---:|---|---|",
        ]
        for r in sample:
            model = (r["model"] or "").replace("|", "\\|")
            if len(model) > 100:
                model = model[:97] + "..."
            lines.append(f"| {r['id']} | {r['new']} | {model} |")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


def recover(
    engine,
    *,
    apply: bool,
    reports_dir: Path | None = None,
) -> dict:
    if reports_dir is None:
        reports_dir = Path(__file__).resolve().parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    recoveries = find_recoveries(engine)

    backup_path: Path | None = None
    updated = 0
    if apply and recoveries:
        backup_path = _write_backup(recoveries, reports_dir=reports_dir)
        with engine.begin() as conn:
            for r in recoveries:
                res = conn.execute(
                    text(
                        "UPDATE psus SET manufacturer = :new "
                        "WHERE id = :id "
                        "  AND ( manufacturer IS NULL "
                        "        OR LOWER(manufacturer) = 'unknown' )"
                    ),
                    {"new": r["new"], "id": r["id"]},
                )
                updated += res.rowcount or 0

        write_audit(
            action=ACTION_COMPONENT_UPDATE,
            service="configurator",
            user_login="recover_psu_manufacturer.py",
            target_type="psu",
            target_id=f"bulk:{updated}",
            payload={
                "stage":  "11.6.2.5.0b",
                "reason": "manufacturer_recovery_from_raw_name",
                "by_brand": dict(Counter(r["new"] for r in recoveries)),
                "ids":    [r["id"] for r in recoveries][:200],
                "total":  updated,
            },
        )

    report_path = _write_report(
        recoveries,
        applied=apply,
        reports_dir=reports_dir,
        backup_path=backup_path,
    )
    return {
        "found":       len(recoveries),
        "updated":     updated,
        "report_path": report_path,
        "backup_path": backup_path,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Восстанавливает psus.manufacturer для bucket 'unknown' "
            "regex'ом по supplier_prices.raw_name. Идемпотентен."
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
        result = recover(engine, apply=apply, reports_dir=args.report_path)
    finally:
        engine.dispose()

    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] Восстановлено бренда: {result['found']}")
    if apply:
        print(f"[{mode}] Записано в psus: {result['updated']}")
        if result["backup_path"]:
            print(f"[{mode}] Бэкап для отката: {result['backup_path']}")
    print(f"[{mode}] Отчёт: {result['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
