"""Этап 6/9 слияния QT↔C-PC2 — одноразовый скрипт миграции данных.

Переносит из БД `quadrotech` (заморожена с Этапа 1) в БД `kvadro_tech`:
  - 628 SKU из nomenclature → printers_mfu (id сохраняется тождественно).
  - ~943 строки supplier_prices QT → kvadro_tech.supplier_prices с
    подменой supplier_id (QT-имена → C-PC2-имена) и conversion price_rub
    → (price + currency='RUB').
  - matches.nomenclature_id — NOOP (id сохранены тождественно). Verify
    orphans=0 → создаёт миграцию 032_matches_fk.sql и применяет её.

Идемпотентен: повторный прогон NOOP (ON CONFLICT DO NOTHING + verify).

Маппинг supplier_id QT → C-PC2 строится по нормализованному name
(case-insensitive, hyphen→space). QT-поставщики без supplier_prices
(asbis/sandisk/marvel/a1tis) пропускаются — про них логируется один
INFO. Если на стороне QT есть supplier_prices с поставщиком, которого
нет в C-PC2 — этот supplier_id фиксируется в логе и в JSON-аудите,
строки пропускаются (не падаем, не пишем «висячий» FK).

Аудит: маппинг и сводный отчёт пишутся в
.business/_backups_2026-05-08-merge/qt_data_migration_report.json.

Использование:
    python scripts/migrate_qt_data_to_printers_mfu.py             # реальный прогон
    python scripts/migrate_qt_data_to_printers_mfu.py --dry-run   # только лог + JSON
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import psycopg2  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.database import engine as cpc2_engine  # noqa: E402

logger = logging.getLogger("migrate_qt_data_to_printers_mfu")


# Локальный QT-DSN: БД заморожена на той же машине, использует postgres-пользователя.
QT_DSN = os.getenv(
    "QT_DSN",
    "host=localhost dbname=quadrotech user=postgres",
)


REPORT_PATH = (
    Path(__file__).resolve().parent.parent
    / ".business"
    / "_backups_2026-05-08-merge"
    / "qt_data_migration_report.json"
)
NOMENCLATURE_MAPPING_PATH = (
    Path(__file__).resolve().parent.parent
    / ".business"
    / "_backups_2026-05-08-merge"
    / "qt_nomenclature_id_mapping.json"
)
FK_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "migrations"
    / "032_matches_fk.sql"
)


# --------------------------------------------------------------------------- #
# Хелперы                                                                      #
# --------------------------------------------------------------------------- #

_WS_RE = re.compile(r"[\s\-_]+")


def _normalize_supplier_name(raw: str) -> str:
    """ресурс-медиа / Ресурс Медиа / РесурсМедиа → 'ресурсмедиа'."""
    s = (raw or "").lower().replace("\xa0", " ")
    return _WS_RE.sub("", s)


def _build_supplier_map(qt_cur, cpc2_conn) -> tuple[dict[int, int], dict]:
    """Возвращает (supplier_id_qt → supplier_id_cpc2) и audit-dict.

    QT-поставщики без записи в C-PC2 → не попадают в map; информация
    об этом фиксируется в audit-dict, чтобы потом разобрать в рефлексии.
    """
    qt_cur.execute("SELECT id, code, name FROM suppliers ORDER BY id")
    qt_rows = qt_cur.fetchall()

    cpc2_rows = cpc2_conn.execute(
        text("SELECT id, name FROM suppliers ORDER BY id")
    ).all()

    cpc2_by_norm: dict[str, tuple[int, str]] = {
        _normalize_supplier_name(r.name): (int(r.id), r.name)
        for r in cpc2_rows
    }

    mapping: dict[int, int] = {}
    audit_matched: list[dict] = []
    audit_unmatched: list[dict] = []

    for qt_id, qt_code, qt_name in qt_rows:
        norm = _normalize_supplier_name(qt_name)
        match = cpc2_by_norm.get(norm)
        if match is None:
            audit_unmatched.append(
                {
                    "qt_id":   qt_id,
                    "qt_code": qt_code,
                    "qt_name": qt_name,
                }
            )
            continue
        cpc2_id, cpc2_name = match
        mapping[int(qt_id)] = cpc2_id
        audit_matched.append(
            {
                "qt_id":     qt_id,
                "qt_code":   qt_code,
                "qt_name":   qt_name,
                "cpc2_id":   cpc2_id,
                "cpc2_name": cpc2_name,
            }
        )

    return mapping, {"matched": audit_matched, "unmatched": audit_unmatched}


# --------------------------------------------------------------------------- #
# Шаг B: nomenclature → printers_mfu                                           #
# --------------------------------------------------------------------------- #

def _migrate_nomenclature(qt_cur, cpc2_conn, *, apply: bool) -> dict:
    """INSERT 628 QT.nomenclature → kvadro_tech.printers_mfu с сохранением id.

    После идемпотентен: ON CONFLICT (sku) DO NOTHING. После INSERT правит
    sequence, чтобы будущий BIGSERIAL не выдал id, занятый перенесёнными
    данными.
    """
    qt_cur.execute(
        """
        SELECT id, sku, mpn, gtin, brand, name, category,
               ktru_codes_array, attrs_jsonb, attrs_source,
               attrs_updated_at, cost_base_rub, margin_pct_target,
               price_updated_at, created_at
          FROM nomenclature
         WHERE category IN ('printer', 'mfu')
         ORDER BY id
        """
    )
    rows = qt_cur.fetchall()
    logger.info("QT nomenclature: вычитано %d строк (printer/mfu)", len(rows))

    inserted = 0
    skipped_conflict = 0

    for row in rows:
        (qt_id, sku, mpn, gtin, brand, name, category,
         ktru_codes_array, attrs_jsonb, attrs_source,
         attrs_updated_at, cost_base_rub, margin_pct_target,
         price_updated_at, created_at) = row

        # NOT NULL-защита: brand и name в QT номинально NULL-able, но в
        # printers_mfu NOT NULL. Если null — fallback на 'unknown'/sku.
        safe_brand = brand if brand else "unknown"
        safe_name = name if name else (mpn or sku)

        if not apply:
            inserted += 1
            continue

        result = cpc2_conn.execute(
            text(
                """
                INSERT INTO printers_mfu
                    (id, sku, mpn, gtin, brand, name, category,
                     ktru_codes_array, attrs_jsonb, attrs_source,
                     attrs_updated_at, cost_base_rub, margin_pct_target,
                     price_updated_at, created_at)
                VALUES
                    (:id, :sku, :mpn, :gtin, :brand, :name, :category,
                     :ktru, CAST(:attrs AS JSONB), :attrs_source,
                     :attrs_updated_at, :cost, :margin_pct,
                     :price_updated_at, :created_at)
                ON CONFLICT (sku) DO NOTHING
                RETURNING id
                """
            ),
            {
                "id":                qt_id,
                "sku":               sku,
                "mpn":               mpn,
                "gtin":              gtin,
                "brand":             safe_brand,
                "name":              safe_name,
                "category":          category,
                "ktru":              list(ktru_codes_array or []),
                "attrs":             json.dumps(attrs_jsonb or {}, ensure_ascii=False),
                "attrs_source":      attrs_source,
                "attrs_updated_at":  attrs_updated_at,
                "cost":              cost_base_rub,
                "margin_pct":        margin_pct_target,
                "price_updated_at":  price_updated_at,
                "created_at":        created_at,
            },
        ).first()
        if result is not None:
            inserted += 1
        else:
            skipped_conflict += 1

    if apply and inserted > 0:
        # Передвигаем sequence, чтобы новые BIGSERIAL не упирались в
        # перенесённые id. setval(... , true) → следующее nextval = max+1.
        cpc2_conn.execute(
            text(
                "SELECT setval('printers_mfu_id_seq', "
                "(SELECT COALESCE(MAX(id), 1) FROM printers_mfu), true)"
            )
        )

    return {
        "qt_rows":           len(rows),
        "inserted":          inserted,
        "skipped_conflict":  skipped_conflict,
    }


# --------------------------------------------------------------------------- #
# Шаг C: supplier_prices QT → kvadro_tech.supplier_prices                      #
# --------------------------------------------------------------------------- #

def _migrate_supplier_prices(
    qt_cur, cpc2_conn,
    *, supplier_map: dict[int, int], apply: bool,
) -> dict:
    """Переносит supplier_prices QT в C-PC2-формат.

    QT: (supplier_id, nomenclature_id, supplier_sku, price_rub, stock_qty,
         transit_qty, updated_at) → C-PC2: (supplier_id, category,
         component_id, supplier_sku, price, currency='RUB', stock_qty,
         transit_qty, raw_name=NULL, updated_at).

    category берётся из printers_mfu по component_id (= QT nomenclature_id).
    Идемпотентность — ON CONFLICT (supplier_id, category, component_id)
    DO NOTHING (повторный прогон ничего не перепишет — это OK для
    исторического data-only-переноса).
    """
    # Узнаём категории по printers_mfu — это наш единственный источник
    # правды для component_id печатных SKU после Этапа 6.
    rows_cat = cpc2_conn.execute(
        text("SELECT id, category FROM printers_mfu")
    ).all()
    cat_by_id: dict[int, str] = {int(r.id): r.category for r in rows_cat}

    qt_cur.execute(
        """
        SELECT supplier_id, nomenclature_id, supplier_sku,
               price_rub, stock_qty, transit_qty, updated_at
          FROM supplier_prices
         ORDER BY id
        """
    )
    rows = qt_cur.fetchall()
    logger.info("QT supplier_prices: вычитано %d строк", len(rows))

    inserted = 0
    skipped_no_supplier = 0
    skipped_no_component = 0
    skipped_conflict = 0

    for sup_id_qt, nom_id, sup_sku, price, stock, transit, updated_at in rows:
        cpc2_supplier_id = supplier_map.get(int(sup_id_qt))
        if cpc2_supplier_id is None:
            skipped_no_supplier += 1
            continue
        category = cat_by_id.get(int(nom_id))
        if category is None:
            skipped_no_component += 1
            continue

        if not apply:
            inserted += 1
            continue

        result = cpc2_conn.execute(
            text(
                """
                INSERT INTO supplier_prices
                    (supplier_id, category, component_id, supplier_sku,
                     price, currency, stock_qty, transit_qty,
                     raw_name, updated_at)
                VALUES
                    (:sid, :cat, :cid, :ssku, :price, 'RUB',
                     :stock, :transit, NULL, :upd)
                ON CONFLICT (supplier_id, category, component_id) DO NOTHING
                RETURNING id
                """
            ),
            {
                "sid":     cpc2_supplier_id,
                "cat":     category,
                "cid":     int(nom_id),
                "ssku":    sup_sku,
                "price":   price,
                "stock":   stock,
                "transit": transit,
                "upd":     updated_at,
            },
        ).first()
        if result is not None:
            inserted += 1
        else:
            skipped_conflict += 1

    return {
        "qt_rows":              len(rows),
        "inserted":             inserted,
        "skipped_no_supplier":  skipped_no_supplier,
        "skipped_no_component": skipped_no_component,
        "skipped_conflict":     skipped_conflict,
    }


# --------------------------------------------------------------------------- #
# Шаг D: matches.nomenclature_id — verify orphans + (опционально) UPDATE       #
# --------------------------------------------------------------------------- #

def _verify_matches(cpc2_conn) -> dict:
    """matches.nomenclature_id уже ссылается на QT-овские id. Поскольку мы
    сохранили id тождественно в printers_mfu (Шаг B), UPDATE не нужен.

    Просто проверяем, что orphans = 0. Если найдём orphans — фиксируем
    в audit-отчёте и не подключаем FK; рефлексия должна объяснить.
    """
    total = cpc2_conn.execute(
        text("SELECT count(*) FROM matches")
    ).scalar()
    orphans = cpc2_conn.execute(
        text(
            "SELECT count(*) FROM matches m "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM printers_mfu p WHERE p.id = m.nomenclature_id"
            ")"
        )
    ).scalar()
    return {"matches_total": int(total), "orphans": int(orphans)}


# --------------------------------------------------------------------------- #
# Шаг E: миграция 032 — FK matches.nomenclature_id → printers_mfu(id)          #
# --------------------------------------------------------------------------- #

_FK_MIGRATION_BODY = """\
-- =============================================================
-- Migration 032: FK matches.nomenclature_id -> printers_mfu(id)
-- (этап 6 слияния, см. migrations/031_printers_mfu.sql и
--  scripts/migrate_qt_data_to_printers_mfu.py).
--
-- Применяется ТОЛЬКО после переноса данных QT.nomenclature →
-- printers_mfu, иначе FK упадёт на orphans. Скрипт миграции
-- данных создаёт этот файл и сразу прогоняет apply_migrations.py.
--
-- Идемпотентно: ALTER TABLE ... ADD CONSTRAINT IF NOT EXISTS
-- НЕ существует в Postgres → используем DO-блок с проверкой.
-- =============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_matches_nomenclature_id'
          AND conrelid = 'matches'::regclass
    ) THEN
        ALTER TABLE matches
            ADD CONSTRAINT fk_matches_nomenclature_id
            FOREIGN KEY (nomenclature_id)
            REFERENCES printers_mfu (id)
            ON DELETE CASCADE;
    END IF;
