"""Тесты UX-правок третьего раунда (мини-этап 9a-fixes-3).

Покрывают:
- /nomenclature: русские лейблы заголовков таблицы и категорий
  (Цена, ₽; Принтер; МФУ);
- /auctions/sku/{id}/details: лейблы «Цена:» и «Поставщик:» вместо
  «cost_base:».
"""
from __future__ import annotations

from sqlalchemy import text

from tests.test_portal.auctions_fixtures import (
    auctions_viewer,         # noqa: F401
    insert_printer_mfu,
    login_as,
)


def _insert_supplier(db_session, *, name: str = "ТестПоставщик") -> int:
    row = db_session.execute(
        text(
            "INSERT INTO suppliers (name, email, contact_person, is_active) "
            "VALUES (:n, '', '', TRUE) "
            "ON CONFLICT (name) DO UPDATE SET is_active = TRUE "
            "RETURNING id"
        ),
        {"n": name},
    ).first()
    db_session.commit()
    return int(row.id)


def _insert_supplier_price(
    db_session, *,
    supplier_id: int,
    component_id: int,
    category: str = "printer",
    price: float = 7987.12,
    stock_qty: int = 5,
) -> None:
    db_session.execute(
        text(
            "INSERT INTO supplier_prices "
            "(category, component_id, supplier_id, supplier_sku, price, "
            " currency, stock_qty, transit_qty) "
            "VALUES (:cat, :cid, :sid, 'SP-T', :p, 'RUB', :sq, 0)"
        ),
        {
            "cat": category, "cid": component_id, "sid": supplier_id,
            "p": price, "sq": stock_qty,
        },
    )
    db_session.commit()


# ============================================================
# /nomenclature: русские лейблы (#1)
# ============================================================

def test_nomenclature_price_header_in_russian(
    portal_client, auctions_viewer, db_session,
):
    """Заголовок колонки «cost_base, ₽» заменён на «Цена, ₽»."""
    insert_printer_mfu(db_session, sku="ru-hdr-1", category="printer")
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature")
    assert r.status_code == 200
    body = r.text
    assert "Цена, ₽" in body
    assert "cost_base, ₽" not in body


def test_nomenclature_category_label_printer_russian(
    portal_client, auctions_viewer, db_session,
):
    """В колонке «Категория» вместо printer — «Принтер»."""
    insert_printer_mfu(db_session, sku="ru-cat-pr", category="printer",
                       name="принтер для теста")
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature?q=ru-cat-pr")
    assert r.status_code == 200
    body = r.text
    # Текстовое значение «Принтер» появилось
    assert "Принтер" in body
    # Внутри строки таблицы (td c data-testid строки) латинский «printer»
    # больше не выводится как видимое значение колонки.
    # Не проверяем строго отсутствие, т.к. слово может появиться в SKU/имени;
    # достаточно проверить наличие русского лейбла.


def test_nomenclature_category_label_mfu_russian(
    portal_client, auctions_viewer, db_session,
):
    """В колонке «Категория» вместо mfu — «МФУ»."""
    insert_printer_mfu(db_session, sku="ru-cat-mfu", category="mfu",
                       name="мфу-тест")
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature?q=ru-cat-mfu")
    assert r.status_code == 200
    body = r.text
    assert "МФУ" in body


# ============================================================
# /auctions/sku/{id}/details: лейблы «Цена:» и «Поставщик:» (#2)
# ============================================================

def test_sku_details_price_label_russian(
    portal_client, auctions_viewer, db_session,
):
    """В модалке SKU details вместо «cost_base:» выводится «Цена:»."""
    sku_id = insert_printer_mfu(
        db_session, sku="DET-RU-1", brand="HP",
        name="HP-test", category="printer", cost_base_rub=7987.12,
    )
    login_as(portal_client, auctions_viewer)
    r = portal_client.get(f"/auctions/sku/{sku_id}/details")
    assert r.status_code == 200, r.status_code
    body = r.text
    assert 'data-testid="sku-price"' in body
    assert "Цена:" in body
    # Старый английский лейбл больше не должен присутствовать.
    assert "cost_base:" not in body


def test_sku_details_shows_cheapest_supplier(
    portal_client, auctions_viewer, db_session,
):
    """В модалке SKU details выводится поставщик с самой низкой ценой
    при stock_qty>0 в категории printer/mfu."""
    sku_id = insert_printer_mfu(
        db_session, sku="DET-SUP-1", brand="HP",
        name="HP-supplier", category="printer", cost_base_rub=10000.00,
    )
    # supplier_prices/suppliers не TRUNCATE-аются, поэтому чистим хвосты
    # с тем же component_id, чтобы тест был детерминированным.
    db_session.execute(
        text("DELETE FROM supplier_prices WHERE component_id = :id"),
        {"id": sku_id},
    )
    db_session.commit()
    sup_low = _insert_supplier(db_session, name="Дешёвый-9aFix3")
    sup_high = _insert_supplier(db_session, name="Дорогой-9aFix3")
    _insert_supplier_price(db_session, supplier_id=sup_high,
                           component_id=sku_id, category="printer",
                           price=20000.00, stock_qty=3)
    _insert_supplier_price(db_session, supplier_id=sup_low,
                           component_id=sku_id, category="printer",
                           price=8500.00, stock_qty=2)

    login_as(portal_client, auctions_viewer)
    r = portal_client.get(f"/auctions/sku/{sku_id}/details")
    assert r.status_code == 200
    body = r.text
    assert 'data-testid="sku-supplier"' in body
    assert "Поставщик:" in body
    assert "Дешёвый-9aFix3" in body
    # Дорогого поставщика не должно быть на видном месте — он не cheapest.
    assert "Дорогой-9aFix3" not in body


def test_sku_details_no_supplier_when_no_stock(
    portal_client, auctions_viewer, db_session,
):
    """Если у SKU нет supplier_prices с stock_qty>0 — блок «Поставщик»
    не выводится. supplier_prices/suppliers не TRUNCATE-аются между
    тестами, поэтому явно прибиваем строки на нашем component_id перед
    проверкой."""
    sku_id = insert_printer_mfu(
        db_session, sku="DET-NOSUP-1", brand="HP",
        name="HP-no-supplier", category="printer", cost_base_rub=10000.00,
    )
    db_session.execute(
        text("DELETE FROM supplier_prices WHERE component_id = :id"),
        {"id": sku_id},
    )
    db_session.commit()
    sup = _insert_supplier(db_session, name="ОстатокНоль-9aFix3")
    _insert_supplier_price(db_session, supplier_id=sup,
                           component_id=sku_id, category="printer",
                           price=12000.00, stock_qty=0)
    login_as(portal_client, auctions_viewer)
    r = portal_client.get(f"/auctions/sku/{sku_id}/details")
    assert r.status_code == 200
    body = r.text
    assert 'data-testid="sku-supplier"' not in body
    assert "Поставщик:" not in body
