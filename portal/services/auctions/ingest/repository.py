from __future__ import annotations

import hashlib
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
    """Результат smart-ingest решения по одному лоту.

    Ровно один из флагов inserted / updated / skipped = True.
    inserted: лота не было в БД, теперь есть.
    updated:  лот был, его content_hash отличается от нового → обновили,
              старые items удалены вместе с их matches, новые items вставлены.
    skipped:  лот был, content_hash совпал → ничего не трогали, matches живы.
    """
    inserted: bool
    updated: bool
    skipped: bool


def compute_content_hash(card: TenderCard, flags: dict[str, Any]) -> str:
    """SHA-256 hex от business-полей TenderCard + flags. Детерминирован
    (sort_keys=True, sorted items by position_num, sorted ktru_codes).

    Намеренно не включает:
      - `raw_html` — HTML-разметка zakupki может меняться при идентичном
        бизнес-контенте (мелкие правки CSS-классов, динамические токены).
      - `reg_number` / `url` — PK и его производное, не предмет изменения.
      - `ingested_at` / `updated_at` / `last_modified_at` — system timestamp'ы,
        не часть лота как такового.
    """
    payload = {
        "customer": card.customer,
        "customer_region": card.customer_region,
        "customer_contacts_jsonb": card.customer_contacts_jsonb,
        "nmck_total": str(card.nmck_total) if card.nmck_total is not None else None,
        "publish_date": card.publish_date.isoformat() if card.publish_date else None,
        "submit_deadline": card.submit_deadline.isoformat() if card.submit_deadline else None,
        "delivery_deadline": card.delivery_deadline.isoformat() if card.delivery_deadline else None,
        "ktru_codes": sorted(card.ktru_codes),
        "items": [
            {
                "position_num": it.position_num,
                "ktru_code": it.ktru_code,
                "name": it.name,
                "qty": str(it.qty) if it.qty is not None else None,
                "unit": it.unit,
                "nmck_per_unit": str(it.nmck_per_unit) if it.nmck_per_unit is not None else None,
                "required_attrs_jsonb": it.required_attrs_jsonb,
            }
            for it in sorted(card.items, key=lambda x: x.position_num)
        ],
        "flags_jsonb": flags,
    }
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# --- SQL statements ---

_SQL_SELECT_HASH = text(
    "SELECT content_hash FROM tenders WHERE reg_number = :rn FOR UPDATE"
)

_SQL_INSERT_TENDER = text("""
    INSERT INTO tenders (
        reg_number, customer, customer_region, customer_contacts_jsonb,
        nmck_total, publish_date, submit_deadline, delivery_deadline,
        ktru_codes_array, url, raw_html, flags_jsonb,
        ingested_at, updated_at, content_hash, last_modified_at
    )
    VALUES (
        :reg_number, :customer, :customer_region, CAST(:contacts AS JSONB),
        :nmck_total, :publish_date, :submit_deadline, :delivery_deadline,
        CAST(:ktru_array AS TEXT[]), :url, :raw_html, CAST(:flags AS JSONB),
        now(), now(), :content_hash, now()
    )
""")

_SQL_UPDATE_TENDER = text("""
    UPDATE tenders SET
        customer = :customer,
        customer_region = :customer_region,
        customer_contacts_jsonb = CAST(:contacts AS JSONB),
        nmck_total = :nmck_total,
        publish_date = :publish_date,
        submit_deadline = :submit_deadline,
        delivery_deadline = :delivery_deadline,
        ktru_codes_array = CAST(:ktru_array AS TEXT[]),
        url = :url,
        raw_html = :raw_html,
        flags_jsonb = CAST(:flags AS JSONB),
        updated_at = now(),
        content_hash = :content_hash,
        last_modified_at = now()
    WHERE reg_number = :reg_number
""")

