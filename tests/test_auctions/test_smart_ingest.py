"""Тесты smart-ingest аукционов (мини-этап 2026-05-16, блокер Волны 3).

Покрытие:
  - compute_content_hash: детерминизм, чувствительность к business-полям,
    нечувствительность к raw_html, чувствительность к items.
  - upsert_tender: ветки INSERT-new / SKIP-unchanged / UPDATE-changed.
  - upsert_tender + matches: matches других лотов не страдают при UPDATE
    одного лота (regression-тест на FK CASCADE-катастрофу до 2026-05-16).
  - match_single_tender: матчит только указанный reg_number, matches
    остальных лотов нетронуты.
  - FK NO ACTION: DELETE FROM tenders WHERE reg_number = X падает, если
    у лота есть tender_items (страховка миграции 0039).
  - run_ingest_once с занятым pg_advisory_lock: возвращает пустую
    IngestStats без обращения к zakupki.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from portal.services.auctions.ingest.card_parser import TenderCard, TenderItem
from portal.services.auctions.ingest.orchestrator import (
    _AUCTIONS_INGEST_ADVISORY_LOCK_ID,
    run_ingest_once,
)
from portal.services.auctions.ingest.repository import (
    compute_content_hash,
    upsert_tender,
)
from portal.services.auctions.match.service import match_single_tender


MSK = timezone(timedelta(hours=3))


# ---------------------------------------------------------------
# Фикстуры
# ---------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_auction_tables(db_engine):
    """Перед каждым тестом — пустые tenders/items/matches/printers_mfu."""
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE matches, tender_items, tender_status, tenders, "
            "printers_mfu RESTART IDENTITY CASCADE"
        ))
    yield


def _make_card(
    reg_number: str = "0816500000626000001",
    customer: str = "ФГБУ Тестовый Заказчик",
    nmck_total: Decimal = Decimal("125000.00"),
    items: list[TenderItem] | None = None,
    raw_html: str = "<html>card</html>",
) -> TenderCard:
    if items is None:
        items = [
            TenderItem(
                position_num=1,
                ktru_code="26.20.18.000-00000069",
                name="МФУ ч/б A4",
                qty=Decimal("5"),
                unit="шт",
                nmck_per_unit=Decimal("25000.00"),
                required_attrs_jsonb={},
            )
        ]
    return TenderCard(
        reg_number=reg_number,
        url=f"https://zakupki.gov.ru/{reg_number}",
        customer=customer,
        customer_region="Республика Татарстан",
        customer_contacts_jsonb={"email": "x@y.ru", "phone": "+78435551234"},
        nmck_total=nmck_total,
        publish_date=datetime(2026, 4, 10, tzinfo=MSK),
        submit_deadline=datetime(2026, 4, 25, 9, 0, tzinfo=MSK),
        delivery_deadline=datetime(2026, 7, 15, tzinfo=MSK),
        ktru_codes=["26.20.18.000-00000069"],
        items=items,
        raw_html=raw_html,
    )


def _flags() -> dict:
    return {
        "excluded_by_region": False,
        "below_nmck_min": False,
        "rejected_by_price_per_unit": False,
        "no_watchlist_ktru_in_card": False,
        "no_positions_parsed": False,
    }


def _insert_sku(db_engine, sku: str = "p1") -> int:
    """Хелпер: вставляет минимальный SKU в printers_mfu и возвращает id."""
    attrs_json = json.dumps({
        "colorness": "ч/б",
        "max_format": "A4",
        "duplex": "yes",
        "print_speed_ppm": 30,
        "usb": "yes",
        "print_technology": "лазерная",
        "network_interface": ["LAN"],
        "resolution_dpi": 1200,
        "starter_cartridge_pages": 1500,
    })
    with db_engine.begin() as conn:
        return conn.execute(text("""
            INSERT INTO printers_mfu (sku, brand, name, category, ktru_codes_array,
                attrs_jsonb, cost_base_rub)
            VALUES (:sku, 'Pantum', :sku, 'mfu', CAST(:ktru AS TEXT[]),
                CAST(:attrs AS JSONB), 12000)
            RETURNING id
        """), {
            "sku": sku,
            "ktru": '{26.20.18.000-00000069}',
            "attrs": attrs_json,
        }).scalar()


def _seed_match(db_engine, item_id: int, sku_id: int) -> None:
    """Вставляет одну запись matches для item_id/sku_id."""
    with db_engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO matches (tender_item_id, nomenclature_id, match_type,
                price_total_rub, margin_rub, margin_pct)
            VALUES (:item, :sku, 'primary', 100, 50, 50)
        """), {"item": item_id, "sku": sku_id})


