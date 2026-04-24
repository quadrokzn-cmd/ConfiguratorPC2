"""Генератор CSV-лога AI-обогащения (этап 2.5Б).

Читает component_field_sources с source='claude_code' и выгружает в
scripts/reports/ai_enrichment_log.csv.

Колонки: id, category, field, old_value, new_value, source_url, confidence, agent_id.

old_value всегда "NULL" (политика: записываем только в NULL-поля).
agent_id извлекается из path source_url (домен) — это не агент-id, а домен источника,
но в контексте 2.5Б это единственный сигнал, по какому whitelist-кластеру сработал агент.
"""

import csv
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.database import SessionLocal
from app.services.enrichment.base import CATEGORY_TO_TABLE


def main():
    out_path = Path(__file__).resolve().parent / "reports" / "ai_enrichment_log.csv"
    session = SessionLocal()
    try:
        # По умолчанию — записи за последний час (актуальный прогон Этапа 2.5Б);
        # для общего аудита можно расширить интервал или убрать where-клозу.
        rows = session.execute(text(
            "SELECT cfs.component_id AS id, cfs.category, cfs.field_name, "
            "       cfs.source, cfs.confidence, cfs.source_url, cfs.updated_at "
            "  FROM component_field_sources cfs "
            " WHERE cfs.source = 'claude_code' "
            "   AND cfs.updated_at >= now() - interval '1 hour' "
            " ORDER BY cfs.updated_at DESC, cfs.category, cfs.component_id, cfs.field_name"
        )).mappings().all()

        # Вытащим new_value из таблицы компонента — иначе CSV неинформативен.
        new_value_cache: dict[tuple[str, int, str], str] = {}
        for r in rows:
            cat = r["category"]
            table = CATEGORY_TO_TABLE.get(cat)
            if not table:
                continue
            key = (cat, r["id"], r["field_name"])
            if key in new_value_cache:
                continue
            val = session.execute(
                text(f"SELECT {r['field_name']} FROM {table} WHERE id = :id"),
                {"id": r["id"]},
            ).scalar()
            if val is None:
                new_value_cache[key] = ""
            elif isinstance(val, list):
                new_value_cache[key] = "|".join(str(x) for x in val)
            else:
                new_value_cache[key] = str(val)

        with out_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=",", quoting=csv.QUOTE_MINIMAL)
            writer.writerow([
                "id", "category", "field", "old_value", "new_value",
                "source_url", "confidence", "agent_id",
            ])
            for r in rows:
                new_value = new_value_cache.get(
                    (r["category"], r["id"], r["field_name"]), ""
                )
                parsed = urlparse(r["source_url"] or "")
                agent_domain = parsed.hostname or "unknown"
                writer.writerow([
                    r["id"], r["category"], r["field_name"], "NULL", new_value,
                    r["source_url"] or "", r["confidence"], agent_domain,
                ])

        print(f"OK: {len(rows)} rows saved to {out_path}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
