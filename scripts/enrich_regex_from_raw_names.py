# CLI-скрипт обогащения характеристик компонентов через regex по raw_name
# из supplier_prices (этап 11.6.1).
#
# Отличия от scripts/enrich_regex.py:
#   - источник имён — все raw_name из supplier_prices, привязанные к компоненту,
#     плюс поле model компонента как fallback;
#   - агрегация по нескольким поставщикам: один компонент может иметь до 6
#     разных raw_name. Конфликты разрешаются по «самому длинному» (длиннее
#     обычно детальнее).
#   - в component_field_sources пишется source_detail='from_raw_name',
#     чтобы в аналитике различать два regex-источника.
#
# Старый scripts/enrich_regex.py (по полю name таблиц компонентов) остаётся
# рабочим без изменений.
#
# Примеры запуска:
#   python scripts/enrich_regex_from_raw_names.py
#   python scripts/enrich_regex_from_raw_names.py --dry-run
#   python scripts/enrich_regex_from_raw_names.py --category cpu
#   python scripts/enrich_regex_from_raw_names.py --supplier netlab
#   python scripts/enrich_regex_from_raw_names.py --component-id 12345

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Гарантируем, что корень проекта есть в sys.path при запуске из любой директории
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Загружаем .env ДО импорта app.*
from dotenv import load_dotenv

load_dotenv()

from app.services.enrichment.raw_name_runner import (
    ALL_SUPPLIER_CODES,
    format_report,
    run,
)

_ALL_CATEGORIES = ["cpu", "psu", "ram", "storage", "cooler", "gpu", "motherboard", "case"]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Regex-обогащение характеристик компонентов по raw_name из "
            "supplier_prices (этап 11.6.1)."
        ),
    )
    parser.add_argument(
        "--category",
        choices=_ALL_CATEGORIES + ["all"],
        default="all",
        help="Обработать только одну категорию (по умолчанию all).",
    )
    parser.add_argument(
        "--supplier",
        choices=list(ALL_SUPPLIER_CODES) + ["all"],
        default="all",
        help="Брать raw_name только от этого поставщика (по умолчанию all).",
    )
    parser.add_argument(
        "--component-id", type=int,
        help="Обработать ОДИН компонент (для отладки).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Показать, что МОГЛО БЫ быть записано — без записи в БД.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="Сколько компонентов между промежуточными commit'ами (по умолчанию 500).",
    )
    args = parser.parse_args()

    categories = _ALL_CATEGORIES if args.category == "all" else [args.category]
    supplier = None if args.supplier == "all" else args.supplier

    report = run(
        categories=categories,
        supplier=supplier,
        component_id=args.component_id,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
    )

    print()
    print(format_report(report, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
