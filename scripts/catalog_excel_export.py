# CLI-обёртка над сервисом portal/services/catalog/excel_export.py
# (Фаза 2 плана plans/2026-05-13-catalog-excel-export-import.md).
#
# Для разовых ручных запусков из админской консоли:
#   python scripts/catalog_excel_export.py --target both
#   python scripts/catalog_excel_export.py --target pc --output /tmp
#   python scripts/catalog_excel_export.py --target printers
#
# UI-эндпоинт /databases/catalog-excel/download/{pc|printers} использует
# тот же сервис, через CLI ходить не обязан.

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Гарантируем, что корень проекта есть в sys.path при запуске из любой
# директории (повторяем приём из scripts/enrich_export.py).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from portal.services.catalog.excel_export import (
    ExportReport,
    default_filename,
    export_components_pc,
    export_printers_mfu,
)


logger = logging.getLogger(__name__)


def _print_report(label: str, report: ExportReport) -> None:
    print(f"\n[{label}] файл: {report.file_path}")
    print(
        f"  курс ЦБ: {report.rate_used} "
        f"({'fallback' if report.rate_is_fallback else report.rate_date})"
    )
    for sheet, count in report.sheet_counts.items():
        print(f"  лист «{sheet}»: {count} строк")
    print(f"  итого строк: {report.total_rows}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Экспорт каталога в Excel (комплектующие ПК и/или печатная техника)."
    )
    parser.add_argument(
        "--target",
        choices=("pc", "printers", "both"),
        default="both",
        help="Какой файл выгружать: pc=Комплектующие_ПК, printers=Печатная_техника, both=оба.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path.cwd(),
        help="Директория для xlsx-файлов. По умолчанию — текущая директория.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    args.output.mkdir(parents=True, exist_ok=True)

    if args.target in ("pc", "both"):
        pc_path = args.output / default_filename("pc")
        _print_report("Комплектующие ПК", export_components_pc(pc_path))

    if args.target in ("printers", "both"):
        pr_path = args.output / default_filename("printers")
        _print_report("Печатная техника", export_printers_mfu(pr_path))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
