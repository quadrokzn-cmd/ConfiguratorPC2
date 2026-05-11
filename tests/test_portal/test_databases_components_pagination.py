"""Тесты пагинации /databases/components (Блок D 9А.2.3) + toggle-text
формы поставщика.

Изначально на /admin/components в конфигураторе (этап 9А.2.3). На этапе
UI-2 Пути B (2026-05-11) страницы и тесты переехали в портал.
"""

from __future__ import annotations

from sqlalchemy import text as _t


def _seed_supplier(db, *, name="SupPag", is_active=True) -> int:
    row = db.execute(
        _t(
            "INSERT INTO suppliers (name, is_active) VALUES (:n, :a) "
            "ON CONFLICT (name) DO UPDATE SET is_active = EXCLUDED.is_active "
            "RETURNING id"
        ),
        {"n": name, "a": is_active},
    ).first()
    db.commit()
    return int(row.id)


def _seed_cpu(db, *, model, price_usd, supplier_id, hidden=False, stock=5):
    row = db.execute(
        _t(
            "INSERT INTO cpus (model, manufacturer, sku, socket, cores, threads, "
            "                  base_clock_ghz, tdp_watts, has_integrated_graphics, "
            "                  memory_type, package_type, is_hidden) "
            "VALUES (:m, 'Intel', :sku, 'LGA1700', 6, 12, 3.0, 65, FALSE, 'DDR5', "
            "        'BOX', :h) RETURNING id"
        ),
        {"m": model, "sku": f"SKU-{model}", "h": hidden},
    ).first()
    cid = int(row.id)
    db.execute(
        _t(
            "INSERT INTO supplier_prices "
            "(category, component_id, supplier_id, supplier_sku, price, currency, "
            " stock_qty, transit_qty) "
            "VALUES ('cpu', :cid, :sid, :ssku, :p, 'USD', :st, 0)"
        ),
        {"cid": cid, "sid": supplier_id, "ssku": f"SP-{model}",
         "p": price_usd, "st": stock},
    )
    db.commit()
    return cid


def _seed_many_components(db, n=200):
    """Создаём поставщика и N CPU с прайсами, чтобы получить ≥ страниц."""
    sid = _seed_supplier(db, name="SupPag")
    for i in range(n):
        _seed_cpu(db, model=f"CPU-Pag-{i:03}", price_usd=100.0 + i, supplier_id=sid)


# =====================================================================
# D. Пагинация по номерам страниц
# =====================================================================

def test_pagination_renders_page_numbers(admin_portal_client, db_session):
    """На странице комплектующих (большой выдаче) видны кнопки страниц."""
    _seed_many_components(db_session, n=200)  # 200/30 ≈ 7 страниц
    r = admin_portal_client.get("/databases/components")
    assert r.status_code == 200
    assert "kt-pagination-page" in r.text


def test_pagination_active_page_marked(admin_portal_client, db_session):
    """Текущая страница имеет класс kt-pagination-page-active."""
    _seed_many_components(db_session, n=200)
    r = admin_portal_client.get("/databases/components?page=2")
    assert r.status_code == 200
    assert "kt-pagination-page-active" in r.text


def test_pagination_dots_for_long_ranges(admin_portal_client, db_session):
    """На большой выдаче в пагинации появляется «…» между страницами."""
    _seed_many_components(db_session, n=600)  # 600/30 = 20 страниц
    r = admin_portal_client.get("/databases/components?page=10")
    assert r.status_code == 200
    assert "kt-pagination-ellipsis" in r.text or "…" in r.text


# =====================================================================
# E. Toggle text update в форме поставщика
# =====================================================================

def test_toggle_text_attributes_in_supplier_form(admin_portal_client):
    """Форма поставщика содержит data-toggle-text и class kt-toggle-text."""
    r = admin_portal_client.get("/databases/suppliers/new")
    assert r.status_code == 200
    assert 'class="toggle kt-toggle"' in r.text
    assert 'data-toggle-text="s-is-active"' in r.text
    assert 'data-toggle-on=' in r.text
    assert 'data-toggle-off=' in r.text
