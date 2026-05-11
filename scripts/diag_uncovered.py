"""Диагностический скрипт: выводит скелеты, у которых regex-обогащение
   НЕ извлекло нужные поля. Используется для поиска пробелов в паттернах."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from app.database import SessionLocal
from portal.services.configurator.enrichment.regex_sources import (
    case as case_src, cooler as cooler_src, gpu as gpu_src, storage as storage_src,
)


def dump(title, rows, needed_fields, extractor):
    print(f"\n===== {title} : missing = {needed_fields} =====")
    bad = []
    for r in rows:
        ext = extractor(r["model"])
        missed = [f for f in needed_fields if r.get(f) is None and f not in ext]
        if missed:
            bad.append((r["id"], r["manufacturer"], missed, r["model"]))
    print(f"total bad: {len(bad)}")
    for row in bad[:30]:
        print(f"  id={row[0]:5d} {row[1]:20s} miss={row[2]}")
        print(f"    model: {row[3][:200]}")


def main():
    s = SessionLocal()
    try:
        # CASE
        rows = s.execute(text("""
            SELECT id, manufacturer, model, supported_form_factors, has_psu_included, included_psu_watts
            FROM cases
            WHERE (supported_form_factors IS NULL OR has_psu_included IS NULL OR included_psu_watts IS NULL)
              AND EXISTS (SELECT 1 FROM supplier_prices sp WHERE sp.category='case' AND sp.component_id=cases.id AND sp.supplier_id IN (5,6))
        """)).mappings().all()
        dump("CASE", rows, ["supported_form_factors", "has_psu_included"], case_src.extract)

        # COOLER
        rows = s.execute(text("""
            SELECT id, manufacturer, model, supported_sockets, max_tdp_watts
            FROM coolers
            WHERE (supported_sockets IS NULL OR max_tdp_watts IS NULL)
              AND EXISTS (SELECT 1 FROM supplier_prices sp WHERE sp.category='cooler' AND sp.component_id=coolers.id AND sp.supplier_id IN (5,6))
        """)).mappings().all()
        dump("COOLER", rows, ["supported_sockets", "max_tdp_watts"], cooler_src.extract)

        # GPU
        rows = s.execute(text("""
            SELECT id, manufacturer, model, vram_gb, vram_type
            FROM gpus
            WHERE (vram_gb IS NULL OR vram_type IS NULL)
              AND EXISTS (SELECT 1 FROM supplier_prices sp WHERE sp.category='gpu' AND sp.component_id=gpus.id AND sp.supplier_id IN (5,6))
        """)).mappings().all()
        dump("GPU", rows, ["vram_gb", "vram_type"], gpu_src.extract)

        # STORAGE
        rows = s.execute(text("""
            SELECT id, manufacturer, model, storage_type, form_factor, interface, capacity_gb
            FROM storages
            WHERE (storage_type IS NULL OR form_factor IS NULL OR interface IS NULL OR capacity_gb IS NULL)
              AND EXISTS (SELECT 1 FROM supplier_prices sp WHERE sp.category='storage' AND sp.component_id=storages.id AND sp.supplier_id IN (5,6))
        """)).mappings().all()
        dump("STORAGE", rows, ["storage_type", "form_factor", "interface", "capacity_gb"], storage_src.extract)
    finally:
        s.close()


if __name__ == "__main__":
    main()