# ===============================================================
# 1. compute_content_hash
# ===============================================================

def test_content_hash_deterministic():
    card = _make_card()
    flags = _flags()
    h1 = compute_content_hash(card, flags)
    h2 = compute_content_hash(card, flags)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_content_hash_changes_on_business_field():
    card1 = _make_card(nmck_total=Decimal("125000.00"))
    card2 = _make_card(nmck_total=Decimal("130000.00"))
    flags = _flags()
    assert compute_content_hash(card1, flags) != compute_content_hash(card2, flags)


def test_content_hash_ignores_raw_html():
    """Изменение raw_html (HTML-разметки) НЕ меняет hash."""
    card1 = _make_card(raw_html="<html>v1</html>")
    card2 = _make_card(raw_html="<html>v2 — другая разметка, тот же контент</html>")
    flags = _flags()
    assert compute_content_hash(card1, flags) == compute_content_hash(card2, flags)


def test_content_hash_changes_on_item_change():
    card1 = _make_card()
    new_items = [
        TenderItem(
            position_num=1,
            ktru_code="26.20.18.000-00000069",
            name="МФУ цветной A4",  # изменили name
            qty=Decimal("5"),
            unit="шт",
            nmck_per_unit=Decimal("25000.00"),
            required_attrs_jsonb={},
        )
    ]
    card2 = _make_card(items=new_items)
    flags = _flags()
    assert compute_content_hash(card1, flags) != compute_content_hash(card2, flags)


# ===============================================================
# 2. upsert_tender — INSERT / SKIP / UPDATE
# ===============================================================

def test_upsert_tender_inserts_new(db_engine):
    card = _make_card("0001")
    result = upsert_tender(db_engine, card, _flags())
    assert result.inserted is True
    assert result.updated is False
    assert result.skipped is False

    with db_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT customer, content_hash, last_modified_at "
            "FROM tenders WHERE reg_number = '0001'"
        )).first()
        assert row.customer == "ФГБУ Тестовый Заказчик"
        assert row.content_hash is not None
        assert row.last_modified_at is not None
        n_items = conn.execute(text(
            "SELECT count(*) FROM tender_items WHERE tender_id = '0001'"
        )).scalar()
        assert n_items == 1
        n_status = conn.execute(text(
            "SELECT count(*) FROM tender_status WHERE tender_id = '0001'"
        )).scalar()
        assert n_status == 1


def test_upsert_tender_skips_unchanged(db_engine):
    """Повторный upsert с тем же card → SKIP, tender_items НЕ пересоздаются."""
    card = _make_card("0002")
    flags = _flags()
    upsert_tender(db_engine, card, flags)

    with db_engine.connect() as conn:
        old_item_id = conn.execute(text(
            "SELECT id FROM tender_items WHERE tender_id = '0002'"
        )).scalar()
        old_last_modified = conn.execute(text(
            "SELECT last_modified_at FROM tenders WHERE reg_number = '0002'"
        )).scalar()

    result2 = upsert_tender(db_engine, card, flags)
    assert result2.skipped is True
    assert result2.inserted is False
    assert result2.updated is False

    with db_engine.connect() as conn:
        new_item_id = conn.execute(text(
            "SELECT id FROM tender_items WHERE tender_id = '0002'"
        )).scalar()
        new_last_modified = conn.execute(text(
            "SELECT last_modified_at FROM tenders WHERE reg_number = '0002'"
        )).scalar()

    assert old_item_id == new_item_id
    assert old_last_modified == new_last_modified


