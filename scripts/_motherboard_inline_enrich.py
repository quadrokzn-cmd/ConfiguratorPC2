"""Этап 11.6.2.7: inline AI-обогащение 2 mining-плат AFOX (3 NULL-ячейки).

На 2026-05-05 у видимых motherboards остались 3 NULL-ячейки в 2 строках:
  id=378 AFOX AFHM65-ETH8EX  — chipset NULL, socket NULL
  id=379 AFOX AFB250-BTC12EX — chipset NULL (socket=LGA1151 уже корректен)

Объём настолько мал (2 платы), что обходимся без batch-pipeline:
данные собраны через WebSearch/WebFetch инлайн, валидаторы из
app/services/enrichment/claude_code/validators.py применяются ниже,
запись делается напрямую через psycopg2 — UPDATE motherboards +
upsert в component_field_sources с source='claude_code',
source_detail='from_web_search'.

Идемпотентность: WHERE chipset IS NULL — повторный прогон ничего
не меняет, для component_field_sources используется ON CONFLICT.

Запуск:
  Локально: python scripts/_motherboard_inline_enrich.py [--apply]
  На проде: cat scripts/_motherboard_inline_enrich.py | railway ssh -- python - --apply
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    # ImportError локально без python-dotenv; AssertionError на проде,
    # когда скрипт прилетает через stdin (`python -`) и find_dotenv()
    # не может определить вызывающий frame. В обоих случаях нам и не
    # нужно подгружать .env — DATABASE_URL уже в окружении.
    pass

import psycopg2

from app.services.enrichment.claude_code.validators import (
    ValidationError,
    validate_field,
)

# Hard-coded findings от inline AI-обогащения (WebSearch + WebFetch на
# afox-corp.com). Каждая запись подаётся в validate_field, который
# нормализует значение и проверяет, что source_url из whitelist
# (afox.eu/afox.ru/afox-corp.com уже в OFFICIAL_DOMAINS).
ENRICHMENTS = [
    # id=378 AFOX AFHM65-ETH8EX
    # chipset HM65 — прямая цитата spec-страницы:
    #   "Intel® HM65 + Intel® Celeron® Sandy Bridge / Ivy Bridge CPU"
    # socket — NULL: на странице сказано "CPU ON-BOARD, embedded
    #   Intel® Celeron® Sandy Bridge / Ivy Bridge Processor on-Board",
    #   отдельного процессорного socket физически нет, в spec'ах нет
    #   BGA-кода → защитный слой 2 промпта motherboard.md → null.
    {
        "id":         378,
        "field":      "chipset",
        "value":      "HM65",
        "source_url": "https://www.afox-corp.com/show-105-413-1.html",
    },
    # id=379 AFOX AFB250-BTC12EX
    # chipset B250 — конкретная карточка BTC12EX отсутствует на
    # afox-corp.com / afox.eu / afox.ru, но карточка близнеца
    # AFB250-ETH12EX (BTC и ETH-варианты — серия с одним PCB и
    # чипсетом, отличается лишь биос-профиль для майнинга) явно
    # указывает Chipset: "Intel® B250", Socket: "INTEL Socket 1151".
    # Socket в БД уже LGA1151, корректен. Префикс «AFB250» в номенклатуре
    # AFOX жёстко закодирован = Intel B250 (ср. AFHM65 = HM65, AFB85 = B85).
    {
        "id":         379,
        "field":      "chipset",
        "value":      "B250",
        "source_url": "https://www.afox-corp.com/index.php?m=content&c=index&a=show&catid=105&id=434",
    },
]


def _validate(record: dict):
    raw = {"value": record["value"], "source_url": record["source_url"]}
    return validate_field("motherboard", record["field"], raw)


def _fetch_current(cur, comp_id: int, field: str):
    cur.execute(
        f"SELECT {field} FROM motherboards WHERE id = %s AND is_hidden = FALSE",
        (comp_id,),
    )
    row = cur.fetchone()
    return row[0] if row else "MISSING"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Записать в БД. Без этого флага — dry-run.",
    )
    args = parser.parse_args(argv)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL не задан", file=sys.stderr)
        return 1
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] Inline AI-обогащение 2 mining-плат AFOX (этап 11.6.2.7)")
    print()

    written = 0
    for rec in ENRICHMENTS:
        comp_id = rec["id"]
        field = rec["field"]

        try:
            validated = _validate(rec)
        except ValidationError as exc:
            print(f"  id={comp_id} {field}: VALIDATION FAIL: {exc}")
            continue

        current = _fetch_current(cur, comp_id, field)
        if current == "MISSING":
            print(f"  id={comp_id}: SKIP — не найден или is_hidden=TRUE")
            continue
        if current is not None:
            print(f"  id={comp_id} {field}: SKIP — уже заполнено ({current!r})")
            continue

        print(f"  id={comp_id} {field}: {validated.value!r}  <-  {validated.source_url}")

        if args.apply:
            cur.execute(
                f"UPDATE motherboards SET {field} = %s "
                f"WHERE id = %s AND {field} IS NULL",
                (validated.value, comp_id),
            )
            cur.execute(
                """
                INSERT INTO component_field_sources
                    (category, component_id, field_name, source, confidence,
                     source_url, source_detail, updated_at)
                VALUES
                    ('motherboard', %s, %s, 'claude_code', 0.90,
                     %s, 'from_web_search', NOW())
                ON CONFLICT (category, component_id, field_name) DO UPDATE SET
                    source        = EXCLUDED.source,
                    confidence    = EXCLUDED.confidence,
                    source_url    = EXCLUDED.source_url,
                    source_detail = EXCLUDED.source_detail,
                    updated_at    = NOW()
                """,
                (comp_id, field, validated.source_url),
            )
            written += 1

    if args.apply:
        conn.commit()
    cur.close()
    conn.close()

    print()
    print(f"[{mode}] Записано полей: {written if args.apply else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
