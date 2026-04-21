# CLI-скрипт загрузки прайс-листа поставщика в базу данных.
# Поддерживаемые поставщики: OCS.
# Читает Excel-файл через app.services.price_loader и выводит итог в консоль.
#
# Пример запуска:
#   python scripts/load_price.py --file /path/to/ocs_price.xlsx

import argparse
import logging
import os
import sys
from pathlib import Path

# Гарантируем, что корень проекта есть в sys.path при запуске из любой директории
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Загружаем переменные окружения из .env ДО импорта app.*,
# чтобы app.config.settings получил реальный DATABASE_URL, а не дефолт.
from dotenv import load_dotenv
load_dotenv()

from app.services.price_loader import load_ocs_price

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Загрузчик прайс-листов поставщиков в БД."
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Путь к Excel-файлу прайс-листа.",
    )
    parser.add_argument(
        "--supplier",
        choices=["ocs"],
        default="ocs",
        help="Поставщик (сейчас поддерживается только 'ocs').",
    )
    args = parser.parse_args()

    if not Path(args.file).is_file():
        print(f"ОШИБКА: файл не найден: {args.file}", file=sys.stderr)
        return 2

    filename = os.path.basename(args.file)
    print(f"Загружаю прайс-лист '{filename}' от поставщика {args.supplier.upper()}...")

    try:
        result = load_ocs_price(args.file)
    except ValueError as exc:
        # Наши собственные ValueError (например, нет нужного листа в Excel).
        print(f"ОШИБКА: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        logger.exception("Необработанное исключение при загрузке прайса")
        print(f"Критическая ошибка при загрузке: {exc}", file=sys.stderr)
        return 1

    print()
    print("Загрузка завершена.")
    print(f"Статус:         {result['status']}")
    print(f"Всего строк:    {result['total_rows']}")
    print(f"Обработано:     {result['processed']}")
    print(f"  из них обновлено:  {result['updated']}")
    print(f"  из них добавлено:  {result['added']}")
    print(f"Пропущено:      {result['skipped']}")
    print(f"Ошибок:         {result['errors']}")
    print(f"ID загрузки:    {result['upload_id']}")

    if result["status"] == "failed":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
