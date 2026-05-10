"""Тесты UX-правок мини-этапа 9a-fixes.

Покрывают:
- GET /auctions/sku/{nomenclature_id}/details — права, 200/404,
  содержимое (атрибуты + KTRU);
- inbox: ссылка на ЕИС у каждой строки лота;
- ru_money фильтр через рендер inbox-таблицы (форматирование сумм).
"""
from __future__ import annotations

import pytest

from tests.test_portal.auctions_fixtures import (
    auctions_no_perm,        # noqa: F401
    auctions_viewer,         # noqa: F401
    auctions_settings_editor,  # noqa: F401
    insert_printer_mfu,
    insert_tender,
    login_as,
)


# ---- /auctions/sku/{id}/details ---------------------------------------

def test_sku_details_anon_redirect(portal_client):
    r = portal_client.get("/auctions/sku/1/details")
    assert r.status_code in (302, 303)
    assert "/login" in r.headers.get("location", "")


def test_sku_details_no_perm_403(portal_client, auctions_no_perm, db_session):
    sku_id = insert_printer_mfu(db_session, sku="np-1")
    login_as(portal_client, auctions_no_perm)
    r = portal_client.get(f"/auctions/sku/{sku_id}/details")
    assert r.status_code == 403


def test_sku_details_unknown_404(portal_client, auctions_viewer):
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions/sku/9999999/details")
    assert r.status_code == 404


def test_sku_details_viewer_200_renders_attrs(
    portal_client, auctions_viewer, db_session,
):
    """С permission 'auctions' и с attrs_jsonb содержимое — таблица атрибутов."""
    from sqlalchemy import text
    import json

    sku_id = insert_printer_mfu(
        db_session, sku="DET-001", brand="HP", name="HP-test-printer",
        category="printer",
    )
    # Дозаливаем 9 атрибутов вручную (фабрика создаёт пустой attrs_jsonb).
    attrs = {
        "print_speed_ppm": 30,
        "colorness": "ч/б",
        "max_format": "A4",
        "duplex": "yes",
        "resolution_dpi": 1200,
        "network_interface": ["LAN"],
        "usb": "yes",
        "starter_cartridge_pages": 1500,
        "print_technology": "лазерная",
    }
    db_session.execute(
        text(
            "UPDATE printers_mfu SET attrs_jsonb = CAST(:a AS JSONB), "
            "  attrs_source = 'manual', "
            "  ktru_codes_array = ARRAY['26.20.16.120-00000001'] "
            "WHERE id = :id"
        ),
        {"a": json.dumps(attrs, ensure_ascii=False), "id": sku_id},
    )
    db_session.commit()

    login_as(portal_client, auctions_viewer)
    r = portal_client.get(f"/auctions/sku/{sku_id}/details")
    assert r.status_code == 200, r.status_code
    body = r.text
    # Таблица атрибутов есть.
    assert 'data-testid="sku-details-fragment"' in body
    # Все 9 ключей выведены.
    for key in attrs.keys():
        assert key in body, f"ключ {key} не виден"
    # KTRU выведен.
    assert "26.20.16.120-00000001" in body


# ---- inbox: ссылка на ЕИС --------------------------------------------

def test_inbox_row_has_eis_link(
    portal_client, auctions_viewer, db_session,
):
    """В каждой строке инбокса — <a target=_blank> ссылка на zakupki.gov.ru."""
    insert_tender(
        db_session, reg_number="eis-link-test",
        submit_deadline_offset_hours=-72,
    )
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    # фабрика insert_tender ставит url='https://zakupki.gov.ru/test'
    assert 'data-testid="eis-link-eis-link-test"' in r.text
    assert 'target="_blank"' in r.text
    assert "zakupki.gov.ru" in r.text


# ---- ru_money: форматирование сумм ------------------------------------

def test_inbox_nmck_uses_ru_money_format(
    portal_client, auctions_viewer, db_session,
):
    """nmck_total отображается с разделителем разрядов и запятой."""
    insert_tender(
        db_session, reg_number="money-fmt-001",
        nmck_total=5348890.31,
        submit_deadline_offset_hours=-1,
    )
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    # NBSP (U+00A0) разделяет тысячи + запятая как десятичный.
    assert "5 348 890,31" in r.text, (
        "ожидался формат «5 348 890,31» (NBSP + запятая), "
        f"получен HTML: {r.text[:500]}"
    )
