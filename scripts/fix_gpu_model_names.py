# Ревизор записей gpus.model на предмет «кривых» значений (этап 8.4).
#
# Живой баг: автоназвание конфигурации показало «RTX 1» вместо «GeForce 210».
# Первопричина оказалась в парсере (spec_naming._short_gpu_model), а не в
# данных — в gpus.model лежит полный описательный текст из прайса. Тем не
# менее скрипт пригождается, чтобы:
#   1) подтвердить гипотезу: в gpus.model нет подозрительных коротких
#      значений вроде «RTX 1», «GT 2» и т. п.;
#   2) пройтись по specification_items.auto_name и вывести список записей,
#      где GPU-блок всё-таки получился подозрительным — их менеджер может
#      переименовать вручную через custom_name.
#
# Запуск:
#   .venv/Scripts/python.exe scripts/fix_gpu_model_names.py --dry-run
# (скрипт всегда read-only: ничего не правит, только отчёт в stdout).

from __future__ import annotations

import argparse
import re
from pathlib import Path

from dotenv import load_dotenv

# load_dotenv ДО импорта app.config, иначе DATABASE_URL не увидится.
load_dotenv()

from sqlalchemy import create_engine, text

from app.config import settings  # noqa: E402


_SUSPICIOUS_MODEL_RE = re.compile(
    r"^\s*(RTX|GTX|GT|RX|HD|Radeon|Radeon RX|Arc)\s+\d{1,2}\s*$",
    re.IGNORECASE,
)


def _engine():
    return create_engine(
        settings.database_url,
        future=True,
        connect_args={"client_encoding": "utf8"},
    )


def _inspect_gpus_table(conn) -> dict:
    """Сколько записей в gpus.model подозрительные по регуляркам выше."""
    rows = conn.execute(
        text(
            "SELECT id, manufacturer, model, vram_gb, vram_type "
            "FROM gpus WHERE model IS NOT NULL"
        )
    ).all()
    suspicious: list[dict] = []
    very_short: list[dict] = []
    for r in rows:
        m = (r.model or "").strip()
        if len(m) < 4:
            very_short.append({
                "id": r.id, "model": r.model,
                "manufacturer": r.manufacturer,
            })
            continue
        if _SUSPICIOUS_MODEL_RE.match(m):
            suspicious.append({
                "id": r.id, "model": r.model,
                "manufacturer": r.manufacturer,
                "vram_gb": r.vram_gb, "vram_type": r.vram_type,
            })
    return {
        "total":      len(rows),
        "suspicious": suspicious,
        "very_short": very_short,
    }


def _inspect_spec_items(conn) -> list[dict]:
    """specification_items.auto_name с подозрительным GPU-фрагментом."""
    rows = conn.execute(
        text(
            "SELECT si.id, si.project_id, si.auto_name, si.custom_name, "
            "       p.name AS project_name "
            "FROM specification_items si "
            "JOIN projects p ON p.id = si.project_id "
            "WHERE si.auto_name ~ '(^|/\\s*)(RTX|GTX|GT|RX|HD|Radeon|Arc)\\s+\\d{1,2}(\\s*/|\\s*$)' "
            "ORDER BY si.id"
        )
    ).all()
    return [
        {
            "id":           r.id,
            "project_id":   r.project_id,
            "project_name": r.project_name,
            "auto_name":    r.auto_name,
            "custom_name":  r.custom_name,
        }
        for r in rows
    ]


def _write_backup(conn, path: Path) -> None:
    """Минимальный бэкап: pg_dump руками через COPY нам не подходит
    (нужен binary через psql), но для gpus хватает текстовой выгрузки
    полей, которые могли бы пострадать при массовом фиксе."""
    rows = conn.execute(
        text(
            "SELECT id, model, manufacturer, vram_gb, vram_type "
            "FROM gpus ORDER BY id"
        )
    ).all()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("-- Snapshot of gpus.model before 8.4 investigation\n")
        fh.write("-- Format: id | manufacturer | model | vram_gb | vram_type\n")
        for r in rows:
            fh.write(
                f"{r.id}\t{r.manufacturer!r}\t{r.model!r}\t"
                f"{r.vram_gb!r}\t{r.vram_type!r}\n"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="всегда включено — скрипт read-only, флаг для совместимости",
    )
    parser.add_argument(
        "--backup", type=Path,
        default=Path("scripts/reports/gpu_model_backup_before_8_4_fix.sql"),
        help="путь для текстового бэкапа gpus.model",
    )
    parser.add_argument(
        "--no-backup", action="store_true",
        help="не писать бэкап (для быстрых повторных прогонов)",
    )
    args = parser.parse_args()

    engine = _engine()
    try:
        with engine.connect() as conn:
            gpus = _inspect_gpus_table(conn)
            spec = _inspect_spec_items(conn)
            if not args.no_backup:
                _write_backup(conn, args.backup)
    finally:
        engine.dispose()

    print(f"[gpus] всего записей с model: {gpus['total']}")
    print(
        f"[gpus] подозрительных «маркер + 1-2 цифры»: "
        f"{len(gpus['suspicious'])}"
    )
    for s in gpus["suspicious"]:
        print(
            f"  id={s['id']} mfg={s['manufacturer']!r} "
            f"model={s['model']!r} vram={s['vram_gb']}{s['vram_type'] or ''}"
        )
    print(f"[gpus] слишком коротких (len<4): {len(gpus['very_short'])}")
    for s in gpus["very_short"]:
        print(f"  id={s['id']} mfg={s['manufacturer']!r} model={s['model']!r}")

    print(f"[specs] auto_name с подозрительным GPU-фрагментом: {len(spec)}")
    for s in spec:
        print(
            f"  spec_id={s['id']} project={s['project_id']} "
            f"({s['project_name']!r})"
        )
        print(f"    auto_name = {s['auto_name']!r}")
        if s["custom_name"]:
            print(f"    custom_name = {s['custom_name']!r}")

    if not args.no_backup:
        print(f"[backup] текстовый снимок gpus -> {args.backup}")

    print(
        "\nРекомендация: если в [gpus] 0 подозрительных — значит первопричина "
        "в парсере spec_naming._short_gpu_model, он уже исправлен в этапе 8.4 "
        "(word-boundary + маркер GeForce + safety net). Существующие "
        "auto_name — снимки; при желании менеджер переименовывает их через "
        "поле custom_name в UI."
    )


if __name__ == "__main__":
    main()
