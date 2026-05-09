"""Тесты страницы /auctions/settings — этап 9a слияния QT↔C-PC2.

Проверяем:
- права (без auctions_edit_settings → 403),
- GET 200 + видны все поля,
- POST /save: обновление настроек,
- POST /region/{code}/toggle,
- POST /ktru/add и /ktru/{code}/toggle.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.test_portal.auctions_fixtures import (
    auctions_editor,         # noqa: F401
    auctions_settings_editor,  # noqa: F401
    auctions_viewer,         # noqa: F401
    login_as,
)
from tests.test_portal.conftest import extract_csrf


# ---- Права ------------------------------------------------------------

def test_settings_no_perm_403(portal_client, auctions_editor):
    """auctions + auctions_edit_status — недостаточно, нужен _settings."""
    login_as(portal_client, auctions_editor)
    r = portal_client.get("/auctions/settings")
    assert r.status_code == 403, r.status_code


def test_settings_admin_200(portal_client, admin_user):
    login_as(portal_client, admin_user)
    r = portal_client.get("/auctions/settings")
    assert r.status_code == 200
    # Проверяем все ключевые элементы.
    assert 'data-testid="set-margin-threshold-pct"' in r.text
    assert 'data-testid="set-nmck-min-rub"' in r.text
    assert 'data-testid="set-deadline-alert-hours"' in r.text
    assert 'data-testid="regions-section"' in r.text
    assert 'data-testid="ktru-section"' in r.text


# ---- POST /settings/save ---------------------------------------------

def test_settings_save_margin_threshold(
    portal_client, auctions_settings_editor, db_session,
):
    login_as(portal_client, auctions_settings_editor)
    r = portal_client.get("/auctions/settings")
    csrf = extract_csrf(r.text)

    r = portal_client.post(
        "/auctions/settings/save",
        data={
            "csrf_token":               csrf,
            "margin_threshold_pct":     "20",
            "nmck_min_rub":             "30000",
            "max_price_per_unit_rub":   "300000",
            "deadline_alert_hours":     "24",
            "contract_reminder_days":   "3",
            "auctions_ingest_enabled":  "on",
        },
    )
    assert r.status_code in (302, 303), r.status_code
    val = db_session.execute(
        text("SELECT value FROM settings WHERE key = 'margin_threshold_pct'"),
    ).scalar()
    assert val == "20"


def test_settings_save_csrf_invalid_400(
    portal_client, auctions_settings_editor,
):
    login_as(portal_client, auctions_settings_editor)
    r = portal_client.post(
        "/auctions/settings/save",
        data={"csrf_token": "wrong", "margin_threshold_pct": "99"},
    )
    assert r.status_code == 400


# ---- POST /settings/region/{code}/toggle -----------------------------

def test_region_toggle(
    portal_client, auctions_settings_editor, db_session,
):
    login_as(portal_client, auctions_settings_editor)
    r = portal_client.get("/auctions/settings")
    csrf = extract_csrf(r.text)

    # До: yakutia.excluded = TRUE (seed/conftest reset).
    before = db_session.execute(
        text("SELECT excluded FROM excluded_regions WHERE region_code='yakutia'"),
    ).scalar()
    assert before is True

    r = portal_client.post(
        "/auctions/settings/region/yakutia/toggle",
        data={"csrf_token": csrf},
    )
    assert r.status_code in (302, 303), r.status_code

    after = db_session.execute(
        text("SELECT excluded FROM excluded_regions WHERE region_code='yakutia'"),
    ).scalar()
    assert after is False  # инвертировано


def test_region_toggle_unknown_404(
    portal_client, auctions_settings_editor,
):
    login_as(portal_client, auctions_settings_editor)
    r = portal_client.get("/auctions/settings")
    csrf = extract_csrf(r.text)
    r = portal_client.post(
        "/auctions/settings/region/no-such-region/toggle",
        data={"csrf_token": csrf},
    )
    assert r.status_code == 404


# ---- POST /settings/ktru/add и /toggle -------------------------------

def test_ktru_add(
    portal_client, auctions_settings_editor, db_session,
):
    login_as(portal_client, auctions_settings_editor)
    r = portal_client.get("/auctions/settings")
    csrf = extract_csrf(r.text)

    r = portal_client.post(
        "/auctions/settings/ktru/add",
        data={
            "csrf_token":   csrf,
            "code":         "26.20.16.120-99999999",
            "display_name": "Тест-принтер",
        },
    )
    assert r.status_code in (302, 303), r.status_code
    row = db_session.execute(
        text(
            "SELECT display_name, is_active "
            "FROM ktru_watchlist WHERE code = '26.20.16.120-99999999'"
        ),
    ).first()
    assert row is not None
    assert row.display_name == "Тест-принтер"
    assert row.is_active is True


def test_ktru_toggle(
    portal_client, auctions_settings_editor, db_session,
):
    login_as(portal_client, auctions_settings_editor)
    r = portal_client.get("/auctions/settings")
    csrf = extract_csrf(r.text)

    # Зонтик МФУ активен.
    code = "26.20.18.000-00000001"
    before = db_session.execute(
        text("SELECT is_active FROM ktru_watchlist WHERE code = :c"),
        {"c": code},
    ).scalar()
    assert before is True

    r = portal_client.post(
        f"/auctions/settings/ktru/{code}/toggle",
        data={"csrf_token": csrf},
    )
    assert r.status_code in (302, 303), r.status_code
    after = db_session.execute(
        text("SELECT is_active FROM ktru_watchlist WHERE code = :c"),
        {"c": code},
    ).scalar()
    assert after is False
