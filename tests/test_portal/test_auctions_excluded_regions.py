"""Тесты дашборда /auctions для фикса 2026-05-13.

Покрывают:
- лот с `flags_jsonb.excluded_by_region=true` скрыт из инбокса по умолчанию,
- галочка `?include_excluded_regions=1` возвращает его в выдачу,
- UI-форма содержит чекбокс «показать стоп-регионы»
  (data-testid="filter-include-excluded-regions") в выключенном состоянии
  по умолчанию.
"""
from __future__ import annotations

from tests.test_portal.auctions_fixtures import (
    auctions_viewer,  # noqa: F401 — pytest fixture
    insert_tender,
    login_as,
)


def test_inbox_hides_lot_with_excluded_region_flag_by_default(
    portal_client, auctions_viewer, db_session,
):
    """Лот с flags_jsonb.excluded_by_region=true не виден в инбоксе
    без галочки."""
    # «Чистый» лот — должен быть виден.
    insert_tender(
        db_session, reg_number="visible-lot",
        customer_region="Татарстан", flags={},
        submit_deadline_offset_hours=-1,
    )
    # Лот из стоп-региона — по умолчанию скрыт.
    insert_tender(
        db_session, reg_number="hidden-lot",
        customer_region="Магаданская обл",
        flags={"excluded_by_region": True, "excluded_region_name": "Магаданская обл"},
        submit_deadline_offset_hours=-1,
    )

    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    assert 'data-testid="lot-row-visible-lot"' in r.text
    assert 'data-testid="lot-row-hidden-lot"' not in r.text


def test_inbox_shows_excluded_lot_when_checkbox_on(
    portal_client, auctions_viewer, db_session,
):
    """С `?include_excluded_regions=1` лот из стоп-региона возвращается."""
    insert_tender(
        db_session, reg_number="hidden-lot-2",
        customer_region="Приморский край",
        flags={"excluded_by_region": True, "excluded_region_name": "Приморский край"},
        submit_deadline_offset_hours=-1,
    )

    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions?include_excluded_regions=1")
    assert r.status_code == 200
    assert 'data-testid="lot-row-hidden-lot-2"' in r.text


def test_inbox_form_renders_include_excluded_regions_checkbox(
    portal_client, auctions_viewer, db_session,
):
    """UI: чекбокс «показать стоп-регионы» присутствует в форме фильтров,
    по умолчанию выключен."""
    # Минимальный seed, чтобы рендерилась форма (не empty-state).
    insert_tender(
        db_session, reg_number="any-lot",
        submit_deadline_offset_hours=-1,
    )

    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    assert 'data-testid="filter-include-excluded-regions"' in r.text
    # По умолчанию чекбокс выключен — не должно быть атрибута `checked`
    # внутри input'а с этим testid (грубая проверка).
    idx = r.text.find('data-testid="filter-include-excluded-regions"')
    # Берём фрагмент вокруг input'а и проверяем отсутствие "checked".
    snippet = r.text[max(0, idx - 200): idx + 100]
    assert "checked" not in snippet


def test_inbox_form_checkbox_checked_when_query_param_on(
    portal_client, auctions_viewer, db_session,
):
    """С `?include_excluded_regions=1` форма показывает чекбокс как отмеченный."""
    insert_tender(
        db_session, reg_number="any-lot-2",
        submit_deadline_offset_hours=-1,
    )

    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions?include_excluded_regions=1")
    assert r.status_code == 200
    idx = r.text.find('data-testid="filter-include-excluded-regions"')
    assert idx >= 0
    snippet = r.text[max(0, idx - 200): idx + 100]
    assert "checked" in snippet