END$$;
"""


def _ensure_fk_migration_file() -> None:
    """Записывает миграцию 032, если её ещё нет."""
    if FK_MIGRATION_PATH.exists():
        logger.info("Миграция 032 уже существует — оставляем как есть.")
        return
    FK_MIGRATION_PATH.write_text(_FK_MIGRATION_BODY, encoding="utf-8")
    logger.info("Создана миграция: %s", FK_MIGRATION_PATH)


def _apply_fk_migration() -> None:
    """Прогоняет apply_migrations.py — она применит 032 (если не применена)."""
    import subprocess

    result = subprocess.run(
        [sys.executable, "-m", "scripts.apply_migrations"],
        capture_output=True, text=True, encoding="utf-8",
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        raise RuntimeError("apply_migrations завершилась с ошибкой")


# --------------------------------------------------------------------------- #
# Главная функция                                                              #
# --------------------------------------------------------------------------- #

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Этап 6/9: перенос QT.nomenclature → printers_mfu + supplier_prices."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Не пишет в БД, только лог + JSON-аудит.",
    )
    args = parser.parse_args()

    apply = not args.dry_run
    print(f"Режим: {'APPLY' if apply else 'DRY-RUN'}")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    qt_conn = psycopg2.connect(QT_DSN)
    qt_conn.set_session(readonly=True)
    qt_cur = qt_conn.cursor()

    audit: dict = {"mode": "apply" if apply else "dry_run"}

    try:
        with cpc2_engine.begin() as cpc2_conn:
            # Шаг A: supplier mapping.
            supplier_map, supplier_audit = _build_supplier_map(qt_cur, cpc2_conn)
            audit["supplier_map"] = supplier_audit
            audit["supplier_map_size"] = len(supplier_map)
            logger.info(
                "Supplier mapping: matched=%d, unmatched=%d",
                len(supplier_audit["matched"]),
                len(supplier_audit["unmatched"]),
            )

            # Шаг B: nomenclature → printers_mfu.
            nomenclature_stats = _migrate_nomenclature(
                qt_cur, cpc2_conn, apply=apply,
            )
            audit["nomenclature"] = nomenclature_stats
            logger.info("nomenclature → printers_mfu: %s", nomenclature_stats)

            # Сохраняем mapping (тождественный) для аудита.
            if apply:
                rows = cpc2_conn.execute(
                    text(
                        "SELECT id FROM printers_mfu ORDER BY id"
                    )
                ).all()
                mapping_payload = {"identity": True, "ids": [int(r.id) for r in rows]}
                NOMENCLATURE_MAPPING_PATH.write_text(
                    json.dumps(mapping_payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            # Шаг C: supplier_prices.
            sp_stats = _migrate_supplier_prices(
                qt_cur, cpc2_conn,
                supplier_map=supplier_map, apply=apply,
            )
            audit["supplier_prices"] = sp_stats
            logger.info("supplier_prices: %s", sp_stats)

            # Шаг D: verify matches.
            match_stats = _verify_matches(cpc2_conn)
            audit["matches"] = match_stats
            logger.info("matches: %s", match_stats)

            if apply and match_stats["orphans"] != 0:
                # Не подключаем FK на orphans. Откатываемся, чтобы
                # сохранить инвариант «после Этапа 6 FK работает».
                raise RuntimeError(
                    f"Найдено {match_stats['orphans']} orphan-строк в matches — "
                    "FK не подключён, откат."
                )

        # Шаг E: создать и применить миграцию 032 (FK).
        if apply and audit["matches"]["orphans"] == 0:
            _ensure_fk_migration_file()
            _apply_fk_migration()
            # Verify FK constraint physically существует.
            with cpc2_engine.connect() as cpc2_conn:
                fk_exists = cpc2_conn.execute(
                    text(
                        "SELECT 1 FROM pg_constraint "
                        "WHERE conname = 'fk_matches_nomenclature_id' "
                        "  AND conrelid = 'matches'::regclass"
                    )
                ).first() is not None
            audit["fk_constraint_exists"] = fk_exists

    finally:
        qt_cur.close()
        qt_conn.close()

    REPORT_PATH.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"\nАудит: {REPORT_PATH}")
    print(json.dumps(audit, ensure_ascii=False, indent=2, default=str))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
