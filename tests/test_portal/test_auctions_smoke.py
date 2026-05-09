"""End-to-end smoke модуля «Аукционы» — этап 9a слияния QT↔C-PC2.

Один тест, проходящий golden path в TestClient (заменяет ручной browser
smoke из DoD §4.8): admin → / → /auctions → карточка лота →
переход new→in_review → /auctions/settings → change margin_threshold →
снова /auctions → /nomenclature. Все шаги — 200/302 без падений.
"""
from __future__ import annotations

from sqlalchemy import text

from tests.test_portal.auctions_fixtures import (
    insert_match,
    insert_printer_mfu,
    insert_tender,
    insert_tender_item,
    login_as,
)
from tests.test_portal.conftest import extract_csrf


def test_auctions_golden_path_e2e(portal_client, admin_user, db_session):
    """Один длинный сценарий: admin проходит все ключевые экраны и мутации.

    Падение любого шага → красный тест с точкой отказа в ассерте."""
    # Сидим один лот с primary-матчем выше порога 15%.
    rn = "smoke-lot-001"
    insert_tender(db_session, reg_number=rn, customer="Smoke-заказчик",
                  customer_region="Татарстан",
                  submit_deadline_offset_hours=72, status="new")
    item_id = insert_tender_item(db_session, tender_id=rn, name="Smoke МФУ A4")
    sku_id = insert_printer_mfu(
        db_session, sku="SMOKE-MFU-001", brand="HP",
        name="Smoke HP MFU", category="mfu", cost_base_rub=20000.00,
    )
    insert_match(
        db_session, tender_item_id=item_id, nomenclature_id=sku_id,
        margin_pct=33.0,
    )

    login_as(portal_client, admin_user)

    # 1) Главная: статус-код и виджет аукционов виден (admin → True).
    r = portal_client.get("/")
    assert r.status_code == 200
    assert 'data-testid="widget-auctions"' in r.text, "виджет аукционов не виден"

    # 2) /auctions: видно ряд лота и секция ready_to_review (margin 33% > 15%).
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    assert f'data-testid="lot-row-{rn}"' in r.text
    assert 'data-testid="section-ready_to_review"' in r.text

    # 3) Карточка лота — видны items и кнопки переходов.
    r = portal_client.get(f"/auctions/{rn}")
    assert r.status_code == 200
    assert 'data-testid="status-btn-in_review"' in r.text
    csrf = extract_csrf(r.text)

    # 4) Переход new → in_review.
    r = portal_client.post(
        f"/auctions/{rn}/status",
        data={"new_status": "in_review", "csrf_token": csrf},
    )
    assert r.status_code in (302, 303)

    # 5) /auctions/settings: меняем margin_threshold с 15 на 50.
    r = portal_client.get("/auctions/settings")
    assert r.status_code == 200
    csrf2 = extract_csrf(r.text)
    r = portal_client.post(
        "/auctions/settings/save",
        data={
            "csrf_token":              csrf2,
            "margin_threshold_pct":    "50",
            "nmck_min_rub":            "30000",
            "max_price_per_unit_rub":  "300000",
            "deadline_alert_hours":    "24",
            "contract_reminder_days":  "3",
            "auctions_ingest_enabled": "on",
        },
    )
    assert r.status_code in (302, 303)
    val = db_session.execute(
        text("SELECT value FROM settings WHERE key='margin_threshold_pct'"),
    ).scalar()
    assert val == "50"

    # 6) Возврат в /auctions: лот теперь в in_work (статус in_review),
    # ready_to_review должна сжаться.
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    in_work_pos = r.text.find('data-testid="section-in_work"')
    row_pos = r.text.find(f'data-testid="lot-row-{rn}"')
    assert in_work_pos != -1 and row_pos != -1
    assert row_pos > in_work_pos, "лот должен быть в in_work после in_review"

    # 7) /nomenclature: видно SKU.
    r = portal_client.get("/nomenclature")
    assert r.status_code == 200
    assert "SMOKE-MFU-001" in r.text

    # 8) Inline-edit cost_base.
    r = portal_client.get("/nomenclature")
    csrf3 = extract_csrf(r.text)
    r = portal_client.post(
        f"/nomenclature/{sku_id}/cost-base",
        data={"cost_base_rub": "21500.00", "csrf_token": csrf3},
    )
    assert r.status_code == 200
    new_cb = db_session.execute(
        text("SELECT cost_base_rub FROM printers_mfu WHERE id = :id"),
        {"id": sku_id},
    ).scalar()
    assert float(new_cb) == 21500.00
