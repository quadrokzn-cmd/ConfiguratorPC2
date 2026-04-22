# CLI-скрипт обогащения через OpenAI Web Search (этап 2.5В).
#
# Режимы:
#   --new-only             обогатить только новые SKU (без записей в
#                          component_field_sources). Основной режим.
#   --category <c> --ids 1,2,3  точечно для конкретных компонентов.
#   --retry                повторить для позиций, помеченных openai_no_data.
#   --dry-run              показать план без запросов к API и без записи.
#
# Защита от трат:
#   - OPENAI_ENRICH_AUTO_LIMIT (дефолт 20): до этого числа — без вопросов;
#   - OPENAI_ENRICH_MAX        (дефолт 200): жёсткий потолок;
#   - между ними — запрос [да/нет] с показом оценочной стоимости в рублях.
#
# Флага --all намеренно нет (архитектурное решение): не прогоняем
# оставшиеся 975 NULL по всем компонентам, это работа для бизнес-ассистента.

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.services.enrichment.openai_search.runner import format_report, run
from app.services.enrichment.openai_search.schema import ALL_CATEGORIES


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Обогащение характеристик через OpenAI Web Search.",
    )
    parser.add_argument(
        "--new-only", action="store_true",
        help="Обогатить только SKU без записей в component_field_sources.",
    )
    parser.add_argument(
        "--retry", action="store_true",
        help="Повторить для позиций, помеченных openai_no_data.",
    )
    parser.add_argument(
        "--category", choices=ALL_CATEGORIES,
        help="Только для --ids: в какой категории искать.",
    )
    parser.add_argument(
        "--ids",
        help="Точечные id через запятую (напр. 1,2,3). Требует --category.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Показать план без вызовов API и без записи.",
    )
    args = parser.parse_args()

    modes_chosen = sum([args.new_only, args.retry, bool(args.ids)])
    if modes_chosen != 1:
        parser.error(
            "Укажите ровно один режим: --new-only / --retry / --category + --ids."
        )
    if args.ids and not args.category:
        parser.error("--ids требует --category.")

    if args.new_only:
        mode = "new_only"
        category = None
        ids = None
    elif args.retry:
        mode = "retry"
        category = None
        ids = None
    else:
        mode = "targeted"
        category = args.category
        try:
            ids = [int(s.strip()) for s in args.ids.split(",") if s.strip()]
        except ValueError:
            parser.error(f"--ids содержит нечисловое значение: {args.ids!r}")
        if not ids:
            parser.error("--ids не должен быть пустым.")

    stats = run(
        mode=mode,
        category=category,
        ids=ids,
        dry_run=args.dry_run,
        non_interactive=False,
    )
    print()
    print(format_report(stats))
    return 0 if not stats.errors else 1


if __name__ == "__main__":
    sys.exit(main())
