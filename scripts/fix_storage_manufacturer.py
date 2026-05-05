"""Восстановление и нормализация storages.manufacturer (этап 11.6.2.6.0b).

Объединяет два режима в одном скрипте, поскольку оба правят одно и
то же поле storages.manufacturer и разводить в два файла бессмысленно.

Зачем
-----

Аудит 6.0a показал две проблемы в storages.manufacturer:

1. **Регистровый разнобой 14 брендов** — у одного физического вендора
   manufacturer записан в нескольких канонических формах:

       WD / Western Digital            → Western Digital
       Seagate / SEAGATE               → Seagate
       Toshiba / TOSHIBA               → Toshiba
       ADATA / A-DATA                  → A-DATA
       Samsung / SAMSUNG /
                Samsung Electronics    → Samsung
       Patriot / PATRIOT               → Patriot
       Kingston / KINGSTON             → Kingston
       KINGSPEC / KingSpec /
                SHENZHEN KINGSPEC      → KingSpec
       KingPrice / KINGPRICE           → KingPrice
       Silicon Power / SILICON POWER   → Silicon Power
       Transcend / TRANSCEND           → Transcend
       AGI / AGI TECHNOLOGY            → AGI
       Netac / NETAC                   → Netac
       ТМИ / «Телеком и Микроэлектр…»  → ТМИ

   От разнобоя страдает AI-обогащение (вместо одной модели бренда AI
   видит несколько), GROUP BY и фильтры в admin-панели.

2. **315 storages в bucket 'unknown'** — это в основном ExeGate M.2
   Next/NextPro/NextPro+ + россыпь Samsung/WD/Seagate/Toshiba и пр.,
   у которых при первичной загрузке прайса бренд не извлёкся
   нормализатором, но в supplier_prices.raw_name явно присутствует.

Стратегия
---------

* `--recover`: для bucket 'unknown' прогоняем regex-паттерны 30+
   storage-брендов по конкатенации `model + supplier_prices.raw_name`.
   Какой первый совпал — тот и записываем (по образцу
   `recover_psu_manufacturer.py`).

* `--normalize`: маппинг канонических форм. Применяется к ВСЕМ
   видимым storages, у которых manufacturer попадает в одну из
   неканонических форм (например, manufacturer='WD' → 'Western Digital').

* `--apply`: запускает оба режима последовательно (recover, потом
   normalize) и применяет в БД. Это дефолтный сценарий батч-починки.

* `--dry-run` (по умолчанию): только отчёт, БД не меняется.

Защита: позиции, попавшие под `is_likely_non_storage` (рамки 2.5"→3.5",
card-reader, USB-hub), пропускаются в режиме recover — у них бренд
осмысленно не нужен (они скрываются отдельным reclassify-скриптом).

Запуск
------
  Локально (или через railway ssh) — dry-run по умолчанию:
    python scripts/fix_storage_manufacturer.py
    python scripts/fix_storage_manufacturer.py --dry-run
    python scripts/fix_storage_manufacturer.py --recover --dry-run
    python scripts/fix_storage_manufacturer.py --normalize --dry-run

  Боевой прогон (recover + normalize):
    python scripts/fix_storage_manufacturer.py --apply

  Боевой прогон только одного режима:
    python scripts/fix_storage_manufacturer.py --recover --apply
    python scripts/fix_storage_manufacturer.py --normalize --apply

Артефакты:
  scripts/reports/fix_storage_manufacturer_report.md
  scripts/reports/fix_storage_manufacturer_backup_YYYYMMDD.sql
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
from shared.component_filters import is_likely_non_storage


# ---------------------------------------------------------------------------
# Recover: regex-паттерны для извлечения бренда из raw_name + model.
# ---------------------------------------------------------------------------
# Порядок важен: длинные/составные бренды должны проверяться раньше
# коротких. Канонические имена в результирующем поле пишем в той форме,
# в которой их потом ожидает _NORMALIZE_MAP (см. ниже) — это позволяет
# в одном --apply прогоне сначала записать правильную каноническую,
# а на втором проходе нормализовать всё остальное.
_BRAND_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Составные / двусоставные бренды
    ("Western Digital", re.compile(r"\bWestern\s*Digital\b", re.IGNORECASE)),
    ("Silicon Power",   re.compile(r"\bSilicon\s*Power\b", re.IGNORECASE)),
    ("Solidigm",        re.compile(r"\bSolidigm\b", re.IGNORECASE)),
    ("KingSpec",        re.compile(r"\bShenzhen\s*KingSpec\b|\bKing\s*Spec\b", re.IGNORECASE)),
    ("KingPrice",       re.compile(r"\bKing\s*Price\b", re.IGNORECASE)),
    ("PC PET",          re.compile(r"\bPC[\s\-]?PET\b", re.IGNORECASE)),
    ("SanDisk",         re.compile(r"\bSan\s*Disk\b", re.IGNORECASE)),
    ("Hikvision",       re.compile(r"\bHikvision\b|\bHIKSEMI\b", re.IGNORECASE)),
    ("KIOXIA",          re.compile(r"\bKIOXIA\b", re.IGNORECASE)),
    ("Synology",        re.compile(r"\bSynology\b", re.IGNORECASE)),
    ("Foxline",         re.compile(r"\bFoxline\b", re.IGNORECASE)),
    ("ExeGate",         re.compile(r"\bExe\s*[Gg]ate\b", re.IGNORECASE)),
    ("Lenovo",          re.compile(r"\bLenovo\b", re.IGNORECASE)),
    ("Crucial",         re.compile(r"\bCrucial\b", re.IGNORECASE)),
    ("Apacer",          re.compile(r"\bApacer\b", re.IGNORECASE)),
    ("Patriot",         re.compile(r"\bPatriot\b", re.IGNORECASE)),
    ("Kingston",        re.compile(r"\bKingston\b", re.IGNORECASE)),
    ("Transcend",       re.compile(r"\bTranscend\b", re.IGNORECASE)),
    ("Samsung",         re.compile(r"\bSamsung\b", re.IGNORECASE)),
    ("Toshiba",         re.compile(r"\bToshiba\b", re.IGNORECASE)),
    ("Seagate",         re.compile(r"\bSeagate\b", re.IGNORECASE)),
    ("A-DATA",          re.compile(r"\bA[\s\-]?DATA\b|\bADATA\b", re.IGNORECASE)),
    ("Netac",           re.compile(r"\bNetac\b", re.IGNORECASE)),
    ("Digma",           re.compile(r"\bDigma\b", re.IGNORECASE)),
    ("KingSpec",        re.compile(r"\bKINGSPEC\b", re.IGNORECASE)),
    # WD как самостоятельное слово (короткий бренд) — после Western Digital,
    # чтобы не съесть его первое слово.
    ("Western Digital", re.compile(r"\bWD\b", re.IGNORECASE)),
    ("AGI",             re.compile(r"\bAGI\b", re.IGNORECASE)),
    ("MSI",             re.compile(r"\bMSI\b", re.IGNORECASE)),
    ("HP",              re.compile(r"\bHP\b", re.IGNORECASE)),
    ("ТМИ",             re.compile(r"\bТМИ\b|Телеком\s+и\s+Микроэлектр", re.IGNORECASE)),
)


# ---------------------------------------------------------------------------
# Normalize: маппинг неканонических manufacturer → каноническая форма.
# ---------------------------------------------------------------------------
# Ключи lower-case для регистронезависимого матчинга. Значения —
# каноническая форма, по которой бренд должен храниться в БД.
_NORMALIZE_MAP: dict[str, str] = {
    # WD / Western Digital → Western Digital
    "wd":                      "Western Digital",
    "western digital":         "Western Digital",
    # Seagate / SEAGATE → Seagate
    "seagate":                 "Seagate",
    # Toshiba / TOSHIBA → Toshiba
    "toshiba":                 "Toshiba",
    # ADATA / A-DATA → A-DATA
    "adata":                   "A-DATA",
    "a-data":                  "A-DATA",
    "a data":                  "A-DATA",
    # Samsung / SAMSUNG / Samsung Electronics → Samsung
    "samsung":                 "Samsung",
    "samsung electronics":     "Samsung",
    # Patriot / PATRIOT → Patriot
    "patriot":                 "Patriot",
    # Kingston / KINGSTON → Kingston
    "kingston":                "Kingston",
    # KINGSPEC / KingSpec / SHENZHEN KINGSPEC → KingSpec
    "kingspec":                "KingSpec",
    "shenzhen kingspec":       "KingSpec",
    # KingPrice / KINGPRICE → KingPrice
    "kingprice":               "KingPrice",
    # Silicon Power / SILICON POWER → Silicon Power
    "silicon power":           "Silicon Power",
    # Transcend / TRANSCEND → Transcend
    "transcend":               "Transcend",
    # AGI / AGI TECHNOLOGY → AGI
    "agi":                     "AGI",
    "agi technology":          "AGI",
    # Netac / NETAC → Netac
    "netac":                   "Netac",
    # ТМИ / «Телеком и Микроэлектроник…» → ТМИ
    "тми":                     "ТМИ",
    "телеком и микроэлектроник": "ТМИ",
    "телеком и микроэлектроника": "ТМИ",
}


# Префикс «Повреждение упаковки» — косметический брак, выкидываем перед
# регексом, чтобы не сбивал поиск бренда (по образцу recover_psu).
_DAMAGED_PREFIX_RE = re.compile(
    r"^(?:Повреждение\s+упр?аковки|Поврежденная\s+упаковк[аи])\s+",
    flags=re.IGNORECASE,
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


def _strip_damaged_prefix(text_: str) -> str:
    prev = None
    cur = text_
    while cur != prev:
        prev = cur
        cur = _DAMAGED_PREFIX_RE.sub("", cur, count=1)
    return cur


def _build_match_text(model: str | None, raw_names: list | None) -> str:
    parts: list[str] = []
    if model:
        parts.append(_strip_damaged_prefix(str(model)))
    for n in (raw_names or []):
        if n:
            parts.append(_strip_damaged_prefix(str(n)))
    return " | ".join(parts)


def _detect_brand(match_text: str) -> str | None:
    for canonical, pattern in _BRAND_PATTERNS:
        if pattern.search(match_text):
            return canonical
    return None


# ---------------------------------------------------------------------------
# Recover.
# ---------------------------------------------------------------------------

def _fetch_unknown_storages(engine) -> list:
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
                "  AND ( s.manufacturer IS NULL "
                "        OR LOWER(s.manufacturer) = 'unknown' ) "
                "GROUP BY s.id "
                "ORDER BY s.id ASC"
            )
        ).all()
    return rows


def find_recoveries(engine) -> list[dict]:
    rows = _fetch_unknown_storages(engine)
    out: list[dict] = []
    for r in rows:
        full = _build_match_text(r.model, r.raw_names)
        if not full.strip():
            continue
        # Не-накопители (рамки/card-reader/USB-hub) пропускаем — они
        # уйдут в reclassify_storage_misclassified.py с is_hidden=TRUE,
        # бренд им не нужен.
        if is_likely_non_storage(
            full, r.manufacturer,
            capacity_gb=r.capacity_gb,
            storage_type=r.storage_type,
        ):
            continue
        brand = _detect_brand(full)
        if brand is None:
            continue
        out.append({
            "id":         int(r.id),
            "old":        r.manufacturer,
            "new":        brand,
            "model":      r.model,
            "raw_sample": (r.raw_names or [None])[0],
        })
    return out


# ---------------------------------------------------------------------------
# Normalize.
# ---------------------------------------------------------------------------

def _fetch_all_visible_for_normalize(engine) -> list:
    """Все видимые storages, у которых manufacturer не None.
    Фильтрацию по картe нормализации делаем в Python (быстрее писать
    и проще тестировать, чем огромный CASE WHEN в SQL)."""
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT id, model, manufacturer "
                "FROM storages "
                "WHERE is_hidden = FALSE "
                "  AND manufacturer IS NOT NULL "
                "ORDER BY id ASC"
            )
        ).all()
    return rows


def find_normalizations(engine) -> list[dict]:
    rows = _fetch_all_visible_for_normalize(engine)
    out: list[dict] = []
    for r in rows:
        if r.manufacturer is None:
            continue
        key = str(r.manufacturer).strip().lower()
        if not key:
            continue
        target = _NORMALIZE_MAP.get(key)
        if target is None:
            # Иногда manufacturer содержит хвостовые токены типа
            # «Samsung Electronics Co., Ltd.» — пробуем prefix-match
            # на ключи карты длиной ≥3 слов.
            for k, v in _NORMALIZE_MAP.items():
                if " " in k and key.startswith(k):
                    target = v
                    break
        if target is None or target == r.manufacturer:
            continue
        out.append({
            "id":    int(r.id),
            "old":   r.manufacturer,
            "new":   target,
            "model": r.model,
        })
    return out


# ---------------------------------------------------------------------------
# Backup / report.
# ---------------------------------------------------------------------------

def _write_backup(
    recoveries: list[dict],
    normalizations: list[dict],
    *,
    reports_dir: Path,
) -> Path:
    today = datetime.now().strftime("%Y%m%d")
    out_path = reports_dir / f"fix_storage_manufacturer_backup_{today}.sql"
    if not recoveries and not normalizations:
        out_path.write_text(
            "-- backup: список пуст, откатывать нечего.\n",
            encoding="utf-8",
        )
        return out_path
    lines = [
        "-- Откат: вернуть manufacturer в исходные значения.\n",
        "-- Recover (старое значение или NULL → unknown).\n",
    ]
    for r in recoveries:
        old = r["old"]
        if old is None:
            lines.append(
                f"UPDATE storages SET manufacturer = NULL WHERE id = {r['id']};\n"
            )
        else:
            esc = str(old).replace("'", "''")
            lines.append(
                f"UPDATE storages SET manufacturer = '{esc}' WHERE id = {r['id']};\n"
            )
    lines.append("-- Normalize.\n")
    for r in normalizations:
        old = r["old"]
        esc = str(old).replace("'", "''")
        lines.append(
            f"UPDATE storages SET manufacturer = '{esc}' WHERE id = {r['id']};\n"
        )
    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path


def _write_report(
    recoveries: list[dict],
    normalizations: list[dict],
    *,
    applied: bool,
    reports_dir: Path,
    backup_path: Path | None,
    mode_label: str,
) -> Path:
    out_path = reports_dir / "fix_storage_manufacturer_report.md"
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    mode = "APPLY (записано в БД)" if applied else "DRY-RUN (БД не менялась)"

    lines: list[str] = [
        f"# Storage manufacturer fix — {mode_label} — {mode}",
        "",
        f"Дата запуска: {today}",
        "",
        f"Всего recover-кандидатов: **{len(recoveries)}**",
        f"Всего normalize-кандидатов: **{len(normalizations)}**",
    ]
    if backup_path is not None:
        lines.append(
            f"Бэкап для отката: `{backup_path.relative_to(reports_dir.parent.parent)}`"
        )
    lines.append("")

    if recoveries:
        by_brand: Counter = Counter(r["new"] for r in recoveries)
        lines += [
            "## Recover: топ восстановленных брендов",
            "",
            "| Бренд | Кол-во |",
            "|---|---:|",
        ]
        for brand, n in by_brand.most_common(20):
            lines.append(f"| {brand} | {n} |")
        lines.append("")

    if normalizations:
        by_pair: Counter = Counter(
            (r["old"], r["new"]) for r in normalizations
        )
        lines += [
            "## Normalize: топ маппингов",
            "",
            "| Было | Стало | Кол-во |",
            "|---|---|---:|",
        ]
        for (old, new), n in by_pair.most_common(20):
            lines.append(f"| {old} | {new} | {n} |")
        lines.append("")

    if recoveries:
        sample = recoveries[:30]
        lines += [
            f"## Примеры recover (первые {len(sample)} из {len(recoveries)})",
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

    if normalizations:
        sample = normalizations[:30]
        lines += [
            f"## Примеры normalize (первые {len(sample)} из {len(normalizations)})",
            "",
            "| ID | Было | Стало | Модель |",
            "|---:|---|---|---|",
        ]
        for r in sample:
            model = (r["model"] or "").replace("|", "\\|")
            if len(model) > 100:
                model = model[:97] + "..."
            lines.append(f"| {r['id']} | {r['old']} | {r['new']} | {model} |")
        lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Apply.
# ---------------------------------------------------------------------------

def _apply_recover(engine, recoveries: list[dict]) -> int:
    if not recoveries:
        return 0
    updated = 0
    with engine.begin() as conn:
        for r in recoveries:
            res = conn.execute(
                text(
                    "UPDATE storages SET manufacturer = :new "
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
        user_login="fix_storage_manufacturer.py",
        target_type="storage",
        target_id=f"bulk:{updated}",
        payload={
            "stage":  "11.6.2.6.0b",
            "reason": "manufacturer_recovery_from_raw_name",
            "by_brand": dict(Counter(r["new"] for r in recoveries)),
            "ids":    [r["id"] for r in recoveries][:200],
            "total":  updated,
        },
    )
    return updated


def _apply_normalize(engine, normalizations: list[dict]) -> int:
    if not normalizations:
        return 0
    updated = 0
    with engine.begin() as conn:
        for r in normalizations:
            res = conn.execute(
                text(
                    "UPDATE storages SET manufacturer = :new "
                    "WHERE id = :id AND manufacturer = :old"
                ),
                {"new": r["new"], "old": r["old"], "id": r["id"]},
            )
            updated += res.rowcount or 0
    write_audit(
        action=ACTION_COMPONENT_UPDATE,
        service="configurator",
        user_login="fix_storage_manufacturer.py",
        target_type="storage",
        target_id=f"bulk:{updated}",
        payload={
            "stage":  "11.6.2.6.0b",
            "reason": "manufacturer_normalization",
            "by_pair": {
                f"{old}->{new}": n
                for (old, new), n in Counter(
                    (r["old"], r["new"]) for r in normalizations
                ).items()
            },
            "ids":    [r["id"] for r in normalizations][:200],
            "total":  updated,
        },
    )
    return updated


def run(
    engine,
    *,
    do_recover: bool,
    do_normalize: bool,
    apply: bool,
    reports_dir: Path | None = None,
) -> dict:
    if reports_dir is None:
        reports_dir = Path(__file__).resolve().parent / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    # Recover ВСЕГДА выполняется до Normalize: так bucket 'unknown'
    # сначала превращается в каноническую форму, и Normalize видит
    # лишь старые рассинхронные значения.
    recoveries: list[dict] = []
    normalizations: list[dict] = []

    if do_recover:
        recoveries = find_recoveries(engine)

    recovered_n = 0
    if apply and recoveries:
        recovered_n = _apply_recover(engine, recoveries)

    if do_normalize:
        normalizations = find_normalizations(engine)

    normalized_n = 0
    if apply and normalizations:
        normalized_n = _apply_normalize(engine, normalizations)

    backup_path: Path | None = None
    if apply and (recoveries or normalizations):
        backup_path = _write_backup(
            recoveries, normalizations, reports_dir=reports_dir,
        )

    mode_label_parts = []
    if do_recover:
        mode_label_parts.append("recover")
    if do_normalize:
        mode_label_parts.append("normalize")
    mode_label = "+".join(mode_label_parts) or "noop"

    report_path = _write_report(
        recoveries,
        normalizations,
        applied=apply,
        reports_dir=reports_dir,
        backup_path=backup_path,
        mode_label=mode_label,
    )
    return {
        "recover_found":     len(recoveries),
        "recover_applied":   recovered_n,
        "normalize_found":   len(normalizations),
        "normalize_applied": normalized_n,
        "report_path":       report_path,
        "backup_path":       backup_path,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Восстанавливает (recover) и нормализует (normalize) "
            "storages.manufacturer. Идемпотентен."
        )
    )
    parser.add_argument(
        "--recover", action="store_true",
        help="Запустить только режим recover (bucket 'unknown' → бренд).",
    )
    parser.add_argument(
        "--normalize", action="store_true",
        help="Запустить только режим normalize (канонизация регистра).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Только показать кандидатов и сохранить отчёт. "
             "Поведение по умолчанию.",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Записать изменения в БД. Если не указаны --recover/--normalize, "
             "запускает оба режима последовательно (recover → normalize).",
    )
    parser.add_argument(
        "--report-path", type=Path, default=None,
        help="Каталог для отчётов (по умолчанию scripts/reports).",
    )
    args = parser.parse_args()

    apply = bool(args.apply)
    # Если ни --recover, ни --normalize не указаны — выполняем оба
    # (по дефолту как для dry-run, так и для apply).
    if not args.recover and not args.normalize:
        do_recover = True
        do_normalize = True
    else:
        do_recover = bool(args.recover)
        do_normalize = bool(args.normalize)

    engine = _connect()
    try:
        result = run(
            engine,
            do_recover=do_recover,
            do_normalize=do_normalize,
            apply=apply,
            reports_dir=args.report_path,
        )
    finally:
        engine.dispose()

    mode = "APPLY" if apply else "DRY-RUN"
    if do_recover:
        print(f"[{mode}] Recover-кандидатов: {result['recover_found']}")
        if apply:
            print(f"[{mode}] Recover применено: {result['recover_applied']}")
    if do_normalize:
        print(f"[{mode}] Normalize-кандидатов: {result['normalize_found']}")
        if apply:
            print(f"[{mode}] Normalize применено: {result['normalize_applied']}")
    if result["backup_path"]:
        print(f"[{mode}] Бэкап для отката: {result['backup_path']}")
    print(f"[{mode}] Отчёт: {result['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
