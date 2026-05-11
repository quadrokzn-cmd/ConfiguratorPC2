# CLI-скрипт подбора конфигурации ПК (этап 3).
#
# Примеры использования:
#   python scripts/build_config.py --input data/request.json
#   cat data/request.json | python scripts/build_config.py --stdin
#   python scripts/build_config.py --example          # печатает пример запроса
#   python scripts/build_config.py --input r.json --json  # JSON-вывод вместо таблицы

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from portal.services.configurator.engine import (
    build_config,
    request_from_dict,
    result_to_dict,
)
from portal.services.configurator.engine.pretty import format_result


_EXAMPLE_REQUEST: dict = {
    "budget_usd": 1200,
    "cpu": {
        "min_cores": 6,
        "min_threads": 12,
        "min_base_ghz": 3.0,
    },
    "ram": {
        "min_gb": 16,
        "min_frequency_mhz": 3200,
    },
    "gpu": {
        "required": True,
        "min_vram_gb": 8,
    },
    "storage": {
        "min_gb": 500,
        "preferred_type": "SSD",
    },
}


def _load_request(args) -> dict:
    if args.example:
        return _EXAMPLE_REQUEST
    if args.input:
        with open(args.input, "r", encoding="utf-8") as f:
            return json.load(f)
    if args.stdin:
        return json.load(sys.stdin)
    raise SystemExit("Укажите --input FILE, --stdin или --example")


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Подбор конфигурации ПК по структурированным требованиям.",
    )
    parser.add_argument("--input", help="Путь к JSON-файлу с требованиями")
    parser.add_argument("--stdin", action="store_true",
                        help="Читать JSON из stdin")
    parser.add_argument("--example", action="store_true",
                        help="Использовать встроенный пример запроса")
    parser.add_argument("--json", action="store_true",
                        help="Вывести результат в JSON (по умолчанию — таблица)")
    parser.add_argument("--print-example", action="store_true",
                        help="Напечатать пример JSON-запроса и выйти")
    args = parser.parse_args()

    if args.print_example:
        print(json.dumps(_EXAMPLE_REQUEST, ensure_ascii=False, indent=2))
        return 0

    try:
        raw = _load_request(args)
    except FileNotFoundError as exc:
        print(f"Файл не найден: {exc.filename}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"Не удалось разобрать JSON: {exc}", file=sys.stderr)
        return 1

    req = request_from_dict(raw)
    result = build_config(req)

    if args.json:
        print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))
    else:
        print(format_result(result))

    return 0 if result.status in ("ok", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
