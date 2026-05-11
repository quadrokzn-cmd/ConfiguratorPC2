"""Колонка «Цены» в /databases/components и детальная карточка (этап 9А.2.1).

Покрывают:
  - в списке у компонента с одной ценой видно «$X у Имя»;
  - в списке у компонента с N>=2 — «от $X (N поставщ.)»;
  - в списке у компонента без цен — бейдж «нет цен»;
  - на детальной странице видна таблица supplier_prices.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text


def _seed_supplier(db, *, name, is_active=True) -> int:
    row = db.execute(
        text(
            "INSERT INTO suppliers (name, is_active) VALUES (:n, :a) "
            "ON CONFLICT (name) DO UPDATE SET is_active = EXCLUDED.is_active "
            "RETURNING id"
        ),
        {"n": name, "a": is_active},
    ).first()
    db.commit()
    return int(row.id)


def _seed_cooler(db, *, model="MyCool", max_tdp=180,
                 sockets=("LGA1700",)) -> int:
    row = db.execute(
        text(
            "INSERT INTO coolers (model, manufacturer, sku, "
            "                     supported_sockets, max_tdp_watts) "
            "VALUES (:m, 'Vendor', :m, :s, :tdp) RETURNING id"
        ),
        {"m": model, "s": list(sockets), "tdp": max_tdp},
    ).first()
    db.commit()
    return int(row.id)


def _add_price(db, *, supplier_id, component_id, category="cooler",
               price=100.0, currency="USD", stock=5, sku="SKU-x"):
    db.execute(
        text(
            "INSERT INTO supplier_prices "
            "(category, component_id, supplier_id, supplier_sku, price, "
            " currency, stock_qty, transit_qty) "
            "VALUES (:cat, :cid, :sid, :sku, :p, :cur, :st, 0)"
        ),
        {
            "cat": category, "cid": component_id, "sid": supplier_id,
            "sku": sku, "p": price, "cur": currency, "st": stock,
        },
    )
    db.commit()


def test_component_list_shows_min_price_single_supplier(admin_portal_client, db_session):
    """Один поставщик — отображается «$X у Имя»."""
    sid = _seed_supplier(db_session, name="MerlionTest1")
    cid = _seed_cooler(db_session, model="OneSupCool")
    _add_price(db_session, supplier_id=sid, component_id=cid, price=88.0)

    r = admin_portal_client.get("/databases/components?category=cooler&q=OneSupCool")
    assert r.status_code == 200
    assert "OneSupCool" in r.text
    # «$88 у MerlionTest1»
    assert "$88" in r.text
    assert "MerlionTest1" in r.text


def test_component_list_shows_min_price_multi_supplier(admin_portal_client, db_session):
    """Два поставщика — отображается «от $X (2 поставщ.)»."""
    s1 = _seed_supplier(db_session, name="MerlionTest2")
    s2 = _seed_supplier(db_session, name="TreolanTest2")
    cid = _seed_cooler(db_session, model="MultiSupCool")
    _add_price(db_session, supplier_id=s1, component_id=cid, price=120.0,
               sku="SK-1")
    _add_price(db_session, supplier_id=s2, component_id=cid, price=110.0,
               sku="SK-2")

    r = admin_portal_client.get("/databases/components?category=cooler&q=MultiSupCool")
    assert r.status_code == 200
    assert "MultiSupCool" in r.text
    assert "от $110" in r.text
    assert "2 поставщ" in r.text


def test_component_list_shows_no_prices_badge(admin_portal_client, db_session):
    """Без поставщиков — бейдж «нет цен»."""
    _seed_cooler(db_session, model="NoPriceCool")
    r = admin_portal_client.get("/databases/components?category=cooler&q=NoPriceCool")
    assert r.status_code == 200
    assert "NoPriceCool" in r.text
    assert "нет цен" in r.text


def test_component_detail_shows_all_supplier_prices(admin_portal_client, db_session):
    """Детальная страница содержит таблицу со всеми поставщиками."""
    s1 = _seed_supplier(db_session, name="DetailSupActive", is_active=True)
    s2 = _seed_supplier(db_session, name="DetailSupOff", is_active=False)
    cid = _seed_cooler(db_session, model="DetailCool")
    _add_price(db_session, supplier_id=s1, component_id=cid, price=90.0,
               sku="A-SKU")
    _add_price(db_session, supplier_id=s2, component_id=cid, price=85.0,
               sku="B-SKU")

    r = admin_portal_client.get(f"/databases/components/cooler/{cid}")
    assert r.status_code == 200
    # Заголовок секции
    assert "Цены поставщиков" in r.text
    # Оба поставщика видны
    assert "DetailSupActive" in r.text
    assert "DetailSupOff" in r.text
    # Артикулы поставщиков
    assert "A-SKU" in r.text
    assert "B-SKU" in r.text
    # Бейдж «неактивен» у выключенного
    assert "неактивен" in r.text
