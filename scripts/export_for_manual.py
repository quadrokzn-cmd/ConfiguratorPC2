# CLI-скрипт выгрузки компонентов в CSV для ручного редактирования (этап 2.5В).
#
# Примеры запуска:
#   python scripts/export_for_manual.py --category gpu --output data/manual_gpu.csv
#   python scripts/export_for_manual.py --all --only-null --output data/
#   python scripts/export_for_manual.py --category case --only-null --output data/cases.csv

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from portal.services.configurator.manual_edit.exporter import export_all, export_category
from portal.services.configurator.manual_edit.schema import ALL_CATEGORIES


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Выгрузка компонентов в CSV для ручного редактирования.",
    )
    parser.add_argument(
        "--category", choices=ALL_CATEGORIES,
        help="Обработать одну категорию.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Выгрузить все категории в --output директорию "
             "(по файлу manual_<category>.csv на каждую).",
    )
    parser.add_argument(
        "--only-null", action="store_true",
        help="Выгружать только компоненты, у которых NULL хотя бы в одном "
             "обязательном поле категории.",
    )
    parser.add_argument(
        "--output", required=True,
        help="Путь к файлу CSV (для --category) или к директории (для --all).",
    )
    args = parser.parse_args()

    if not args.category and not args.all:
        parser.error("Укажите --category <cat> или --all.")
    if args.category and args.all:
        parser.error("Одновременно --category и --all указывать нельзя.")

    print()
    print("Выгрузка CSV для ручного редактирования")
    print("=" * 72)

    output = Path(args.output)

    if args.all:
        if output.suffix:
            parser.error("Для --all --output должен быть директорией (без расширения).")
        results = export_all(output, only_null=args.only_null)
    else:
        if output.is_dir() or not output.suffix:
            parser.error("Для --category --output должен быть путём к файлу .csv.")
        results = [export_category(args.category, output, only_null=args.only_null)]

    total = 0
    for r in results:
        if r.get("status") == "unknown_category":
            print(f"  {r['category']:12} неизвестная категория, пропущено")
            continue
        mark = " [only-null]" if r.get("only_null") else ""
        print(f"  {r['category']:12} строк: {r['rows']:5}  {r['path']}{mark}")
        total += r.get("rows", 0)

    print("-" * 72)
    print(f"Всего экспортировано строк: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
