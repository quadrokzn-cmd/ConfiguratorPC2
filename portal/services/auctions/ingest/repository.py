from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loguru import logger
from sqlalchemy import Engine, text

from portal.services.auctions.ingest.card_parser import TenderCard


@dataclass(frozen=True)
class PlatformSettings:
    nmck_min_rub: float
    max_price_per_unit_rub: float
    # Названия регионов в форме, как они хранятся в `excluded_regions.region_name`.
    # Матчинг идёт по канонической форме — см. filters.py / region_normalizer.py.
    excluded_region_names: frozenset[str]
    ktru_watchlist: tuple[tuple[str, str], ...]


def load_settings(engine: Engine) -> PlatformSettings:
    """Load platform thresholds + filters from DB. All editable from UI per principle 9.

    `ktru_watchlist` — пары `(code, display_name)`. `display_name` нужен для параметра
    `ktruCodeNameList=<КОД>&&&<НАЗВАНИЕ>` zakupki-поиска (миграция 0009). Если в БД
    `display_name` пуст — fallback на сам код, чтобы не падать на старых строках.
    """
    sql_settings = text("SELECT key, value FROM settings")
    sql_regions = text("SELECT region_name FROM excluded_regions WHERE excluded = TRUE")
    sql_watchlist = text(
        "SELECT code, display_name FROM ktru_watchlist "
        "WHERE is_active = TRUE ORDER BY code"
    )
    with engine.connect() as conn:
        rows = {r.key: r.value for r in conn.execute(sql_settings)}
        regions = frozenset(r.region_name for r in conn.execute(sql_regions))
        watchlist = tuple(
            (r.code, r.display_name or r.code)
            for r in conn.execute(sql_watchlist)
        )

    def _f(key: str, default: float) -> float:
        try:
            return float(rows.get(key, default))
        except (TypeError, ValueError):
            return default

    return PlatformSettings(
        nmck_min_rub=_f("nmck_min_rub", 30000.0),
        max_price_per_unit_rub=_f("max_price_per_unit_rub", 300000.0),
        excluded_region_names=regions,
        ktru_watchlist=watchlist,
    )


@dataclass
class UpsertResult:
    inserted: bool
    updated: bool


def upsert_tender(
    engine: Engine,
    card: TenderCard,
    flags: dict[str, Any],
) -> UpsertResult:
    """Idempotent: ON CONFLICT (reg_number) DO UPDATE — refresh all fields + flags + updated_at.
    Positions: full DELETE+INSERT inside the same transaction. tender_status: created once with
    status='new' on first insert, untouched on later updates.
    """
    contacts_json = json.dumps(card.customer_contacts_jsonb, ensure_ascii=False)
    flags_json = json.dumps(flags, ensure_ascii=False)
    ktru_array = list(card.ktru_codes)

    sql_upsert = text("""
        INSERT INTO tenders (
            reg_number, customer, customer_region, customer_contacts_jsonb,
            nmck_total, publish_date, submit_deadline, delivery_deadline,
            ktru_codes_array, url, raw_html, flags_jsonb, ingested_at, updated_at
        )
        VALUES (
            :reg_number, :customer, :customer_region, CAST(:contacts AS JSONB),
            :nmck_total, :publish_date, :submit_deadline, :delivery_deadline,
            CAST(:ktru_array AS TEXT[]), :url, :raw_html, CAST(:flags AS JSONB),
            now(), now()
        )
        ON CONFLICT (reg_number) DO UPDATE SET
            customer = EXCLUDED.customer,
            customer_region = EXCLUDED.customer_region,
            customer_contacts_jsonb = EXCLUDED.customer_contacts_jsonb,
            nmck_total = EXCLUDED.nmck_total,
            publish_date = EXCLUDED.publish_date,
            submit_deadline = EXCLUDED.submit_deadline,
            delivery_deadline = EXCLUDED.delivery_deadline,
            ktru_codes_array = EXCLUDED.ktru_codes_array,
            url = EXCLUDED.url,
            raw_html = EXCLUDED.raw_html,
            flags_jsonb = EXCLUDED.flags_jsonb,
            updated_at = now()
        RETURNING (xmax = 0) AS inserted
    """)
    sql_delete_items = text("DELETE FROM tender_items WHERE tender_id = :reg_number")
    sql_insert_item = text("""
        INSERT INTO tender_items (
            tender_id, position_num, ktru_code, name, qty, unit,
            required_attrs_jsonb, nmck_per_unit
        )
        VALUES (
            :tender_id, :position_num, :ktru_code, :name, :qty, :unit,
            CAST(:required_attrs AS JSONB), :nmck_per_unit
        )
    """)
    sql_init_status = text("""
        INSERT INTO tender_status (tender_id, status, changed_by)
        VALUES (:tender_id, 'new', 'system')
        ON CONFLICT (tender_id) DO NOTHING
    """)

    pg_array_literal = "{" + ",".join(_pg_array_escape(c) for c in ktru_array) + "}"

    with engine.begin() as conn:
        row = conn.execute(
            sql_upsert,
            {
                "reg_number": card.reg_number,
                "customer": card.customer,
                "customer_region": card.customer_region,
                "contacts": contacts_json,
                "nmck_total": card.nmck_total,
                "publish_date": card.publish_date,
                "submit_deadline": card.submit_deadline,
                "delivery_deadline": card.delivery_deadline,
                "ktru_array": pg_array_literal,
                "url": card.url,
                "raw_html": card.raw_html,
                "flags": flags_json,
            },
        ).fetchone()
        inserted = bool(row.inserted) if row is not None else False

        conn.execute(sql_delete_items, {"reg_number": card.reg_number})
        for item in card.items:
            conn.execute(
                sql_insert_item,
                {
                    "tender_id": card.reg_number,
                    "position_num": item.position_num,
                    "ktru_code": item.ktru_code,
                    "name": item.name,
                    "qty": item.qty,
                    "unit": item.unit,
                    "required_attrs": json.dumps(item.required_attrs_jsonb, ensure_ascii=False),
                    "nmck_per_unit": item.nmck_per_unit,
                },
            )

        conn.execute(sql_init_status, {"tender_id": card.reg_number})

    return UpsertResult(inserted=inserted, updated=not inserted)


def _pg_array_escape(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def tender_exists(engine: Engine, reg_number: str) -> bool:
    sql = text("SELECT 1 FROM tenders WHERE reg_number = :rn LIMIT 1")
    with engine.connect() as conn:
        return conn.execute(sql, {"rn": reg_number}).first() is not None


def log_db_versions(engine: Engine) -> None:
    """Best-effort sanity check at startup."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT count(*) AS n FROM ktru_watchlist WHERE is_active = TRUE")).first()
            logger.info("ktru_watchlist active codes: {}", row.n if row else 0)
    except Exception as exc:
        logger.warning("ingest startup check failed: {}", exc)
