"""Тесты `aggregate_tender` через локальный SQLite-инмемори с тем же DDL.

Не пишу против Postgres-теста, чтобы не требовать живой БД в pytest. SQLite
поддерживает простые ARRAY/JSONB-операторы плохо; здесь обходимся базовым SQL,
который и так использует aggregator (count, sum, avg)."""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import create_engine, text

from app.modules.auctions.match.aggregator import aggregate_tender, margin_threshold_pct


def _make_db():
    """SQLite in-memory с минимальным DDL под aggregator-запросы."""
    engine = create_engine("sqlite:///:memory:", future=True)
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE tenders (
                reg_number TEXT PRIMARY KEY,
                nmck_total NUMERIC
            )
        """))
        conn.execute(text("""
            CREATE TABLE tender_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tender_id TEXT NOT NULL,
                position_num INTEGER NOT NULL,
                ktru_code TEXT,
                qty NUMERIC,
                nmck_per_unit NUMERIC
            )
        """))
        conn.execute(text("""
            CREATE TABLE matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tender_item_id INTEGER NOT NULL,
                nomenclature_id INTEGER NOT NULL,
                match_type TEXT NOT NULL,
                price_total_rub NUMERIC,
                margin_rub NUMERIC,
                margin_pct NUMERIC
            )
        """))
        conn.execute(text("""
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """))
    return engine


def _seed(engine, tender_id: str, items: list[dict], matches: list[dict]):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO tenders (reg_number) VALUES (:r)"), {"r": tender_id})
        for it in items:
            conn.execute(
                text("""INSERT INTO tender_items (tender_id, position_num, ktru_code, qty, nmck_per_unit)
                        VALUES (:t, :p, :k, :q, :n)"""),
                {"t": tender_id, "p": it["position_num"], "k": it.get("ktru_code", "26.20.18.000-00000069"),
                 "q": it.get("qty", 1), "n": it.get("nmck_per_unit", 10000)},
            )
        for m in matches:
            conn.execute(
                text("""INSERT INTO matches (tender_item_id, nomenclature_id, match_type, margin_rub, margin_pct)
                        VALUES (:ti, :n, :mt, :mr, :mp)"""),
                m,
            )


def test_aggregate_full_coverage():
    engine = _make_db()
    _seed(
        engine,
        "T1",
        items=[{"position_num": 1, "qty": 2, "nmck_per_unit": 10000},
               {"position_num": 2, "qty": 1, "nmck_per_unit": 5000}],
        matches=[
            {"ti": 1, "n": 100, "mt": "primary", "mr": 4000, "mp": 40},
            {"ti": 1, "n": 101, "mt": "alternative", "mr": 3000, "mp": 30},
            {"ti": 2, "n": 102, "mt": "primary", "mr": 1000, "mp": 20},
        ],
    )
    summary = aggregate_tender(engine, "T1")
    assert summary.items_total == 2
    assert summary.items_with_primary == 2
    assert summary.all_positions_covered is True
    # margin_total = 4000*2 + 1000*1 = 9000
    assert summary.primary_margin_total_rub == Decimal("9000.00")
    # avg pct = (40+20)/2 = 30
    assert summary.primary_margin_pct_avg == Decimal("30.00")


def test_aggregate_partial_coverage():
    engine = _make_db()
    _seed(
        engine,
        "T2",
        items=[{"position_num": 1}, {"position_num": 2}, {"position_num": 3}],
        matches=[{"ti": 1, "n": 100, "mt": "primary", "mr": 1000, "mp": 10}],
    )
    summary = aggregate_tender(engine, "T2")
    assert summary.items_total == 3
    assert summary.items_with_primary == 1
    assert summary.all_positions_covered is False


def test_aggregate_no_matches():
    engine = _make_db()
    _seed(
        engine,
        "T3",
        items=[{"position_num": 1}],
        matches=[],
    )
    summary = aggregate_tender(engine, "T3")
    assert summary.items_total == 1
    assert summary.items_with_primary == 0
    assert summary.primary_margin_total_rub is None or summary.primary_margin_total_rub == Decimal("0.00")
    assert summary.primary_margin_pct_avg is None
    assert not summary.all_positions_covered


def test_margin_threshold_default():
    engine = _make_db()
    assert margin_threshold_pct(engine) == Decimal("15")


def test_margin_threshold_from_settings():
    engine = _make_db()
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO settings (key, value) VALUES ('margin_threshold_pct', '20.5')")
        )
    assert margin_threshold_pct(engine) == Decimal("20.5")
