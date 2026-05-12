"""Перевзвешивает `tenders.flags_jsonb.excluded_by_region` по уже
залитым лотам, опираясь на текущий стоп-лист `excluded_regions` и
канонизацию `portal/services/auctions/region_normalizer.canonical_region`.

Зачем: исторически до фикса 2026-05-13 ingest сравнивал
`customer_region` карточки с `excluded_regions.region_name` через
точное строковое равенство. Из-за разных форм («Магаданская обл» vs
«Магаданская область», «Саха (Якутия) Респ» vs «Якутия») лоты из
стоп-регионов не получали флаг и попадали в инбокс.

Скрипт обновляет ТОЛЬКО `flags_jsonb.excluded_by_region` /
`excluded_region_name` — остальные ключи (below_nmck_min,
rejected_by_price_per_unit, no_watchlist_ktru_in_card, и т.д.)
остаются нетронутыми. raw_html НЕ перепарсивается.

Запуск:
    # pre-prod
    python scripts/refresh_excluded_region_flag.py path/to/.env.preprod
    # prod
    python scripts/refresh_excluded_region_flag.py path/to/.env.prod

Использует ту же логику чтения DATABASE_URL / DATABASE_PUBLIC_URL,
что и discovery-скрипт: priority — DATABASE_URL, fallback —
DATABASE_PUBLIC_URL.

Идемпотентен: повторный запуск без изменений в excluded_regions /
customer_region ничего не меняет.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from dotenv import load_dotenv

env_file = sys.argv[1] if len(sys.argv) > 1 else None
if env_file:
    load_dotenv(env_file, override=True)
    print(f"loaded env: {env_file}")
else:
    load_dotenv()
    print("loaded env: .env (default)")

db_url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_PUBLIC_URL") or ""
if not db_url:
    print("ОШИБКА: не задан ни DATABASE_URL, ни DATABASE_PUBLIC_URL", file=sys.stderr)
    sys.exit(2)

print(f"DATABASE host: {db_url.split('@')[-1].split('/')[0] if '@' in db_url else '?'}")

from sqlalchemy import create_engine, text  # noqa: E402

from portal.services.auctions.region_normalizer import canonical_region  # noqa: E402


def main() -> int:
    engine = create_engine(
        db_url,
        future=True,
        connect_args={"client_encoding": "utf8"},
    )

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT region_code, region_name FROM excluded_regions "
                "WHERE excluded = TRUE"
            )
        ).all()
        excluded_canonicals: set[str] = {
            canonical_region(r.region_name) for r in rows if r.region_name
        }
        excluded_canonicals.discard("")

    print(f"стоп-регионов (canonical): {len(excluded_canonicals)}")
    for c in sorted(excluded_canonicals):
        print(f"  - {c}")

    with engine.connect() as conn:
        tenders = conn.execute(
            text(
                "SELECT reg_number, customer_region, "
                "       COALESCE(flags_jsonb, '{}'::jsonb) AS flags "
                "  FROM tenders"
            )
        ).all()

    set_count = 0
    cleared_count = 0
    unchanged = 0
    updates: list[dict[str, str]] = []

    for row in tenders:
        reg = row.reg_number
        region = row.customer_region or ""
        flags = dict(row.flags or {})

        canonical = canonical_region(region)
        should_flag = bool(canonical) and canonical in excluded_canonicals

        has_flag = bool(flags.get("excluded_by_region"))

        if should_flag and not has_flag:
            flags["excluded_by_region"] = True
            flags["excluded_region_name"] = region
            updates.append({"rn": reg, "flags": json.dumps(flags, ensure_ascii=False)})
            set_count += 1
        elif not should_flag and has_flag:
            flags.pop("excluded_by_region", None)
            flags.pop("excluded_region_name", None)
            updates.append({"rn": reg, "flags": json.dumps(flags, ensure_ascii=False)})
            cleared_count += 1
        else:
            unchanged += 1

    print(f"\nрезультат:")
    print(f"  всего лотов:                  {len(tenders)}")
    print(f"  нужно проставить флаг:        {set_count}")
    print(f"  нужно убрать флаг:            {cleared_count}")
    print(f"  без изменений:                {unchanged}")

    if not updates:
        print("\nИзменений нет — выход.")
        return 0

    # Применяем батчем по 100 для стабильности (см. memory:
    # remote_db_n1_pattern — не делаем per-row commit).
    BATCH = 100
    with engine.begin() as conn:
        for i in range(0, len(updates), BATCH):
            chunk = updates[i:i + BATCH]
            for u in chunk:
                conn.execute(
                    text(
                        "UPDATE tenders "
                        "   SET flags_jsonb = CAST(:flags AS JSONB), "
                        "       updated_at = NOW() "
                        " WHERE reg_number = :rn"
                    ),
                    u,
                )
            print(f"  применено {min(i + BATCH, len(updates))}/{len(updates)}")

    print(f"\nГотово: {len(updates)} строк обновлено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