_SQL_DELETE_MATCHES_FOR_TENDER = text("""
    DELETE FROM matches
    WHERE tender_item_id IN (
        SELECT id FROM tender_items WHERE tender_id = :reg_number
    )
""")

_SQL_DELETE_ITEMS = text("DELETE FROM tender_items WHERE tender_id = :reg_number")

_SQL_INSERT_ITEM = text("""
    INSERT INTO tender_items (
        tender_id, position_num, ktru_code, name, qty, unit,
        required_attrs_jsonb, nmck_per_unit
    )
    VALUES (
        :tender_id, :position_num, :ktru_code, :name, :qty, :unit,
        CAST(:required_attrs AS JSONB), :nmck_per_unit
    )
""")

_SQL_INIT_STATUS = text("""
    INSERT INTO tender_status (tender_id, status, changed_by)
    VALUES (:tender_id, 'new', 'system')
    ON CONFLICT (tender_id) DO NOTHING
""")


def upsert_tender(
    engine: Engine,
    card: TenderCard,
    flags: dict[str, Any],
) -> UpsertResult:
    """Smart-ingest решение по одному лоту:

      - reg_number не существует → INSERT всех таблиц (tender, items, status);
      - reg_number существует, content_hash совпал → SKIP (tender_items не
        трогаем, matches живы);
      - reg_number существует, content_hash отличается → UPDATE tender +
        DELETE matches привязанных к старым items (явно, т.к. FK NO ACTION
        после миграции 0039) + DELETE/INSERT tender_items + сохранение
        content_hash + last_modified_at = now().

    Все три ветки — внутри одной транзакции с SELECT ... FOR UPDATE на строке
    tenders, чтобы серилизовать concurrent ingest-prokrutky одного reg_number.
    """
    new_hash = compute_content_hash(card, flags)
    contacts_json = json.dumps(card.customer_contacts_jsonb, ensure_ascii=False)
    flags_json = json.dumps(flags, ensure_ascii=False)
    pg_array_literal = "{" + ",".join(
        _pg_array_escape(c) for c in card.ktru_codes
    ) + "}"

    tender_params = {
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
        "content_hash": new_hash,
    }

    with engine.begin() as conn:
        existing = conn.execute(_SQL_SELECT_HASH, {"rn": card.reg_number}).first()

        if existing is None:
            conn.execute(_SQL_INSERT_TENDER, tender_params)
            _insert_items(conn, card)
            conn.execute(_SQL_INIT_STATUS, {"tender_id": card.reg_number})
            return UpsertResult(inserted=True, updated=False, skipped=False)

        if existing.content_hash == new_hash:
            # SKIP — лот не изменился, tender_items нетронуты, matches живы.
            return UpsertResult(inserted=False, updated=False, skipped=True)

        # UPDATE — content_hash отличается (или NULL после миграции 0039).
        conn.execute(_SQL_UPDATE_TENDER, tender_params)
        # FK matches.tender_item_id → tender_items теперь NO ACTION (миграция 0039),
        # поэтому DELETE FROM tender_items упадёт, если есть matches на эти items.
        # Удаляем matches явно до удаления items.
        conn.execute(_SQL_DELETE_MATCHES_FOR_TENDER, {"reg_number": card.reg_number})
        conn.execute(_SQL_DELETE_ITEMS, {"reg_number": card.reg_number})
        _insert_items(conn, card)
        return UpsertResult(inserted=False, updated=True, skipped=False)


def _insert_items(conn, card: TenderCard) -> None:
    for item in card.items:
        conn.execute(
            _SQL_INSERT_ITEM,
            {
                "tender_id": card.reg_number,
                "position_num": item.position_num,
                "ktru_code": item.ktru_code,
                "name": item.name,
                "qty": item.qty,
                "unit": item.unit,
                "required_attrs": json.dumps(
                    item.required_attrs_jsonb, ensure_ascii=False
                ),
                "nmck_per_unit": item.nmck_per_unit,
            },
        )


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
