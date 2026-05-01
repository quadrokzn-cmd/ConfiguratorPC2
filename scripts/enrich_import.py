# CLI-скрипт импорта результатов от Claude Code в БД (этап 2.5Б;
# расширен на 11.6.2.1: --file для точечного импорта;
# расширен на 11.6.2.3.3: --keep-source).
#
# Примеры запуска:
#   python scripts/enrich_import.py --category gpu
#   python scripts/enrich_import.py --all
#   python scripts/enrich_import.py --category gpu --dry-run
#   python scripts/enrich_import.py --file enrichment/done/gpu/batch_001_gpu_…
#   python scripts/enrich_import.py --category gpu --keep-source
#       (после успешного импорта файлы ОСТАЮТСЯ в done/, не переезжают в archive/;
#        используется при импорте на локали перед повторным импортом на проде
#        через railway ssh теми же файлами — этап 11.6.2.3.3).

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
    import_file,
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
        "--file", type=str, default=None,
        help="Импортировать один конкретный batch-файл (этап 11.6.2.1). "
             "Категория определяется из payload.category.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Показать, что будет сделано, без записи в БД и без переноса файлов.",
    )
    parser.add_argument(
        "--keep-source", action="store_true",
        help="Этап 11.6.2.3.3: после успешного импорта НЕ перемещать файлы "
             "из enrichment/done/<category>/ в enrichment/archive/<category>/. "
             "Use case: импорт на локали для теста, затем повторный импорт "
             "на проде через railway ssh теми же файлами.",
    )
    args = parser.parse_args()

    chosen = sum(bool(x) for x in (args.category, args.all, args.file))
    if chosen != 1:
        parser.error("Укажите ровно одно из: --category <cat> | --all | --file <path>.")

    if args.all:
        results = import_all(dry_run=args.dry_run, keep_source=args.keep_source)
    elif args.file:
        results = [import_file(
            Path(args.file),
            dry_run=args.dry_run,
            keep_source=args.keep_source,
        )]
    else:
        results = [import_category(
            args.category,
            dry_run=args.dry_run,
            keep_source=args.keep_source,
        )]

    exit_code = 0
    for r in results:
        print()
        print(format_report(r, dry_run=args.dry_run))
        if r.get("errors"):
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
