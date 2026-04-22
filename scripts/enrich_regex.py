# CLI-скрипт обогащения характеристик компонентов через регулярные выражения.
#
# Примеры запуска:
#   python scripts/enrich_regex.py --category cpu
#   python scripts/enrich_regex.py --all
#   python scripts/enrich_regex.py --category cpu --dry-run

import argparse
import logging
import sys
from pathlib import Path

# Гарантируем, что корень проекта есть в sys.path при запуске из любой директории
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Загружаем .env ДО импорта app.*
from dotenv import load_dotenv
load_dotenv()

from app.services.enrichment.runner import run_for_category
from app.services.enrichment.report import format_report

# Порядок категорий зафиксирован в плане Этапа 2.5А (от простого к сложному).
_ALL_CATEGORIES = ["cpu", "psu", "ram", "storage", "cooler", "gpu", "motherboard", "case"]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Обогащение характеристик компонентов через регулярные выражения.",
    )
    parser.add_argument(
        "--category", choices=_ALL_CATEGORIES,
        help="Обработать только одну категорию.",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Обработать все 8 категорий по очереди.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Показать, что будет сделано, без записи в БД.",
    )
    args = parser.parse_args()

    if not args.category and not args.all:
        parser.error("Укажите --category <cat> или --all.")

    categories = _ALL_CATEGORIES if args.all else [args.category]

    exit_code = 0
    any_success = False
    for cat in categories:
        print()
        try:
            stats = run_for_category(cat, dry_run=args.dry_run)
        except Exception as exc:
            print(f"ОШИБКА при обработке категории {cat}: {exc}", file=sys.stderr)
            exit_code = 1
            continue

        print(format_report(stats, dry_run=args.dry_run))
        if stats.get("status") == "success":
            any_success = True

    if not any_success and exit_code == 0:
        # Все указанные категории либо ещё не реализованы, либо завершились ошибкой
        return 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
