# CLI-скрипт загрузки прайс-листа поставщика в базу данных.
# Поддерживаемые поставщики: OCS, Merlion, Treolan, Netlab, Ресурс Медиа,
# Green Place.
#
# Примеры запуска:
#   python scripts/load_price.py --file path/to/OCS_price.xlsx --supplier ocs
#   python scripts/load_price.py --file path/to/Прайслист_Мерлион.xlsm --supplier merlion
#   python scripts/load_price.py --file path/to/23_04_2026_catalog.xlsx --supplier treolan
#   python scripts/load_price.py --file path/to/DealerD.xlsx --supplier netlab
#   python scripts/load_price.py --file path/to/dealerd.zip --supplier netlab
#   python scripts/load_price.py --file path/to/price_struct.xlsx --supplier resurs_media
#   python scripts/load_price.py --file path/to/Price_GP_*.xlsx --supplier green_place
#
# Если --supplier не указан, скрипт попытается определить поставщика
# по имени файла (OCS → ocs, Merlion/Мерлион → merlion, Treolan/catalog → treolan,
# DealerD/netlab → netlab, price_struct/ресурс/медиа → resurs_media,
# Price_GP/green_place → green_place).

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

from portal.services.configurator.price_loaders import LOADERS, detect_loader, get_loader
from portal.services.configurator.price_loaders.orchestrator import load_price

logger = logging.getLogger(__name__)


def _resolve_supplier(filepath: str, explicit: str | None) -> str:
    """Возвращает ключ поставщика: либо то, что передано, либо авто-детект
    по имени файла. Если не получилось — печатает понятную ошибку и
    выходит с кодом 2."""
    if explicit:
        key = explicit.strip().lower()
        if key not in LOADERS:
            known = ", ".join(sorted(LOADERS.keys()))
            print(f"ОШИБКА: неизвестный --supplier «{explicit}». "
                  f"Поддерживаются: {known}.", file=sys.stderr)
            sys.exit(2)
        return key

    loader = detect_loader(filepath)
    if loader is None:
        known = ", ".join(sorted(LOADERS.keys()))
        print(
            "ОШИБКА: не удалось определить поставщика по имени файла. "
            f"Укажите --supplier явно (варианты: {known}).",
            file=sys.stderr,
        )
        sys.exit(2)
    # У loader.supplier_name — каноничное имя из suppliers ('OCS','Merlion','Treolan').
    # Превращаем в ключ CLI (lower).
    for key, cls in LOADERS.items():
        if isinstance(loader, cls):
            return key
    # Теоретически недостижимо.
    raise RuntimeError("Не удалось определить ключ поставщика по классу loader.")


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
        choices=sorted(LOADERS.keys()),
        default=None,
        help="Ключ поставщика. Если не указан — определяется по имени файла.",
    )
    args = parser.parse_args()

    if not Path(args.file).is_file():
        print(f"ОШИБКА: файл не найден: {args.file}", file=sys.stderr)
        return 2

    supplier_key = _resolve_supplier(args.file, args.supplier)

    filename = os.path.basename(args.file)
    supplier_name = get_loader(supplier_key).supplier_name
    print(f"Загружаю прайс-лист '{filename}' от поставщика {supplier_name}...")

    try:
        result = load_price(args.file, supplier_key=supplier_key)
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
    print(f"Поставщик:       {result['supplier']}")
    print(f"Статус:          {result['status']}")
    print(f"Всего строк:     {result['total_rows']}")
    print(f"Обработано:      {result['processed']}")
    print(f"  из них обновлено: {result['updated']}")
    print(f"  из них добавлено: {result['added']}")
    print(f"Пропущено:       {result['skipped']}")
    print(f"Ошибок:          {result['errors']}")
    print(
        f"На ручное сопоставление:  "
        f"ambiguous={result.get('unmapped_ambiguous', 0)}, "
        f"new={result.get('unmapped_new', 0)}"
    )
    print(f"ID загрузки:     {result['upload_id']}")

    if result["status"] == "failed":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
