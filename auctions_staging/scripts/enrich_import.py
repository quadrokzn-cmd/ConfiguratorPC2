"""CLI: импорт результатов от Claude Code из enrichment/done/ в БД.

Примеры:
    python scripts/enrich_import.py
    python scripts/enrich_import.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.modules.auctions.catalog.enrichment.importer import (
    format_report,
    import_done,
)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Импорт результатов обогащения от Claude Code в nomenclature.attrs_jsonb."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Показать, что будет сделано, без записи в БД и без переноса файлов.",
    )
    args = parser.parse_args()

    report = import_done(dry_run=args.dry_run)
    print()
    print(format_report(report, dry_run=args.dry_run))
    return 1 if report.get("files_rejected") else 0


if __name__ == "__main__":
    sys.exit(main())
