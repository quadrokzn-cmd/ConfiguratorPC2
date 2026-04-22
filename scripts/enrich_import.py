# CLI-скрипт импорта результатов от Claude Code в БД (этап 2.5Б).
#
# Примеры запуска:
#   python scripts/enrich_import.py --category gpu
#   python scripts/enrich_import.py --all
#   python scripts/enrich_import.py --category gpu --dry-run

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.services.enrichment.claude_code.importer import (
    format_report,
    import_all,
    import_category,
)
from app.services.enrichment.claude_code.schema import ALL_CATEGORIES


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Импорт результатов обогащения от Claude Code в БД.",
    )
    parser.add_argument(
        "--category", choices=ALL_CATEGORIES,
        help="Импортировать только одну категорию.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Импортировать все категории по очереди.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Показать, что будет сделано, без записи в БД и без переноса файлов.",
    )
    args = parser.parse_args()

    if not args.category and not args.all:
        parser.error("Укажите --category <cat> или --all.")

    if args.all:
        results = import_all(dry_run=args.dry_run)
    else:
        results = [import_category(args.category, dry_run=args.dry_run)]

    exit_code = 0
    for r in results:
        print()
        print(format_report(r, dry_run=args.dry_run))
        if r.get("errors"):
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
