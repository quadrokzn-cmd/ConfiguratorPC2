"""Backfill: пометить fan-разветвители в категории cooler как is_hidden=TRUE.

Мини-этап 2026-05-13 (плана 2026-04-23-platforma-i-aukciony.md).

Зачем
-----
Триггер собственника: в подбор CPU-кулеров проскочил «ID-Cooling FS-04 ARGB» —
4-pin сплиттер питания для подключения нескольких корпусных вентиляторов к
одному разъёму материнки. Эвристика `is_likely_fan_splitter` (добавлена в
`shared/component_filters.py`) ловит такие позиции по словам «разветвитель»,
«сплиттер», «splitter», «удлинитель», «fan hub», «фан-хаб», «PWM hub»,
«fan controller», «fan switch», «multi-fan» и паттерну «3pin/4pin разъём».

Скрипт ищет видимые (`is_hidden = FALSE`) кулеры с этими маркерами и
помечает их `is_hidden = TRUE`. Идемпотентный — повторный запуск с --apply
после первого не меняет ничего (фильтр уже пропускает по `is_hidden = FALSE`).

Защитные слои встроены в саму эвристику:
  * _CPU_COOLER_HINTS — общий слой по CPU-маркерам (башня/tower/радиатор/
    heat-sink/liquid/aio/процессорн/water cool/cpu fan/cpu-cooler);
  * _FAN_SPLITTER_CPU_GUARDS — socket/AM4-5/LGA/low-profile/двухсекционный/
    TDP ≥50W;
  * дополнительно здесь: если у компонента уже непустой `supported_sockets`
    или `max_tdp_watts NOT NULL` — это CPU-кулер по данным AI-обогащения,
    скрипт его не трогает.

Применяется к конкатенации `model + manufacturer + array_agg(raw_name)`:
это даёт максимум контекста (raw_name из supplier_prices обычно полнее,
чем model, особенно для скелетов).

Запуск
------
  Dry-run (по умолчанию, только отчёт):
    python scripts/hide_fan_splitters_in_cooler.py
    python scripts/hide_fan_splitters_in_cooler.py --dry-run

  Боевой прогон (требует --apply):
    python scripts/hide_fan_splitters_in_cooler.py --apply

Артефакт: stdout-отчёт (кандидаты, manufacturer, model, raw_names);
без файлов в scripts/reports/ — задача узкая, отчёт читается глазами.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import create_engine, text  # noqa: E402

from shared.audit import write_audit  # noqa: E402
from shared.audit_actions import ACTION_COMPONENT_HIDE  # noqa: E402
from shared.component_filters import is_likely_fan_splitter  # noqa: E402

logger = logging.getLogger("hide_fan_splitters_in_cooler")


def _connect():
    db_url = os.environ.get("DATABASE_URL") or os.environ.get(
        "DATABASE_PUBLIC_URL"
    )
    if not db_url:
        raise RuntimeError(
            "DATABASE_URL/DATABASE_PUBLIC_URL не задан. Скопируйте .env.example "
            "в .env и проставьте подключение."
        )
    return create_engine(
        db_url, future=True,
        connect_args={"client_encoding": "utf8"},
    )


def _fetch_visible_coolers(engine) -> list:
    """Возвращает все видимые кулеры с агрегированным raw_names."""
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
    """Кандидат на скрытие, если эвристика is_likely_fan_splitter
    срабатывает на совокупности model + manufacturer + raw_names.

    Защита: компоненты с заполненным supported_sockets или max_tdp_watts —
    это CPU-кулеры по данным AI-обогащения, не трогаем.
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
    return is_likely_fan_splitter(full, row.manufacturer)


def find_candidates(engine) -> list:
    return [r for r in _fetch_visible_coolers(engine) if _is_candidate(r)]


def hide_candidates(engine, *, apply: bool) -> dict:
    candidates = find_candidates(engine)
    hidden = 0
    if apply and candidates:
        ids = [int(r.id) for r in candidates]
        with engine.begin() as conn:
            res = conn.execute(
                text(
                    "UPDATE coolers SET is_hidden = TRUE "
                    "WHERE id = ANY(:ids) AND is_hidden = FALSE"
                ),
                {"ids": ids},
            )
            hidden = res.rowcount or 0
        write_audit(
            action=ACTION_COMPONENT_HIDE,
            service="configurator",
            user_login="hide_fan_splitters_in_cooler.py",
            target_type="cooler",
            target_id=f"bulk:{hidden}",
            payload={
                "stage":  "2026-05-13-cooler-classification-fix",
                "reason": "fan_splitter_in_cooler",
                "ids":    ids[:200],
                "total":  hidden,
            },
        )
    return {"found": len(candidates), "hidden": hidden, "candidates": candidates}


def _print_report(candidates: list, *, apply: bool, hidden: int) -> None:
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] Кандидатов: {len(candidates)}")
    if apply:
        print(f"[{mode}] Помечено is_hidden=TRUE: {hidden}")
    if not candidates:
        return
    print()
    print("ID    | manufacturer    | model")
    print("------+-----------------+--------------------------------------------")
    for r in candidates:
        mfg = (r.manufacturer or "—")[:15]
        model = (r.model or "")[:80]
        print(f"{r.id:5d} | {mfg:<15s} | {model}")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(
        description=(
            "Backfill: помечает fan-разветвители / fan-хабы / fan-контроллеры "
            "в категории cooler is_hidden=TRUE. Идемпотентный."
        )
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Только показать кандидатов (поведение по умолчанию).",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Реально записать is_hidden=TRUE в БД.",
    )
    args = parser.parse_args()
    apply = bool(args.apply)

    engine = _connect()
    try:
        result = hide_candidates(engine, apply=apply)
    finally:
        engine.dispose()

    _print_report(result["candidates"], apply=apply, hidden=result["hidden"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
