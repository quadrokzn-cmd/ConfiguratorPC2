"""Бэкап затрагиваемых полей перед AI-обогащением (Этап 2.5Б).

Для каждой категории из TARGET_FIELDS генерирует UPDATE-операторы, возвращающие
текущие NULL-значения ключевых полей — достаточно для отката изменений.

Результат: scripts/reports/ai_enrichment_backup_YYYYMMDD.sql
"""

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from shared.db import SessionLocal
from portal.services.configurator.enrichment.base import CATEGORY_TO_TABLE
from portal.services.configurator.enrichment.claude_code.schema import CASE_PSU_WATTS_FIELD, TARGET_FIELDS


def _fmt_value(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        return "ARRAY[" + ",".join("'" + str(x).replace("'", "''") + "'" for x in v) + "]::text[]"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return "'" + str(v).replace("'", "''") + "'"


def main():
    out_path = Path(__file__).resolve().parent / "reports" / f"ai_enrichment_backup_{date.today().strftime('%Y%m%d')}.sql"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    session = SessionLocal()
    total_rows = 0
    try:
        with out_path.open("w", encoding="utf-8") as f:
            f.write("-- Backup: fields targeted by AI-enrichment stage 2.5B.\n")
            f.write(f"-- Generated: {date.today().isoformat()}\n")
            f.write("-- Restores targeted TARGET_FIELDS to state at backup (typically NULL).\n")
            f.write("-- Usage: psql -U postgres -h localhost -d kvadro_tech -f <this_file>\n\n")
            f.write("BEGIN;\n\n")

            for category, table in CATEGORY_TO_TABLE.items():
                fields = list(TARGET_FIELDS.get(category, []))
                if category == "case":
                    fields = fields + [CASE_PSU_WATTS_FIELD]
                if not fields:
                    continue

                cols = ["id"] + fields
                where_null = " OR ".join(f"{f} IS NULL" for f in fields)
                rows = session.execute(
                    text(f"SELECT {', '.join(cols)} FROM {table} WHERE {where_null} ORDER BY id")
                ).mappings().all()

                f.write(f"-- ============ {category} ({table}): {len(rows)} rows ============\n")
                for row in rows:
                    set_clause = ", ".join(f"{fn} = {_fmt_value(row[fn])}" for fn in fields)
                    f.write(f"UPDATE {table} SET {set_clause} WHERE id = {row['id']};\n")
                f.write("\n")
                total_rows += len(rows)

            f.write("COMMIT;\n")
    finally:
        session.close()

    print(f"OK: {total_rows} rows saved to {out_path}")


if __name__ == "__main__":
    main()
