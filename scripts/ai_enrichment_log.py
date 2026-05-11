"""Генератор CSV-лога AI-обогащения (этапы 2.5Б, 2.5В).

Читает component_field_sources с source IN ('claude_code','derived_from_name')
и выгружает в scripts/reports/ai_enrichment_log.csv.

Колонки: id, category, field, old_value, new_value, source_url,
confidence, agent_id, stage.

- old_value всегда "NULL" (политика: записываем только в NULL-поля).
- agent_id — хост source_url (whitelist-домен), либо "derived_from_name"
  если URL отсутствует.
- stage — "2.5b" / "2.5v" по датировке (rubеж 2026-04-24 17:00 MSK).
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
from portal.services.configurator.enrichment.base import CATEGORY_TO_TABLE


def main():
    out_path = Path(__file__).resolve().parent / "reports" / "ai_enrichment_log.csv"
    session = SessionLocal()
    try:
        # Фильтр по дате 2026-04-24 — покрывает 2.5Б и 2.5В (оба в одних сутках).
        # Рубеж (17:00 MSK) различает этапы через колонку stage ниже.
        rows = session.execute(text(
            "SELECT cfs.component_id AS id, cfs.category, cfs.field_name, "
            "       cfs.source, cfs.confidence, cfs.source_url, cfs.updated_at "
            "  FROM component_field_sources cfs "
            " WHERE cfs.source IN ('claude_code', 'derived_from_name') "
            "   AND cfs.updated_at >= '2026-04-24 00:00:00' "
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

        # Грубый рубеж: всё до 17:00 MSK 2026-04-24 = 2.5b; после = 2.5v.
        # Не идеально, но единственный маркер stage — updated_at, отдельного
        # поля в схеме не предусмотрено.
        from datetime import datetime
        STAGE_CUTOFF = datetime(2026, 4, 24, 17, 0, 0)

        with out_path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=",", quoting=csv.QUOTE_MINIMAL)
            writer.writerow([
                "id", "category", "field", "old_value", "new_value",
                "source_url", "confidence", "agent_id", "stage",
            ])
            for r in rows:
                new_value = new_value_cache.get(
                    (r["category"], r["id"], r["field_name"]), ""
                )
                source_url = r["source_url"] or ""
                parsed = urlparse(source_url)
                if r["source"] == "derived_from_name":
                    agent_domain = "derived_from_name"
                else:
                    agent_domain = parsed.hostname or "unknown"
                stage = "2.5v" if r["updated_at"] >= STAGE_CUTOFF else "2.5b"
                writer.writerow([
                    r["id"], r["category"], r["field_name"], "NULL", new_value,
                    source_url, r["confidence"], agent_domain, stage,
                ])

        print(f"OK: {len(rows)} rows saved to {out_path}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
