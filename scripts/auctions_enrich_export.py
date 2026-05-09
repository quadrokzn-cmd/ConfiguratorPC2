"""CLI: выгрузка SKU без атрибутов в JSON-батчи для Claude Code (printers_mfu).

Этап 8 слияния (2026-05-08): импорты переехали в
`app.services.auctions.catalog.enrichment.exporter`. Файлы пишутся в
`enrichment/auctions/pending/` отдельно от существующего C-PC2-enrichment
для ПК-компонентов (он живёт в `enrichment/pending/` и обслуживается
`scripts/enrich_export.py`).

Примеры:
    python scripts/auctions_enrich_export.py
    python scripts/auctions_enrich_export.py --brand pantum
    python scripts/auctions_enrich_export.py --brand pantum --batch-size 30
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.services.auctions.catalog.enrichment.exporter import export_pending


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Выгрузка незаполненных SKU printers_mfu в JSON-батчи для Claude Code."
    )
    parser.add_argument(
        "--brand", default=None,
        help="Обработать только один бренд (case-insensitive). По умолчанию — все.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=30,
        help="Сколько моделей в одном batch-файле (по умолчанию 30).",
    )
    args = parser.parse_args()

    files = export_pending(brand=args.brand, batch_size=args.batch_size)

    print()
    print("Выгрузка batch-файлов для Claude Code (printers_mfu)")
    print("=" * 72)
    if not files:
        print("Нечего выгружать: все SKU либо уже заполнены, либо уже в pending/done.")
        return 0
    for p in files:
        print(f"  {p.name}")
    print("-" * 72)
    print(f"Создано файлов: {len(files)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
