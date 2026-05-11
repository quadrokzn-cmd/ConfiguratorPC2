# CLI-скрипт прогона derived-правил обогащения (этап 11.6.2.0).
#
# Derived-правила — это логические выводы, которые НЕ требуют веб-поиска
# и не являются простой regex-экстракцией:
#   1: cases.has_psu_included = FALSE по маркерам «без БП» в raw_name.
#   2: cases.included_psu_watts помечается not_applicable_no_psu в
#      component_field_sources, если has_psu_included = FALSE.
#   4: gpus.needs_extra_power = (tdp_watts > 75).
#   5: storages.storage_type = 'SSD', если interface = 'NVMe'.
#
# Примеры запуска:
#   python scripts/apply_derived_rules.py --dry-run
#   python scripts/apply_derived_rules.py
#   python scripts/apply_derived_rules.py --rule 1
#   python scripts/apply_derived_rules.py --category gpu

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from portal.services.configurator.enrichment.derived_rules import (  # noqa: E402
    all_rule_ids,
    format_report,
    rules_for_category,
    run,
)

_ALL_CATEGORIES = ("case", "gpu", "storage")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Derived-правила обогащения логических NULL "
            "(этап 11.6.2.0). "
            "Дополняет regex-обогащение из 11.6.1 без AI/веб-поиска."
        ),
    )
    parser.add_argument(
        "--category",
        choices=list(_ALL_CATEGORIES) + ["all"],
        default="all",
        help="Применять только правила этой категории (по умолчанию all).",
    )
    parser.add_argument(
        "--rule",
        choices=all_rule_ids() + ["all"],
        default="all",
        help="Применять только одно правило (по умолчанию all).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Показать, что МОГЛО БЫ быть записано — без записи в БД.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="Коммитим транзакцию каждые N записей (по умолчанию 500).",
    )
    args = parser.parse_args()

    if args.rule == "all":
        rules = all_rule_ids()
    else:
        rules = [args.rule]

    if args.category != "all":
        cat_rules = set(rules_for_category(args.category))
        rules = [r for r in rules if r in cat_rules]
        if not rules:
            print(
                f"Нет правил для категории {args.category!r} "
                f"(возможно, выбран --rule из другой категории).",
            )
            return 0

    report = run(rules=rules, dry_run=args.dry_run, batch_size=args.batch_size)

    print()
    print(format_report(report, dry_run=args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
