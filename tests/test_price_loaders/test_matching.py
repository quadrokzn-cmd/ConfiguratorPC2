# Логика сопоставления PriceRow с компонентами БД.
#
# Проверяем все ветки алгоритма из matching.py:
#   - existing (повторная загрузка);
#   - match по MPN (ровно 1);
#   - match по GTIN (ровно 1);
#   - ambiguous_mpn (несколько по MPN) — детерминированный выбор min id;
#   - ambiguous_gtin (несколько по GTIN) — то же;
#   - no_match (ни MPN, ни GTIN не дали результат);
#   - отдельный кейс Intel CPU с S-Spec: совпадает ТОЛЬКО по GTIN.

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import text as _t

from app.services.price_loaders.matching import (
    AMBIG_GTIN, AMBIG_MPN, EXISTING, MATCH_GTIN, MATCH_MPN, NO_MATCH,
    resolve,
)
from app.services.price_loaders.models import PriceRow


def _insert_cpu(session, *, model: str, manufacturer: str, sku: str,
                gtin: str | None = None) -> int:
    row = session.execute(_t(
        "INSERT INTO cpus "
        "  (model, manufacturer, sku, gtin, socket, cores, threads, "
        "   base_clock_ghz, turbo_clock_ghz, tdp_watts, has_integrated_graphics, "
        "   memory_type, package_type) "
        "VALUES (:m, :mfg, :sku, :gtin, 'LGA1700', 6, 12, 3.0, 4.0, 65, FALSE, "
        "        'DDR5', 'BOX') "
        "RETURNING id"
    ), {"m": model, "mfg": manufacturer, "sku": sku, "gtin": gtin}).scalar()
    session.commit()
    return int(row)


def _insert_supplier(session, name: str) -> int:
    row = session.execute(_t(
        "INSERT INTO suppliers (name, is_active) VALUES (:n, TRUE) "
        "ON CONFLICT (name) DO UPDATE SET is_active = suppliers.is_active "
        "RETURNING id"
    ), {"n": name}).scalar()
    session.commit()
    return int(row)


def _supplier_price(session, *, sid: int, cid: int, ssku: str, price=100.0):
    session.execute(_t(
        "INSERT INTO supplier_prices "
        "  (supplier_id, category, component_id, supplier_sku, price, currency, "
        "   stock_qty, transit_qty) "
        "VALUES (:sid, 'cpu', :cid, :ssku, :price, 'RUB', 1, 0)"
    ), {"sid": sid, "cid": cid, "ssku": ssku, "price": price})
    session.commit()


def _row(**kw) -> PriceRow:
    defaults = {
        "supplier_sku": "S-001",
        "mpn":          None,
        "gtin":         None,
        "brand":        "AMD",
        "raw_category": "Комплектующие->Процессоры",
        "our_category": "cpu",
        "name":         "Test CPU",
        "price":        Decimal("10000"),
        "currency":     "RUB",
        "stock":        1,
        "transit":      0,
    }
    defaults.update(kw)
    return PriceRow(**defaults)


# -------------------- тесты --------------------------------------------------


def test_match_by_mpn_single(db_session):
    cid = _insert_cpu(db_session, model="Ryzen 5 7600", manufacturer="AMD",
                      sku="100-000001591")
    sid = _insert_supplier(db_session, "Treolan")

    res = resolve(db_session, _row(mpn="100-000001591"), supplier_id=sid)
    assert res.source == MATCH_MPN
    assert res.component_id == cid


def test_match_by_gtin_when_mpn_differs(db_session):
    """Intel CPU: в БД sku=OrderCode, у Treolan mpn=SRMBG (S-Spec),
    GTIN одинаков. Match должен произойти по GTIN."""
    cid = _insert_cpu(
        db_session,
        model="Intel Core i5-13400F", manufacturer="Intel",
        sku="CM8071512400F", gtin="5032037260466",
    )
    sid = _insert_supplier(db_session, "Treolan")

    res = resolve(
        db_session,
        _row(mpn="SRMBG", gtin="5032037260466"),
        supplier_id=sid,
    )
    assert res.source == MATCH_GTIN
    assert res.component_id == cid


