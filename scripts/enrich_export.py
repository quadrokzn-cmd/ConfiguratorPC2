# CLI-скрипт выгрузки незаполненных позиций в JSON-батчи для Claude Code
# (этап 2.5Б).
#
# Примеры запуска:
#   python scripts/enrich_export.py --category gpu --batch-size 40
#   python scripts/enrich_export.py --category case --case-psu-pass
#   python scripts/enrich_export.py --all --batch-size 50

import argparse
import logging
import sys
from pathlib import Path

# Гарантируем, что корень проекта есть в sys.path при запуске из любой директории
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.services.enrichment.claude_code.exporter import (
    export_all,
    export_category,
)
from app.services.enrichment.claude_code.schema import ALL_CATEGORIES


def _format_result(result: dict) -> str:
    cat = result.get("category", "?")
    status = result.get("status")
    if status == "unknown_category":
        return f"  {cat:14} неизвестная категория, пропущено"

    pass_marker = "  [2-й прогон: included_psu_watts]" if result.get("case_psu_pass") else ""
    line1 = (
        f"  {cat:14} кандидатов: {result.get('candidates', 0):4}  "
        f"уже выгружены: {result.get('skipped_known', 0):4}  "
        f"экспортировано: {result.get('exported', 0):4}  "
        f"батчей: {len(result.get('batches', []))}"
        f"{pass_marker}"
    )
    if result.get("batches"):
        files = ", ".join(result["batches"])
        line2 = f"      файлы: {files}"
        return line1 + "\n" + line2
    return line1


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Выгрузка незаполненных позиций в JSON-батчи для Claude Code.",
    )
    parser.add_argument(
        "--category", choices=ALL_CATEGORIES,
        help="Обработать только одну категорию.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Обработать все категории по очереди.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=None,
        help="Сколько позиций в одном batch-файле (по умолчанию — из схемы).",
    )
    parser.add_argument(
        "--case-psu-pass", action="store_true",
        help="Только для --category case: запустить 2-й прогон — выгрузить "
             "корпуса с has_psu_included=TRUE и пустым included_psu_watts.",
    )
    args = parser.parse_args()

    if not args.category and not args.all:
        parser.error("Укажите --category <cat> или --all.")
    if args.case_psu_pass and args.category != "case":
        parser.error("--case-psu-pass допустим только вместе с --category case.")
    if args.case_psu_pass and args.all:
        parser.error("--case-psu-pass нельзя комбинировать с --all.")

    print()
    print("Выгрузка batch-файлов для Claude Code")
    print("=" * 72)

    if args.all:
        results = export_all(batch_size=args.batch_size)
    else:
        results = [export_category(
            args.category,
            batch_size=args.batch_size,
            case_psu_pass=args.case_psu_pass,
        )]

    total_exported = 0
    for r in results:
        print(_format_result(r))
        total_exported += r.get("exported", 0)

    print("-" * 72)
    print(f"Всего экспортировано позиций: {total_exported}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
