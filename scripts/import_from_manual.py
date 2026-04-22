# CLI-скрипт импорта отредактированного CSV обратно в БД (этап 2.5В).
#
# Примеры запуска:
#   python scripts/import_from_manual.py --file data/manual_gpu.csv
#   python scripts/import_from_manual.py --file data/manual_gpu.csv --dry-run

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.services.manual_edit.importer import format_report, import_csv


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Импорт отредактированного CSV обратно в БД.",
    )
    parser.add_argument(
        "--file", required=True,
        help="Путь к CSV-файлу.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Показать, что будет сделано, без записи в БД.",
    )
    args = parser.parse_args()

    file_path = Path(args.file)
    stats = import_csv(file_path, dry_run=args.dry_run)

    print()
    print(format_report(stats, dry_run=args.dry_run))

    return 0 if stats["rows_errors"] == 0 and stats["fields_rejected"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
