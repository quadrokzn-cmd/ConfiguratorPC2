"""Догоняет attrs_jsonb из printers_mfu.name через regex-парсер.

Идея: для каждого SKU парсит `name` через
`app.services.auctions.catalog.enrichment.name_parser.parse_printer_attrs`
и заполняет ТОЛЬКО ключи, где сейчас в attrs_jsonb стоит "n/a". Не
перезаписывает уже заполненные через Claude Code или вручную значения.

Источник пишется в `attrs_source`:
- если поле было пустое → 'regex_name'
- если уже было 'claude_code' → 'claude_code+regex_name' (или с
  существующим source через '+')
- 'manual' source НЕ затирается, к нему НЕ дописывается, чтобы ручные
  правки оставались каноничными

Запуск:
    python scripts/enrich_printers_mfu_from_names.py            # dry-run (по умолчанию)
    python scripts/enrich_printers_mfu_from_names.py --apply    # применить
    python scripts/enrich_printers_mfu_from_names.py --apply --verbose

Идемпотентен: повторный запуск не меняет ничего.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text

from app.database import SessionLocal
from app.services.auctions.catalog.enrichment.name_parser import parse_printer_attrs
from app.services.auctions.catalog.enrichment.schema import NA, PRINTER_MFU_ATTRS

logger = logging.getLogger("enrich_from_names")


REGEX_SOURCE = "regex_name"
MANUAL_SOURCE = "manual"


def _merge_attrs(
    current: dict[str, Any], parsed: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Возвращает (новый_attrs_jsonb, список_дописанных_ключей).

    Правило: для каждого ключа из parsed — если текущее значение == NA
    (или ключа нет в attrs_jsonb), записываем parsed[key]. Иначе
    оставляем как было.
    """
    new_attrs = dict(current)
    changed_keys: list[str] = []
    for key, value in parsed.items():
        if key not in PRINTER_MFU_ATTRS:
            continue  # парсер не вернёт лишнего, но на всякий случай
        existing = current.get(key, NA)
        if existing == NA:
            new_attrs[key] = value
            changed_keys.append(key)
    # Если в текущем attrs_jsonb отсутствуют какие-то schema-ключи
    # (старые записи могут быть неполными) — добавим их со значением NA,
    # чтобы layout был ровный.
    for key in PRINTER_MFU_ATTRS:
        if key not in new_attrs:
            new_attrs[key] = NA
    return new_attrs, changed_keys


def _merge_source(current_source: str | None, regex_added: bool) -> str | None:
    """Обновляет attrs_source при добавлении regex-значений.

    - manual → не трогаем (ручная правка важнее автоматики).
    - None / '' → 'regex_name'.
    - есть source без 'regex_name' → '<source>+regex_name'.
    - уже содержит 'regex_name' → не меняем.
    """
    if not regex_added:
        return current_source
    if current_source == MANUAL_SOURCE:
        return current_source
    if not current_source:
        return REGEX_SOURCE
    parts = current_source.split("+")
    if REGEX_SOURCE in parts:
        return current_source
    parts.append(REGEX_SOURCE)
    return "+".join(parts)


def run(*, apply: bool, verbose: bool) -> int:
    session = SessionLocal()
    rows = session.execute(
        text(
            "SELECT id, sku, name, brand, attrs_jsonb, attrs_source "
            "FROM printers_mfu "
            "ORDER BY id"
        )
    ).all()

    total_skus = len(rows)
    skus_to_update = 0
    keys_total = 0
    keys_distribution: Counter[str] = Counter()
    by_brand: Counter[str] = Counter()
    sample_changes: list[tuple[int, str, list[str]]] = []
    update_payloads: list[tuple[int, dict[str, Any], str | None]] = []

    for row in rows:
        current_attrs: dict[str, Any] = row.attrs_jsonb or {}
        parsed = parse_printer_attrs(row.name)
        if not parsed:
            continue
        new_attrs, changed_keys = _merge_attrs(current_attrs, parsed)
        if not changed_keys:
            continue
        skus_to_update += 1
        keys_total += len(changed_keys)
        keys_distribution.update(changed_keys)
        by_brand[row.brand or "—"] += 1
        new_source = _merge_source(row.attrs_source, regex_added=True)
        update_payloads.append((row.id, new_attrs, new_source))
        if verbose or len(sample_changes) < 20:
            sample_changes.append((row.id, row.sku or row.name[:60], changed_keys))

    print()
    print(f"=== Прогон: {'APPLY' if apply else 'DRY-RUN'} ===")
    print(f"SKU всего: {total_skus}")
    print(f"SKU будут обновлены: {skus_to_update}")
    print(f"Ключей суммарно: {keys_total}")
    print()
    print("Распределение по ключам:")
    for key in sorted(PRINTER_MFU_ATTRS.keys()):
        print(f"  {key:>26}: {keys_distribution.get(key, 0)}")
    print()
    print("Топ-15 brand с правками:")
    for brand, n in by_brand.most_common(15):
        print(f"  {brand:>20}: {n}")
    print()
    if sample_changes:
        print("Примеры (первые 20):")
        for sku_id, name_short, keys in sample_changes[:20]:
            print(f"  id={sku_id:<5} {name_short[:60]:<60} +keys={keys}")

    if not apply:
        print()
        print("Это был DRY-RUN. Для применения запустите с --apply.")
        session.close()
        return 0

    print()
    print(f"Применяю {len(update_payloads)} UPDATE-ов...")
    for sku_id, new_attrs, new_source in update_payloads:
        session.execute(
            text(
                "UPDATE printers_mfu "
                "SET attrs_jsonb = CAST(:attrs AS JSONB), "
                "    attrs_source = :src, "
                "    attrs_updated_at = NOW() "
                "WHERE id = :id"
            ),
            {
                "attrs": json.dumps(new_attrs, ensure_ascii=False),
                "src": new_source,
                "id": sku_id,
            },
        )
    session.commit()
    session.close()
    print("Готово.")
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Догоняет attrs_jsonb из printers_mfu.name через regex-парсер. "
            "Заполняет только n/a-ключи, не перезаписывает Claude Code."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Применить изменения (по умолчанию — dry-run).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Печатать все изменения, не только первые 20.",
    )
    args = parser.parse_args()
    return run(apply=args.apply, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
