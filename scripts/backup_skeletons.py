"""Бэкап затрагиваемых полей компонентов перед regex-обогащением.

Для каждой категории генерирует UPDATE-операторы, возвращающие текущие
(NULL-) значения ключевых полей — достаточно для отката изменений.

Результат пишется в scripts/reports/skeletons_backup_YYYYMMDD.sql.
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
from portal.services.configurator.enrichment.runner import REQUIRED_FIELDS


def _fmt_value(v) -> str:
    """PostgreSQL-литерал для поля."""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        # TEXT[]
        inner = ",".join(
            "\"" + str(x).replace("\\", "\\\\").replace("\"", "\\\"") + "\"" for x in v
        )
        return "ARRAY[" + ",".join("'" + str(x).replace("'", "''") + "'" for x in v) + "]::text[]"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    return "'" + str(v).replace("'", "''") + "'"


def main():
    out_path = Path(__file__).resolve().parent / "reports" / f"skeletons_backup_{date.today().strftime('%Y%m%d')}.sql"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    session = SessionLocal()
    total_rows = 0
    try:
        with out_path.open("w", encoding="utf-8") as f:
            f.write("-- Бэкап затрагиваемых полей скелетов перед regex-обогащением.\n")
            f.write(f"-- Сгенерирован: {date.today().isoformat()}\n")
            f.write("-- Восстанавливает поля в их состояние на момент бэкапа (обычно NULL).\n")
            f.write("-- Использование: psql -U postgres -h localhost -d kvadro_tech -f <этот_файл>\n\n")
            f.write("BEGIN;\n\n")

            for category, table in CATEGORY_TO_TABLE.items():
                fields = REQUIRED_FIELDS.get(category, [])
                if not fields:
                    continue

                cols = ["id"] + list(fields)
                where_null = " OR ".join(f"{f} IS NULL" for f in fields)
                rows = session.execute(
                    text(f"SELECT {', '.join(cols)} FROM {table} WHERE {where_null} ORDER BY id")
                ).mappings().all()

                f.write(f"-- ============ {category} ({table}): {len(rows)} строк ============\n")
                for row in rows:
                    set_clause = ", ".join(f"{fn} = {_fmt_value(row[fn])}" for fn in fields)
                    f.write(f"UPDATE {table} SET {set_clause} WHERE id = {row['id']};\n")
                f.write("\n")
                total_rows += len(rows)

            f.write("COMMIT;\n")
    finally:
        session.close()

    print(f"OK: {total_rows} строк сохранено в {out_path}")


if __name__ == "__main__":
    main()
