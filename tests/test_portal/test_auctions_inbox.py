"""Тесты страницы /auctions (inbox) — этап 9a слияния QT↔C-PC2.

Проверяем:
- авторизацию (anon → 302),
- права (manager без `auctions` → 403),
- секции (рендер 200 OK + видны заголовки секций),
- фильтры (status / nmck / q / urgent_only),
- empty state (когда таблица пустая или фильтр обнуляет результат).
"""
from __future__ import annotations

import pytest

from tests.test_portal.auctions_fixtures import (
    auctions_no_perm,        # noqa: F401 — pytest fixture
    auctions_viewer,         # noqa: F401
    auctions_settings_editor,  # noqa: F401
    insert_match,
    insert_printer_mfu,
    insert_tender,
    insert_tender_item,
    login_as,
)


# ---- Авторизация / права ----------------------------------------------

def test_inbox_anon_redirected_to_login(portal_client):
    r = portal_client.get("/auctions")
    assert r.status_code in (302, 303), r.status_code
    assert "/login" in r.headers.get("location", "")


def test_inbox_manager_no_auctions_perm_403(portal_client, auctions_no_perm):
    login_as(portal_client, auctions_no_perm)
    r = portal_client.get("/auctions")
    assert r.status_code == 403, r.status_code


def test_inbox_manager_with_auctions_perm_200(
    portal_client, auctions_viewer, db_session,
):
    """С данными в БД секции рендерятся; без данных — empty state."""
    insert_tender(db_session, reg_number="seed-for-sections",
                  submit_deadline_offset_hours=-72)
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200, r.status_code
    for sec in ("urgent", "ready_to_review", "in_work", "archive"):
        assert f'data-testid="section-{sec}"' in r.text, f"section {sec} не найдена"


def test_inbox_admin_200(portal_client, admin_user):
    login_as(portal_client, admin_user)
    r = portal_client.get("/auctions")
    assert r.status_code == 200


# ---- Empty state -------------------------------------------------------

def test_inbox_empty_state_with_settings_perm_shows_run_ingest(
    portal_client, auctions_settings_editor,
):
    """Пустая БД + есть auctions_edit_settings → видна кнопка ingest."""
    login_as(portal_client, auctions_settings_editor)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    assert 'data-testid="empty-state"' in r.text
    assert 'data-testid="run-ingest"' in r.text


def test_inbox_empty_state_view_only_no_run_button(
    portal_client, auctions_viewer,
):
    """Пустая БД + только view-право → кнопки ingest нет."""
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    assert 'data-testid="empty-state"' in r.text
    assert 'data-testid="run-ingest"' not in r.text


# ---- Секции (с реальными данными) -------------------------------------

def test_inbox_lot_appears_in_archive_when_overdue(
    portal_client, auctions_viewer, db_session,
):
    """Лот с status='new' и overdue-дедлайном попадает в секцию архив."""
    insert_tender(
        db_session, reg_number="0816500000626007072",
        submit_deadline_offset_hours=-72,  # дедлайн 3 дня назад
    )
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    assert 'data-testid="lot-row-0816500000626007072"' in r.text
    # Архивная секция содержит ряд (грубая проверка близости).
    archive_pos = r.text.find('data-testid="section-archive"')
    row_pos = r.text.find('data-testid="lot-row-0816500000626007072"')
    assert archive_pos != -1 and row_pos != -1
    assert row_pos > archive_pos, "ряд должен быть в секции 'архив'"


def test_inbox_urgent_lot_in_urgent_section(
    portal_client, auctions_viewer, db_session,
):
    """status='new' + дедлайн в ближайшие 12 часов → секция 'Срочно'."""
    insert_tender(
        db_session, reg_number="0123456789012345678",
        submit_deadline_offset_hours=12,  # через 12 часов
        status="new",
    )
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    urgent_pos = r.text.find('data-testid="section-urgent"')
    row_pos = r.text.find('data-testid="lot-row-0123456789012345678"')
    assert urgent_pos != -1 and row_pos != -1
    assert row_pos > urgent_pos, "ряд должен быть в секции 'срочно'"


def test_inbox_ready_to_review_with_high_margin(
    portal_client, auctions_viewer, db_session,
):
    """status='new' + не overdue + primary >= margin_threshold (15%)
    → секция 'готовы к ревью'."""
    insert_tender(
        db_session, reg_number="0987654321098765432",
        submit_deadline_offset_hours=72,
        status="new",
    )
    item_id = insert_tender_item(db_session, tender_id="0987654321098765432")
    sku_id = insert_printer_mfu(db_session, sku="ready-sku-1")
    insert_match(
        db_session, tender_item_id=item_id, nomenclature_id=sku_id,
        margin_pct=30.0,  # выше порога 15
    )
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    ready_pos = r.text.find('data-testid="section-ready_to_review"')
    row_pos = r.text.find('data-testid="lot-row-0987654321098765432"')
    assert ready_pos != -1 and row_pos != -1
    assert row_pos > ready_pos, "ряд должен быть в 'готовы к ревью'"


# ---- Фильтры ----------------------------------------------------------

def test_inbox_status_filter_narrows_results(
    portal_client, auctions_viewer, db_session,
):
    insert_tender(db_session, reg_number="aaa", status="new",
                  submit_deadline_offset_hours=-72)
    insert_tender(db_session, reg_number="bbb", status="won",
                  submit_deadline_offset_hours=-72)
    login_as(portal_client, auctions_viewer)

    # Фильтр только 'won'
    r = portal_client.get("/auctions?status=won")
    assert r.status_code == 200
    assert 'data-testid="lot-row-bbb"' in r.text
    assert 'data-testid="lot-row-aaa"' not in r.text


def test_inbox_search_filter_by_reg_number(
    portal_client, auctions_viewer, db_session,
):
    insert_tender(db_session, reg_number="searchable123",
                  submit_deadline_offset_hours=-1)
    insert_tender(db_session, reg_number="otherone456",
                  submit_deadline_offset_hours=-1)
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions?q=searchable")
    assert r.status_code == 200
    assert 'data-testid="lot-row-searchable123"' in r.text
    assert 'data-testid="lot-row-otherone456"' not in r.text


def test_inbox_nmck_range_filter(
    portal_client, auctions_viewer, db_session,
):
    insert_tender(db_session, reg_number="cheap", nmck_total=10000.00,
                  submit_deadline_offset_hours=-1)
    insert_tender(db_session, reg_number="expensive", nmck_total=500000.00,
                  submit_deadline_offset_hours=-1)
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions?nmck_min=100000")
    assert r.status_code == 200
    assert 'data-testid="lot-row-expensive"' in r.text
    assert 'data-testid="lot-row-cheap"' not in r.text
