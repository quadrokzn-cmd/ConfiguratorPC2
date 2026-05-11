"""Принудительный экспорт batch-файлов для PSU и MOTHERBOARD скелетов.

Exporter.py пропускает id, уже присутствующие в archive/, однако у этих позиций
в прошлой попытке импорта значения остались NULL. Создаём batch с этими
позициями, минуя idempotency-проверку.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from shared.db import SessionLocal
from portal.services.configurator.enrichment.claude_code.exporter import ENRICHMENT_ROOT
from portal.services.configurator.enrichment.claude_code.schema import TARGET_FIELDS


QUERIES = {
    "psu":         "SELECT id, manufacturer, sku, model, power_watts FROM psus WHERE power_watts IS NULL ORDER BY id",
    "motherboard": "SELECT id, manufacturer, sku, model, memory_type, has_m2_slot FROM motherboards WHERE memory_type IS NULL OR has_m2_slot IS NULL ORDER BY id",
}


def _make_item(row: dict, target_fields: list[str]) -> dict:
    current = {f: row.get(f) for f in target_fields}
    to_fill = [f for f, v in current.items() if v is None]
    current_present = {f: v for f, v in current.items() if v is not None}
    return {
        "id":           row["id"],
        "manufacturer": row.get("manufacturer") or "",
        "sku":          row.get("sku") or "",
        "model":        row.get("model") or "",
        "current":      current_present,
        "to_fill":      to_fill,
    }


def main():
    session = SessionLocal()
    try:
        for cat, q in QUERIES.items():
            pending_dir = ENRICHMENT_ROOT / "pending" / cat
            pending_dir.mkdir(parents=True, exist_ok=True)

            existing = list(pending_dir.glob("batch_*.json"))
            next_n = 1
            for p in existing:
                try:
                    num = int(p.stem.split("_")[1])
                    next_n = max(next_n, num + 1)
                except (ValueError, IndexError):
                    pass

            rows = session.execute(text(q)).mappings().all()
            if not rows:
                print(f"{cat}: 0 rows, skipped")
                continue

            target_fields = list(TARGET_FIELDS[cat])
            items = [_make_item(dict(r), target_fields) for r in rows]

            payload = {
                "category":     cat,
                "batch_id":     f"batch_{next_n:03d}",
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "target_fields": target_fields,
                "case_psu_pass": False,
                "items":        items,
                "_note":        "Force-exported (skipped archive/ idempotency) — stage 2.5B retry",
            }
            out = pending_dir / f"batch_{next_n:03d}.json"
            with out.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            print(f"{cat}: exported {len(items)} items to {out.name}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
