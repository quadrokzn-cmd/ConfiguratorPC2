"""Тесты UX-правок второго раунда (мини-этап 9a-fixes-2).

Покрывают:
- /nomenclature: пагинация (page=1/2 + блок пагинатора, страница > total_pages
  не падает), inline-описание атрибутов под именем SKU, отсутствие колонки
  «АТРИБУТЫ»;
- /auctions/{reg}: отсутствие префикса «primary:» перед моделью, русские
  лейблы контактов заказчика (ФИО / Телефон / Должность), отсутствие
  плашек атрибутов в развёрнутом details «Полный текст требования».
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from tests.test_portal.auctions_fixtures import (
    auctions_no_perm,        # noqa: F401
    auctions_viewer,         # noqa: F401
    auctions_settings_editor,  # noqa: F401
    insert_match,
    insert_printer_mfu,
    insert_tender,
    insert_tender_item,
    login_as,
)


# ============================================================
# /nomenclature: пагинация
# ============================================================

def test_nomenclature_pagination_block_present(
    portal_client, auctions_viewer, db_session,
):
    """При >1 страницы данных блок пагинатора рендерится в HTML."""
    for i in range(60):
        insert_printer_mfu(db_session, sku=f"page-test-{i:03d}",
                           brand=f"BrandZ{i:02d}", name=f"P{i:03d}")
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature")
    assert r.status_code == 200
    assert 'data-testid="pagination"' in r.text
    assert 'data-testid="page-current"' in r.text
    assert 'data-testid="page-next"' in r.text
    # Найдено: ≥60 SKU, страница 1 из ≥2.
    assert "Найдено:" in r.text
    assert "страница" in r.text


def test_nomenclature_page_2_returns_next_slice(
    portal_client, auctions_viewer, db_session,
):
    """page=2 возвращает следующие 50, не первые."""
    for i in range(60):
        insert_printer_mfu(db_session, sku=f"slice-{i:03d}",
                           brand=f"AAAA{i:02d}", name=f"slice-{i:03d}")
    login_as(portal_client, auctions_viewer)
    # На странице 1 нет id 51-60 (по умолчанию per_page=50).
    r1 = portal_client.get("/nomenclature?q=slice")
    assert r1.status_code == 200
    # Страница 2 — следующие 10 (51-60), и эти sku 'slice-050'-'slice-059'
    r2 = portal_client.get("/nomenclature?q=slice&page=2")
    assert r2.status_code == 200
    # Минимум одно из тех, что не было на page=1, должно появиться на page=2.
    # (Сортировка по brand → name → AAAA50, AAAA51, ..., AAAA59 — последние 10)
    assert "slice-050" not in r1.text or "slice-050" in r2.text
    assert 'data-testid="pagination"' in r2.text


def test_nomenclature_page_beyond_does_not_crash(
    portal_client, auctions_viewer, db_session,
):
    """page=N (N > total_pages) — страница рендерится, empty-state виден."""
    insert_printer_mfu(db_session, sku="only-one")
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature?page=99")
    assert r.status_code == 200
    assert 'data-testid="empty-state"' in r.text


def test_nomenclature_pagination_preserves_filters(
    portal_client, auctions_viewer, db_session,
):
    """Фильтры (brand/q/category) пробрасываются в ссылки пагинатора."""
    for i in range(60):
        insert_printer_mfu(db_session, sku=f"flt-{i:03d}",
                           brand="PaginBrand", name=f"flt-{i:03d}")
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature?brand=PaginBrand")
    assert r.status_code == 200
    # Ссылка «Следующая →» содержит brand-фильтр.
    assert "brand=PaginBrand" in r.text
    assert "page=2" in r.text


# ============================================================
# /nomenclature: атрибуты как серая строка под именем
# ============================================================

def test_nomenclature_no_attributes_column(
    portal_client, auctions_viewer, db_session,
):
    """Колонка «Атрибуты» удалена из заголовка таблицы."""
    insert_printer_mfu(db_session)
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature")
    assert r.status_code == 200
    # Заголовок столбца «Атрибуты» больше не должен присутствовать как th.
    assert "<th class=\"text-left\">Атрибуты</th>" not in r.text


def test_nomenclature_attrs_inline_under_name(
    portal_client, auctions_viewer, db_session,
):
    """Под именем SKU выводится серая строка с не-n/a атрибутами."""
    sku_id = insert_printer_mfu(
        db_session, sku="inl-001", brand="HP", name="HP test inline",
    )
    attrs = {
        "print_speed_ppm":   8,
        "colorness":         "ч/б",
        "max_format":        "A4",
        "resolution_dpi":    200,
        "usb":               "yes",
        "duplex":            "n/a",
    }
    db_session.execute(
        text(
            "UPDATE printers_mfu SET attrs_jsonb = CAST(:a AS JSONB) WHERE id = :id"
        ),
        {"a": json.dumps(attrs, ensure_ascii=False), "id": sku_id},
    )
    db_session.commit()

    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature")
    assert r.status_code == 200
    body = r.text
    assert f'data-testid="attrs-inline-{sku_id}"' in body
    assert "8 стр/мин" in body
    assert "200 dpi" in body
    assert "USB" in body
    # n/a duplex не должен появиться в строке.
    assert "duplex" not in body or "duplex: n/a" not in body


# ============================================================
# /auctions/{reg}: «primary:» убран
# ============================================================

REG_PRIMARY = "9a-fix2-prim-001"


@pytest.fixture()
def lot_with_primary(db_session):
    insert_tender(db_session, reg_number=REG_PRIMARY, status="new",
                  submit_deadline_offset_hours=72)
    item_id = insert_tender_item(db_session, tender_id=REG_PRIMARY,
                                 name="МФУ A4 ч/б", required_attrs={})
    sku_id = insert_printer_mfu(
        db_session, sku="primprefix-test", brand="HP",
        name="HP LaserJet тест",
    )
    insert_match(db_session, tender_item_id=item_id, nomenclature_id=sku_id,
                 margin_pct=20.0)
    return REG_PRIMARY


def test_card_no_primary_prefix_text(
    portal_client, auctions_viewer, lot_with_primary,
):
    """В HTML карточки нет служебного префикса «primary:» перед моделью."""
    login_as(portal_client, auctions_viewer)
    r = portal_client.get(f"/auctions/{lot_with_primary}")
    assert r.status_code == 200
    # Раньше был span с text-caption text-ink-muted и текстом "primary:".
    # Сам data-testid="primary-..." остаётся (это семантический тестовый
    # маркер, не видимый текст), но именно текстовый префикс убираем.
    assert ">primary:</span>" not in r.text


# ============================================================
# /auctions/{reg}: русские лейблы контактов
# ============================================================

REG_CONTACTS = "9a-fix2-contacts-001"


def test_card_russian_contact_labels(
    portal_client, auctions_viewer, db_session,
):
    """В HTML карточки лейблы контактов заказчика — на русском (#6)."""
    insert_tender(db_session, reg_number=REG_CONTACTS, status="new",
                  submit_deadline_offset_hours=48)
    contacts = {
        "fio":      "Милицина Л. Ю.",
        "phone":    "7-391-2226780",
        "position": "Главный специалист",
        "email":    "test@example.ru",
    }
    db_session.execute(
        text(
            "UPDATE tenders SET customer_contacts_jsonb = CAST(:c AS JSONB) "
            "WHERE reg_number = :rn"
        ),
        {"c": json.dumps(contacts, ensure_ascii=False), "rn": REG_CONTACTS},
    )
    db_session.commit()

    login_as(portal_client, auctions_viewer)
    r = portal_client.get(f"/auctions/{REG_CONTACTS}")
    assert r.status_code == 200
    body = r.text
    assert 'data-testid="customer-contacts"' in body
    assert "ФИО:" in body
    assert "Телефон:" in body
    assert "Должность:" in body
    # email — оставили как есть.
    assert "email:" in body
    # Старые англ. лейблы fio:/phone:/position: — больше не должны
    # рендериться как видимые подписи (они только в JSON, не как лейблы).
    assert ">fio:</span>" not in body
    assert ">phone:</span>" not in body
    assert ">position:</span>" not in body


# ============================================================
# /auctions/{reg}: плашки атрибутов отсутствуют, когда есть details
# ============================================================

REG_DETAILS = "9a-fix2-details-001"


def test_card_long_name_has_details_no_attr_badges(
    portal_client, auctions_viewer, db_session,
):
    """Если name>80 символов — рендерится <details>, и плашки атрибутов
    позиции рядом не выводятся (#4)."""
    insert_tender(db_session, reg_number=REG_DETAILS, status="new",
                  submit_deadline_offset_hours=72)
    # name >80 символов → details-блок появится.
    long_name = (
        "Принтер Время выхода первого черно-белого отпечатка ≤ 7 С, "
        "разрешение 1200 dpi, USB, дуплекс"
    )
    insert_tender_item(
        db_session, tender_id=REG_DETAILS, position_num=1,
        name=long_name,
        required_attrs={"usb": "yes", "duplex": "yes", "colorness": "ч/б"},
    )

    login_as(portal_client, auctions_viewer)
    r = portal_client.get(f"/auctions/{REG_DETAILS}")
    assert r.status_code == 200
    body = r.text
    # details-блок появился.
    assert 'data-testid="full-text-details"' in body
    # Плашек атрибутов позиции нет (когда details есть, чтобы не дублировать).
    assert 'data-testid="item-attrs-1"' not in body


def test_card_short_name_keeps_attr_badges(
    portal_client, auctions_viewer, db_session,
):
    """Если name короткое — details нет, плашки атрибутов остаются (#4)."""
    reg = "9a-fix2-short-001"
    insert_tender(db_session, reg_number=reg, status="new",
                  submit_deadline_offset_hours=72)
    insert_tender_item(
        db_session, tender_id=reg, position_num=1,
        name="МФУ A4 ч/б",
        required_attrs={"usb": "yes", "colorness": "ч/б"},
    )

    login_as(portal_client, auctions_viewer)
    r = portal_client.get(f"/auctions/{reg}")
    assert r.status_code == 200
    body = r.text
    # details-блока нет, плашки на своём месте.
    assert 'data-testid="full-text-details"' not in body
    assert 'data-testid="item-attrs-1"' in body
