"""Тесты карточки лота /auctions/{reg_number} — этап 9a слияния QT↔C-PC2.

Проверяем:
- 404 на несуществующий reg_number,
- права (manager без `auctions` → 403),
- POST status: разрешённые/запрещённые переходы,
- POST contract: только при status='won',
- POST note,
- запись в audit_log при мутациях.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.test_portal.auctions_fixtures import (
    auctions_no_perm,        # noqa: F401
    auctions_viewer,         # noqa: F401
    auctions_editor,         # noqa: F401
    auctions_settings_editor,  # noqa: F401
    insert_match,
    insert_printer_mfu,
    insert_tender,
    insert_tender_item,
    login_as,
)
from tests.test_portal.conftest import extract_csrf


REG = "0700000000000000007"


@pytest.fixture()
def seeded_lot(db_session):
    """Создаёт один лот в статусе 'new' с одной позицией и primary-матчем.
    Возвращает reg_number."""
    insert_tender(db_session, reg_number=REG, status="new",
                  submit_deadline_offset_hours=72)
    item_id = insert_tender_item(db_session, tender_id=REG)
    sku_id = insert_printer_mfu(db_session)
    insert_match(db_session, tender_item_id=item_id, nomenclature_id=sku_id,
                 margin_pct=20.0)
    return REG


# ---- GET карточки -----------------------------------------------------

def test_card_404_unknown(portal_client, auctions_viewer):
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions/does-not-exist")
    assert r.status_code == 404, r.status_code


def test_card_manager_no_perm_403(portal_client, auctions_no_perm, seeded_lot):
    login_as(portal_client, auctions_no_perm)
    r = portal_client.get(f"/auctions/{seeded_lot}")
    assert r.status_code == 403


def test_card_viewer_200_renders_status_badge(
    portal_client, auctions_viewer, seeded_lot,
):
    login_as(portal_client, auctions_viewer)
    r = portal_client.get(f"/auctions/{seeded_lot}")
    assert r.status_code == 200
    assert 'data-testid="card-status"' in r.text
    assert 'data-testid="card-items"' in r.text
    # Кнопок переходов у viewer нет (только у auctions_edit_status).
    assert 'data-testid="status-btn-in_review"' not in r.text


def test_card_editor_sees_status_buttons(
    portal_client, auctions_editor, seeded_lot,
):
    login_as(portal_client, auctions_editor)
    r = portal_client.get(f"/auctions/{seeded_lot}")
    assert r.status_code == 200
    # new → in_review/skipped — обе кнопки видны.
    assert 'data-testid="status-btn-in_review"' in r.text
    assert 'data-testid="status-btn-skipped"' in r.text


# ---- POST status ------------------------------------------------------

def test_status_change_no_perm_403(
    portal_client, auctions_viewer, seeded_lot,
):
    """Manager с auctions (view), но без auctions_edit_status → 403."""
    login_as(portal_client, auctions_viewer)
    r = portal_client.get(f"/auctions/{seeded_lot}")
    assert r.status_code == 200
    csrf = extract_csrf(r.text)
    r = portal_client.post(
        f"/auctions/{seeded_lot}/status",
        data={"new_status": "in_review", "csrf_token": csrf},
    )
    assert r.status_code == 403


def test_status_valid_transition_new_to_in_review(
    portal_client, auctions_editor, seeded_lot, db_session,
):
    login_as(portal_client, auctions_editor)
    r = portal_client.get(f"/auctions/{seeded_lot}")
    csrf = extract_csrf(r.text)

    r = portal_client.post(
        f"/auctions/{seeded_lot}/status",
        data={"new_status": "in_review", "csrf_token": csrf},
    )
    assert r.status_code in (302, 303), r.status_code
    # Статус в БД обновлён.
    new_status = db_session.execute(
        text("SELECT status FROM tender_status WHERE tender_id = :rn"),
        {"rn": seeded_lot},
    ).scalar()
    assert new_status == "in_review"
    # Audit-запись.
    n_audits = db_session.execute(
        text("SELECT COUNT(*) FROM audit_log WHERE action = 'auction.status_change'"),
    ).scalar()
    assert n_audits >= 1


def test_status_invalid_transition_new_to_won_400(
    portal_client, auctions_editor, seeded_lot,
):
    login_as(portal_client, auctions_editor)
    r = portal_client.get(f"/auctions/{seeded_lot}")
    csrf = extract_csrf(r.text)
    r = portal_client.post(
        f"/auctions/{seeded_lot}/status",
        data={"new_status": "won", "csrf_token": csrf},
    )
    assert r.status_code == 400, r.status_code


def test_status_csrf_invalid_400(
    portal_client, auctions_editor, seeded_lot,
):
    login_as(portal_client, auctions_editor)
    r = portal_client.post(
        f"/auctions/{seeded_lot}/status",
        data={"new_status": "in_review", "csrf_token": "wrong-token"},
    )
    assert r.status_code == 400


# ---- POST contract ----------------------------------------------------

def test_contract_update_after_won(
    portal_client, auctions_editor, db_session,
):
    """Перевести в won через цепочку, потом обновить контрактные даты."""
    insert_tender(db_session, reg_number="contract-test",
                  submit_deadline_offset_hours=72, status="submitted")
    login_as(portal_client, auctions_editor)
    r = portal_client.get("/auctions/contract-test")
    csrf = extract_csrf(r.text)

    # submitted → won
    portal_client.post(
        "/auctions/contract-test/status",
        data={"new_status": "won", "csrf_token": csrf},
    )

    # контракт
    r = portal_client.get("/auctions/contract-test")
    csrf2 = extract_csrf(r.text)
    r = portal_client.post(
        "/auctions/contract-test/contract",
        data={
            "csrf_token":               csrf2,
            "contract_registry_number": "12345-abc",
            "signed_at":                "2026-05-15",
            "delivery_at":              "2026-06-01",
            "acceptance_at":            "",
            "payment_at":               "",
        },
    )
    assert r.status_code in (302, 303), r.status_code
    crn = db_session.execute(
        text(
            "SELECT contract_registry_number, contract_key_dates_jsonb "
            "FROM tender_status WHERE tender_id = 'contract-test'"
        ),
    ).first()
    assert crn.contract_registry_number == "12345-abc"
    assert crn.contract_key_dates_jsonb.get("signed_at") == "2026-05-15"
    assert crn.contract_key_dates_jsonb.get("delivery_at") == "2026-06-01"
    assert "acceptance_at" not in crn.contract_key_dates_jsonb


# ---- POST note --------------------------------------------------------

def test_note_update(
    portal_client, auctions_editor, seeded_lot, db_session,
):
    login_as(portal_client, auctions_editor)
    r = portal_client.get(f"/auctions/{seeded_lot}")
    csrf = extract_csrf(r.text)
    r = portal_client.post(
        f"/auctions/{seeded_lot}/note",
        data={"note": "проверить ТЗ на пусконаладку", "csrf_token": csrf},
    )
    assert r.status_code in (302, 303), r.status_code
    note = db_session.execute(
        text("SELECT note FROM tender_status WHERE tender_id = :rn"),
        {"rn": seeded_lot},
    ).scalar()
    assert note == "проверить ТЗ на пусконаладку"
