"""CLI: импорт результатов от Claude Code из enrichment/auctions/done/ в БД (printers_mfu).

Этап 8 слияния (2026-05-08): импорты переехали в
`app.services.auctions.catalog.enrichment.importer`. Сканируется
`enrichment/auctions/done/`, обновляется `printers_mfu.attrs_jsonb`,
обработанные файлы переносятся в `enrichment/auctions/archive/<дата>/`.

Примеры:
    python scripts/auctions_enrich_import.py
    python scripts/auctions_enrich_import.py --dry-run
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from app.services.auctions.catalog.enrichment.importer import (
    format_report,
    import_done,
)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Импорт результатов обогащения от Claude Code в printers_mfu.attrs_jsonb."
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
