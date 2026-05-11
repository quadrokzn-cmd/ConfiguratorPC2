"""Тесты страницы /nomenclature (справочник принтеров/МФУ) — этап 9a слияния.

Проверяем:
- права (без auctions → 403, без auctions_edit_settings — нельзя править),
- GET 200 + рендер строк/фильтров,
- POST cost-base: валидно/невалидно,
- POST attrs: запись в БД,
- POST enrich: создаёт файл в enrichment/auctions/pending/.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from tests.test_portal.auctions_fixtures import (
    auctions_no_perm,        # noqa: F401
    auctions_viewer,         # noqa: F401
    auctions_editor,         # noqa: F401
    auctions_settings_editor,  # noqa: F401
    insert_printer_mfu,
    login_as,
)
from tests.test_portal.conftest import extract_csrf


# ---- Авторизация / права ----------------------------------------------

def test_nomenclature_anon_redirected(portal_client):
    r = portal_client.get("/nomenclature")
    assert r.status_code in (302, 303)


def test_nomenclature_no_perm_403(portal_client, auctions_no_perm):
    login_as(portal_client, auctions_no_perm)
    r = portal_client.get("/nomenclature")
    assert r.status_code == 403


def test_nomenclature_viewer_200_empty_state(portal_client, auctions_viewer):
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature")
    assert r.status_code == 200
    assert 'data-testid="empty-state"' in r.text


def test_nomenclature_viewer_200_with_rows(
    portal_client, auctions_viewer, db_session,
):
    insert_printer_mfu(db_session, sku="hp-test-01", brand="HP",
                       name="HP LaserJet тест", category="printer")
    insert_printer_mfu(db_session, sku="canon-test-02", brand="Canon",
                       name="Canon i-SENSYS тест", category="mfu")
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature")
    assert r.status_code == 200
    assert "hp-test-01" in r.text
    assert "canon-test-02" in r.text


def test_nomenclature_filter_brand(
    portal_client, auctions_viewer, db_session,
):
    insert_printer_mfu(db_session, sku="hp-1", brand="HP", name="HP Test")
    insert_printer_mfu(db_session, sku="canon-1", brand="Canon", name="Canon Test")
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature?brand=HP")
    assert r.status_code == 200
    assert "hp-1" in r.text
    assert "canon-1" not in r.text


# ---- POST cost-base ---------------------------------------------------

def test_cost_base_no_perm_403(
    portal_client, auctions_viewer, db_session,
):
    """Viewer не имеет _settings → 403 на cost-base."""
    sku_id = insert_printer_mfu(db_session)
    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/nomenclature")
    csrf = extract_csrf(r.text)
    r = portal_client.post(
        f"/nomenclature/{sku_id}/cost-base",
        data={"cost_base_rub": "1000", "csrf_token": csrf},
    )
    assert r.status_code == 403


def test_cost_base_save_valid(
    portal_client, auctions_settings_editor, db_session,
):
    sku_id = insert_printer_mfu(db_session, cost_base_rub=None)
    login_as(portal_client, auctions_settings_editor)
    r = portal_client.get("/nomenclature")
    csrf = extract_csrf(r.text)
    r = portal_client.post(
        f"/nomenclature/{sku_id}/cost-base",
        data={"cost_base_rub": "1234.56", "csrf_token": csrf},
    )
    assert r.status_code == 200, r.status_code
    body = r.json()
    assert body["ok"] is True
    val = db_session.execute(
        text("SELECT cost_base_rub FROM printers_mfu WHERE id = :id"),
        {"id": sku_id},
    ).scalar()
    assert float(val) == pytest.approx(1234.56)


def test_cost_base_invalid_value_400(
    portal_client, auctions_settings_editor, db_session,
):
    sku_id = insert_printer_mfu(db_session)
    login_as(portal_client, auctions_settings_editor)
    r = portal_client.get("/nomenclature")
    csrf = extract_csrf(r.text)
    r = portal_client.post(
        f"/nomenclature/{sku_id}/cost-base",
        data={"cost_base_rub": "not-a-number", "csrf_token": csrf},
    )
    assert r.status_code == 400


# ---- POST attrs -------------------------------------------------------

def test_attrs_save(
    portal_client, auctions_settings_editor, db_session,
):
    sku_id = insert_printer_mfu(db_session)
    login_as(portal_client, auctions_settings_editor)
    r = portal_client.get("/nomenclature")
    csrf = extract_csrf(r.text)

    r = portal_client.post(
        f"/nomenclature/{sku_id}/attrs",
        data={
            "csrf_token":              csrf,
            "print_speed_ppm":         "30",
            "colorness":               "ч/б",
            "max_format":              "A4",
            "duplex":                  "yes",
            "resolution_dpi":          "1200",
            "network_interface":       ["LAN", "WiFi"],
            "usb":                     "yes",
            "starter_cartridge_pages": "1500",
            "print_technology":        "лазерная",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    row = db_session.execute(
        text("SELECT attrs_jsonb, attrs_source FROM printers_mfu WHERE id = :id"),
        {"id": sku_id},
    ).first()
    attrs = row.attrs_jsonb
    assert attrs["print_speed_ppm"] == 30
    assert attrs["colorness"] == "ч/б"
    assert sorted(attrs["network_interface"]) == ["LAN", "WiFi"]
    assert row.attrs_source == "manual"


# ---- POST enrich ------------------------------------------------------

def test_enrich_creates_pending_file(
    portal_client, auctions_settings_editor, db_session, tmp_path, monkeypatch,
):
    """POST /enrich → файл создан в enrichment/auctions/pending/."""
    sku = "ENRICH-TEST-001"
    sku_id = insert_printer_mfu(db_session, sku=sku, brand="TestBrand")
    login_as(portal_client, auctions_settings_editor)

    # Подменяем корень enrichment на tmp_path, чтобы не засорять реальную папку.
    from portal.services.auctions.catalog.enrichment import exporter as exp_mod
    monkeypatch.setattr(exp_mod, "ENRICHMENT_ROOT", tmp_path)

    r = portal_client.get("/nomenclature")
    csrf = extract_csrf(r.text)
    r = portal_client.post(
        f"/nomenclature/{sku_id}/enrich",
        data={"csrf_token": csrf},
    )
    assert r.status_code == 200, r.text[:300]
    body = r.json()
    assert body["ok"] is True
    pending = tmp_path / "pending"
    assert pending.exists()
    files = list(pending.glob("*.json"))
    assert len(files) >= 1