def test_upsert_tender_updates_on_content_change(db_engine):
    """Изменение nmck_total → UPDATE, items пересозданы, last_modified_at обновлён."""
    card1 = _make_card("0003", nmck_total=Decimal("100000.00"))
    upsert_tender(db_engine, card1, _flags())

    with db_engine.connect() as conn:
        old_item_ids = [r.id for r in conn.execute(text(
            "SELECT id FROM tender_items WHERE tender_id = '0003' ORDER BY position_num"
        ))]
        old_last_modified = conn.execute(text(
            "SELECT last_modified_at FROM tenders WHERE reg_number = '0003'"
        )).scalar()

    card2 = _make_card("0003", nmck_total=Decimal("110000.00"))
    result = upsert_tender(db_engine, card2, _flags())
    assert result.updated is True
    assert result.inserted is False
    assert result.skipped is False

    with db_engine.connect() as conn:
        new_item_ids = [r.id for r in conn.execute(text(
            "SELECT id FROM tender_items WHERE tender_id = '0003' ORDER BY position_num"
        ))]
        new_last_modified = conn.execute(text(
            "SELECT last_modified_at FROM tenders WHERE reg_number = '0003'"
        )).scalar()
        new_nmck = conn.execute(text(
            "SELECT nmck_total FROM tenders WHERE reg_number = '0003'"
        )).scalar()

    # Items пересозданы — id другие (DELETE+INSERT в одной транзакции).
    assert old_item_ids != new_item_ids
    # last_modified_at обновился вперёд.
    assert new_last_modified > old_last_modified
    # nmck_total обновлён.
    assert Decimal(new_nmck) == Decimal("110000.00")


def test_upsert_tender_update_clears_matches_for_this_tender_only(db_engine):
    """REGRESSION на FK CASCADE-катастрофу: при UPDATE-ветке matches для
    items ЭТОГО лота удаляются явно (FK NO ACTION после миграции 0039
    не каскадит автоматически), а matches других лотов нетронуты.

    Это инверсия дефекта, из-за которого до 2026-05-16 cron `auctions_ingest`
    каждые 2 часа убивал ВСЕ matches.
    """
    card_a = _make_card("AAA")
    card_b = _make_card("BBB")
    upsert_tender(db_engine, card_a, _flags())
    upsert_tender(db_engine, card_b, _flags())

    sku_id = _insert_sku(db_engine)

    with db_engine.connect() as conn:
        item_a_id = conn.execute(text(
            "SELECT id FROM tender_items WHERE tender_id = 'AAA'"
        )).scalar()
        item_b_id = conn.execute(text(
            "SELECT id FROM tender_items WHERE tender_id = 'BBB'"
        )).scalar()
    _seed_match(db_engine, item_a_id, sku_id)
    _seed_match(db_engine, item_b_id, sku_id)

    # UPDATE лота AAA — matches AAA должны быть удалены, BBB живы.
    card_a_modified = _make_card("AAA", customer="Заказчик 2.0")
    result = upsert_tender(db_engine, card_a_modified, _flags())
    assert result.updated is True

    with db_engine.connect() as conn:
        matches_a = conn.execute(text("""
            SELECT count(*) FROM matches WHERE tender_item_id IN
                (SELECT id FROM tender_items WHERE tender_id = 'AAA')
        """)).scalar()
        matches_b = conn.execute(text("""
            SELECT count(*) FROM matches WHERE tender_item_id IN
                (SELECT id FROM tender_items WHERE tender_id = 'BBB')
        """)).scalar()

    assert matches_a == 0
    assert matches_b == 1


def test_upsert_tender_skip_preserves_matches(db_engine):
    """REGRESSION (главный): при SKIP-ветке matches не трогаются. Это
    оборотная сторона test_..._clears_matches_for_this_tender_only —
    проверка, что НЕизменившиеся лоты сохраняют свои matches между
    ingest-тиками (главная цель миграции 0039)."""
    card = _make_card("SKIP1")
    upsert_tender(db_engine, card, _flags())
    sku_id = _insert_sku(db_engine)

    with db_engine.connect() as conn:
        item_id = conn.execute(text(
            "SELECT id FROM tender_items WHERE tender_id = 'SKIP1'"
        )).scalar()
    _seed_match(db_engine, item_id, sku_id)

    # Повторный ingest с тем же card → SKIP. Matches должны остаться.
    result = upsert_tender(db_engine, card, _flags())
    assert result.skipped is True

    with db_engine.connect() as conn:
        n_matches = conn.execute(text(
            "SELECT count(*) FROM matches WHERE tender_item_id = :i"
        ), {"i": item_id}).scalar()
    assert n_matches == 1


