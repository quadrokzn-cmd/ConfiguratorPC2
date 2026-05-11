"""Тесты UX-правок четвёртого раунда (мини-этап 9a-fixes-4).

Покрывают /nomenclature (Справочник печати):
- удалена колонка KTRU из заголовка и тела таблицы;
- под ценой выводится имя cheapest_supplier;
- ширины колонок «SKU/Название» и «Цена, ₽» пересобраны (SKU получил
  min-width, Цена расширилась до w-44).
"""
from __future__ import annotations

from sqlalchemy import text

from tests.test_portal.auctions_fixtures import (
    auctions_viewer,         # noqa: F401
    insert_printer_mfu,
    login_as,
)


def _insert_supplier(db_session, *, name: str) -> int:
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
# #1. Колонка KTRU удалена из таблицы /nomenclature
# ============================================================

def test_nomenclature_ktru_column_removed_from_header(
    portal_client, auctions_viewer, db_session,
):
    """В шапке таблицы больше нет колонки «KTRU»."""
    insert_printer_mfu(db_session, sku="ktru-hdr-1", category="printer")
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature")
    assert r.status_code == 200
    body = r.text
    # В шапке таблицы нет ячейки <th>...KTRU</th>.
    assert ">KTRU</th>" not in body
    # Прочие заголовки на месте — sanity check, что мы не сломали таблицу
    # целиком и сравнение выше осмысленно.
    assert "SKU / Название" in body
    assert "Цена, ₽" in body


def test_nomenclature_ktru_codes_not_rendered_in_row(
    portal_client, auctions_viewer, db_session,
):
    """KTRU-коды самого SKU не появляются в строке таблицы (UI-удаление,
    БД хранит коды и матчинг по ним продолжает работать)."""
    sku_id = insert_printer_mfu(
        db_session, sku="ktru-row-1", brand="HP",
        name="HP-no-ktru-col", category="printer",
    )
    # Прописываем массив KTRU-кодов — раньше они бы вылились в td.
    db_session.execute(
        text(
            "UPDATE printers_mfu SET ktru_codes_array = :codes WHERE id = :id"
        ),
        {"codes": ["26.20.18.000-99999999"], "id": sku_id},
    )
    db_session.commit()
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature?q=ktru-row-1")
    assert r.status_code == 200
    body = r.text
    # Сам код в БД лежит (data-ktru у tr остался) — но в видимой td его нет.
    # Грубо проверяем: код не выводится как видимое значение колонки,
    # т.е. не идёт в обёртке <td class="text-caption text-ink-secondary">.
    assert (
        '<td class="text-caption text-ink-secondary">' not in body
        or "26.20.18.000-99999999</td>" not in body
    )


# ============================================================
# #2. Имя cheapest_supplier выводится под ценой
# ============================================================

def test_nomenclature_shows_cheapest_supplier_under_price(
    portal_client, auctions_viewer, db_session,
):
    """В ячейке цены под числом — имя поставщика с минимальной ценой при
    stock_qty>0 (так же, как в карточке лота)."""
    sku_id = insert_printer_mfu(
        db_session, sku="SUP-NOM-1", brand="HP",
        name="HP-supplier-cell", category="printer", cost_base_rub=38070.10,
    )
    # supplier_prices/suppliers не TRUNCATE-аются — чистим хвосты на нашем SKU.
    db_session.execute(
        text("DELETE FROM supplier_prices WHERE component_id = :id"),
        {"id": sku_id},
    )
    db_session.commit()
    sup_low = _insert_supplier(db_session, name="Дешёвый-9aFix4")
    sup_high = _insert_supplier(db_session, name="Дорогой-9aFix4")
    _insert_supplier_price(
        db_session, supplier_id=sup_high, component_id=sku_id,
        category="printer", price=42000.00, stock_qty=2,
    )
    _insert_supplier_price(
        db_session, supplier_id=sup_low, component_id=sku_id,
        category="printer", price=37500.00, stock_qty=4,
    )

    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature?q=SUP-NOM-1")
    assert r.status_code == 200
    body = r.text
    assert f'data-testid="cheapest-supplier-{sku_id}"' in body
    assert "Дешёвый-9aFix4" in body
    # Дорогого поставщика в строке быть не должно — он не cheapest.
    assert "Дорогой-9aFix4" not in body


def test_nomenclature_no_supplier_line_when_no_stock(
    portal_client, auctions_viewer, db_session,
):
    """Если у SKU нет supplier_prices с stock_qty>0 — строка под ценой
    не выводится (div data-testid='cheapest-supplier-...' отсутствует)."""
    sku_id = insert_printer_mfu(
        db_session, sku="SUP-NOM-EMPTY", brand="HP",
        name="HP-no-supplier-cell", category="printer", cost_base_rub=15000.00,
    )
    db_session.execute(
        text("DELETE FROM supplier_prices WHERE component_id = :id"),
        {"id": sku_id},
    )
    db_session.commit()
    sup = _insert_supplier(db_session, name="ОстатокНоль-9aFix4")
    _insert_supplier_price(
        db_session, supplier_id=sup, component_id=sku_id,
        category="printer", price=20000.00, stock_qty=0,
    )

    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature?q=SUP-NOM-EMPTY")
    assert r.status_code == 200
    body = r.text
    assert f'data-testid="cheapest-supplier-{sku_id}"' not in body
    assert "ОстатокНоль-9aFix4" not in body


# ============================================================
# #3. Ширины колонок «SKU/Название» и «Цена, ₽» пересобраны
# ============================================================

def test_nomenclature_column_widths_redistributed(
    portal_client, auctions_viewer, db_session,
):
    """SKU/Название получает min-width (наследует «освобождённое» от KTRU
    пространство), Цена расширена до w-44 (была w-32)."""
    insert_printer_mfu(db_session, sku="width-1", category="printer")
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature")
    assert r.status_code == 200
    body = r.text
    # У SKU/Название появился min-w-[40%] (Tailwind arbitrary value).
    assert 'min-w-[40%]">SKU / Название</th>' in body
    # У «Цена, ₽» теперь w-44 (раньше было w-32).
    assert 'w-44">Цена, ₽</th>' in body
    assert 'w-32">Цена, ₽</th>' not in body
