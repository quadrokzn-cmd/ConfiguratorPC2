"""CLI: загрузка прайса поставщика в БД через orchestrator.

Использование:
    python scripts/load_price.py --supplier ocs --file path/to/ocs.xlsx
    python scripts/load_price.py --file path/to/file.xlsx       # автоопределение
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules.auctions.price_loaders import detect_loader, get_loader  # noqa: E402
from app.modules.auctions.price_loaders.orchestrator import load_price  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Загрузка прайса поставщика")
    parser.add_argument("--supplier", help="код поставщика (merlion/ocs/treolan/...)")
    parser.add_argument("--file", required=True, help="путь к Excel-файлу")
    parser.add_argument("--user", default="cli", help="кто запустил (для price_uploads)")
    args = parser.parse_args()

    if args.supplier:
        loader = get_loader(args.supplier)
    else:
        loader = detect_loader(args.file)
        if loader is None:
            print(
                "Не удалось определить поставщика по имени файла. Укажите --supplier.",
                file=sys.stderr,
            )
            return 2

    print(f"Поставщик: {loader.supplier_code}")
    print(f"Файл:       {args.file}")
    report = load_price(args.file, loader=loader, uploaded_by=args.user)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report["status"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
