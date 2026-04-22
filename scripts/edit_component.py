# CLI-скрипт точечных правок компонентов (этап 2.5В).
#
# Примеры:
#   python scripts/edit_component.py --show 1234
#   python scripts/edit_component.py --show 90YV0K60-M0NA00 --category gpu
#   python scripts/edit_component.py --update 1234 --field tdp_watts --value 220
#   python scripts/edit_component.py --add --category cpu
#   python scripts/edit_component.py --delete 1234

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from app.services.manual_edit.editor import (
    add_component_interactive,
    delete_one,
    find_component,
    format_component,
    update_one_field,
)
from app.services.manual_edit.schema import ALL_CATEGORIES


def _cmd_show(args) -> int:
    try:
        hit = find_component(args.show, category=args.category)
    except RuntimeError as exc:
        print(f"Ошибка: {exc}")
        return 1
    if hit is None:
        print(f"Не найден: {args.show!r}")
        return 1
    cat, row = hit
    print(format_component(cat, row))
    return 0


def _cmd_update(args) -> int:
    if not args.field or args.value is None:
        print("Укажите --field и --value для --update.")
        return 1
    result = update_one_field(
        args.update, args.field, args.value, category=args.category,
    )
    status = result.get("status")
    if status == "ok":
        print(f"Обновлено {result['category']}#{result['id']}.{result['field']} "
              f"(изменено полей: {len(result['changed'])})")
        return 0
    if status == "no_change":
        print(f"{result['category']}#{result['id']}.{result['field']}: "
              "значение совпадает с текущим — без изменений.")
        return 0
    if status == "rejected":
        print(f"Отклонено валидацией: {result['reason']}")
        return 1
    if status == "not_found":
        print(f"Компонент с id={result['id']} не найден.")
        return 1
    if status == "unknown_field":
        print(f"Поле {result['field']!r} неизвестно для категории "
              f"{result['category']}.")
        return 1
    if status == "empty_value":
        print("Пустое значение — ничего не сделано. "
              f"Чтобы очистить поле, передайте --value {'__CLEAR__'!r}.")
        return 1
    print(f"Неизвестный статус: {status}")
    return 1


def _cmd_add(args) -> int:
    if not args.category:
        print("Для --add обязателен --category.")
        return 1

    def _prompt(text: str) -> str:
        return input(text)

    result = add_component_interactive(args.category, _prompt)
    print(f"Создан {result['category']}#{result['id']}, "
          f"записано полей: {len(result['written'])}")
    return 0


def _cmd_delete(args) -> int:
    if not args.yes:
        ans = input(
            f"Удалить компонент id={args.delete} (каскадно: supplier_prices, "
            f"component_field_sources)? [да/нет]: "
        ).strip().lower()
        if ans not in {"да", "yes", "y"}:
            print("Отменено.")
            return 1

    result = delete_one(args.delete, category=args.category)
    if result["status"] == "not_found":
        print(f"Компонент id={args.delete} не найден.")
        return 1
    print(f"Удалено: {result['category']}#{result['id']}")
    print(f"  supplier_prices:            {result['deleted_prices']}")
    print(f"  component_field_sources:    {result['deleted_sources']}")
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Точечные правки компонентов.",
    )
    parser.add_argument("--show",   help="Показать компонент: --show <id|sku>")
    parser.add_argument("--update", type=int, help="Обновить одно поле: --update <id>")
    parser.add_argument("--field",  help="Имя поля для --update")
    parser.add_argument("--value",  help="Новое значение (для массивов — через '|'; "
                                         "'__CLEAR__' обнуляет поле)")
    parser.add_argument("--add",    action="store_true",
                        help="Интерактивно добавить новый компонент "
                             "(требуется --category).")
    parser.add_argument("--delete", type=int,
                        help="Удалить компонент по id.")
    parser.add_argument("--category", choices=ALL_CATEGORIES,
                        help="Категория (для --show по SKU, --update, --add, --delete).")
    parser.add_argument("--yes", action="store_true",
                        help="Не спрашивать подтверждение (для --delete).")

    args = parser.parse_args()

    modes = [bool(args.show), bool(args.update), bool(args.add), bool(args.delete)]
    if sum(modes) != 1:
        parser.error("Укажите ровно один режим: --show / --update / --add / --delete.")

    if args.show:
        return _cmd_show(args)
    if args.update:
        return _cmd_update(args)
    if args.add:
        return _cmd_add(args)
    if args.delete:
        return _cmd_delete(args)
    return 1


if __name__ == "__main__":
    sys.exit(main())