# ===============================================================
# 3. match_single_tender
# ===============================================================

def test_match_single_tender_does_not_touch_other_tenders_matches(db_engine):
    """match_single_tender('TGT') не удаляет и не добавляет matches для
    items другого лота."""
    card_target = _make_card("TGT")
    card_other = _make_card("OTH")
    upsert_tender(db_engine, card_target, _flags())
    upsert_tender(db_engine, card_other, _flags())

    sku_id = _insert_sku(db_engine)

    with db_engine.connect() as conn:
        item_other_id = conn.execute(text(
            "SELECT id FROM tender_items WHERE tender_id = 'OTH'"
        )).scalar()
    _seed_match(db_engine, item_other_id, sku_id)

    # Матчим только TGT.
    match_single_tender(db_engine, "TGT")

    with db_engine.connect() as conn:
        matches_other = conn.execute(text(
            "SELECT count(*) FROM matches WHERE tender_item_id = :i"
        ), {"i": item_other_id}).scalar()
    # Матчи лота OTH не пострадали.
    assert matches_other == 1


def test_match_single_tender_unknown_tender_returns_zero(db_engine):
    """match_single_tender для несуществующего reg_number — 0 matches,
    исключения не бросает."""
    n = match_single_tender(db_engine, "DOES_NOT_EXIST")
    assert n == 0


# ===============================================================
# 4. FK NO ACTION — миграция 0039
# ===============================================================

def test_fk_no_action_blocks_delete_tenders_with_items(db_engine):
    """DELETE FROM tenders WHERE reg_number = X должен упасть, если есть
    tender_items: FK NO ACTION после миграции 0039 запрещает orphans.

    Сторожевой тест: если кто-то откатит миграцию 0039 или вернёт CASCADE,
    этот тест отловит регрессию (DELETE пройдёт молча и убьёт matches)."""
    card = _make_card("FK1")
    upsert_tender(db_engine, card, _flags())

    with pytest.raises(Exception) as exc_info:
        with db_engine.begin() as conn:
            conn.execute(text("DELETE FROM tenders WHERE reg_number = 'FK1'"))

    msg = str(exc_info.value).lower()
    assert "foreign key" in msg or "violates" in msg


# ===============================================================
# 5. pg_advisory_lock — concurrent ingest защита
# ===============================================================

def test_run_ingest_once_skips_when_advisory_lock_busy(db_engine, monkeypatch):
    """Если pg_advisory_lock уже занят другой сессией, run_ingest_once
    возвращает IngestStats() пустыми и НЕ дёргает ZakupkiClient."""
    def _fail(*args, **kwargs):
        raise AssertionError(
            "ZakupkiClient must not be instantiated when advisory_lock is busy"
        )
    monkeypatch.setattr(
        "portal.services.auctions.ingest.orchestrator.ZakupkiClient",
        _fail,
    )

    with db_engine.connect() as lock_holder:
        got = lock_holder.execute(
            text("SELECT pg_try_advisory_lock(:lid) AS g"),
            {"lid": _AUCTIONS_INGEST_ADVISORY_LOCK_ID},
        ).scalar()
        assert got is True
        try:
            stats = run_ingest_once(db_engine)
            assert stats.cards_seen == 0
            assert stats.inserted == 0
            assert stats.updated == 0
            assert stats.skipped == 0
            assert stats.matches_inserted == 0
        finally:
            lock_holder.execute(
                text("SELECT pg_advisory_unlock(:lid)"),
                {"lid": _AUCTIONS_INGEST_ADVISORY_LOCK_ID},
            )