def test_ambiguous_mpn_picks_min_id_and_records_all(db_session):
    """Несколько компонентов с одинаковым sku — выбирается min(id),
    ambiguous_ids содержит всех кандидатов."""
    cid_a = _insert_cpu(db_session, model="CPU A", manufacturer="AMD", sku="DUP-1")
    cid_b = _insert_cpu(db_session, model="CPU B", manufacturer="AMD", sku="DUP-1")
    assert cid_a < cid_b
    sid = _insert_supplier(db_session, "Treolan")

    res = resolve(db_session, _row(mpn="DUP-1"), supplier_id=sid)
    assert res.source == AMBIG_MPN
    assert res.component_id == cid_a
    assert set(res.ambiguous_ids) == {cid_a, cid_b}


def test_ambiguous_gtin_picks_min_id(db_session):
    cid_a = _insert_cpu(db_session, model="X1", manufacturer="Intel",
                        sku="SKU-A", gtin="5032037260466")
    cid_b = _insert_cpu(db_session, model="X2", manufacturer="Intel",
                        sku="SKU-B", gtin="5032037260466")
    sid = _insert_supplier(db_session, "Treolan")

    res = resolve(
        db_session,
        _row(mpn="NONEXIST", gtin="5032037260466"),
        supplier_id=sid,
    )
    assert res.source == AMBIG_GTIN
    assert res.component_id == min(cid_a, cid_b)


def test_no_match_when_neither_mpn_nor_gtin(db_session):
    _insert_cpu(db_session, model="AMD Ryzen 5 7600", manufacturer="AMD",
                sku="100-000001591", gtin="0730143314572")
    sid = _insert_supplier(db_session, "Treolan")

    res = resolve(
        db_session,
        _row(mpn="UNKNOWN", gtin="0000000000000"),
        supplier_id=sid,
    )
    assert res.source == NO_MATCH
    assert res.component_id is None


def test_existing_returns_stored_component(db_session):
    """Если supplier_prices уже содержит запись (supplier_id, supplier_sku) —
    источник 'existing', component_id берётся из supplier_prices."""
    cid = _insert_cpu(db_session, model="R5 7600", manufacturer="AMD",
                      sku="100-000001591")
    sid = _insert_supplier(db_session, "Treolan")
    _supplier_price(db_session, sid=sid, cid=cid, ssku="100-000001591")

    # Даже если MPN в PriceRow другой — existing перекрывает.
    res = resolve(
        db_session,
        _row(mpn="SOMETHING-ELSE", supplier_sku="100-000001591"),
        supplier_id=sid,
    )
    assert res.source == EXISTING
    assert res.component_id == cid


def test_existing_scoped_by_supplier(db_session):
    """existing не срабатывает на записи другого поставщика — там может
    быть свой supplier_sku с тем же значением."""
    cid = _insert_cpu(db_session, model="R5 7600", manufacturer="AMD",
                      sku="100-000001591")
    sid_a = _insert_supplier(db_session, "Treolan")
    sid_b = _insert_supplier(db_session, "Merlion")
    _supplier_price(db_session, sid=sid_a, cid=cid, ssku="100-000001591")

    # Merlion загружает эту строку впервые — существующей записи для него нет.
    res = resolve(
        db_session,
        _row(mpn="100-000001591", supplier_sku="100-000001591"),
        supplier_id=sid_b,
    )
    assert res.source == MATCH_MPN
    assert res.component_id == cid


def test_none_category_returns_no_match(db_session):
    """Категория None — сразу no_match, в БД не лезем."""
    sid = _insert_supplier(db_session, "Treolan")
    res = resolve(
        db_session,
        _row(our_category=None, mpn="ANY"),
        supplier_id=sid,
    )
    assert res.source == NO_MATCH


def test_mpn_matches_before_gtin(db_session):
    """Если и MPN, и GTIN дают match — приоритет у MPN (как в алгоритме)."""
    cid_m = _insert_cpu(db_session, model="M", manufacturer="AMD",
                        sku="ABC-1", gtin=None)
    _insert_cpu(db_session, model="G", manufacturer="AMD",
                sku="OTHER", gtin="1112223334445")
    sid = _insert_supplier(db_session, "Merlion")

    res = resolve(
        db_session,
        _row(mpn="ABC-1", gtin="1112223334445"),
        supplier_id=sid,
    )
    assert res.source == MATCH_MPN
    assert res.component_id == cid_m
