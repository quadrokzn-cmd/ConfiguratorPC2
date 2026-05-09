"""Тесты эндпоинтов /admin/run-ingest{,-blocking} (этап 8/9 слияния).

Проверяем:
- авторизация (anon → 302/403 в зависимости от настроек роутера),
- права (manager без `auctions_edit_settings` → 403),
- single-flight (если ingest_lock занят — 409),
- успешный путь (run_ingest_once замокан, чтобы не лезть к zakupki).
"""
from __future__ import annotations

import threading

import pytest


@pytest.fixture()
def admin_with_auctions_perms(db_session):
    """Админ имеет все права по определению (`has_permission` для admin → True)."""
    from tests.test_portal.conftest import _create_user
    uid = _create_user(
        db_session,
        login="admin_auc",
        password="admin-pass",
        role="admin",
        name="Админ аукционов",
    )
    return {"id": uid, "login": "admin_auc", "password": "admin-pass"}


@pytest.fixture()
def manager_with_auctions_settings(db_session):
    """Менеджер с правом auctions_edit_settings."""
    from tests.test_portal.conftest import _create_user
    uid = _create_user(
        db_session,
        login="mgr_auc_set",
        password="manager-pass",
        role="manager",
        name="Менеджер с настройками аукционов",
        permissions={"auctions_edit_settings": True},
    )
    return {"id": uid, "login": "mgr_auc_set", "password": "manager-pass"}


@pytest.fixture()
def admin_portal_client_auc(portal_client, admin_with_auctions_perms):
    from tests.test_portal.conftest import _login_via_portal
    _login_via_portal(
        portal_client,
        admin_with_auctions_perms["login"],
        admin_with_auctions_perms["password"],
    )
    return portal_client


@pytest.fixture()
def manager_portal_client_auc(portal_client, manager_with_auctions_settings):
    from tests.test_portal.conftest import _login_via_portal
    _login_via_portal(
        portal_client,
        manager_with_auctions_settings["login"],
        manager_with_auctions_settings["password"],
    )
    return portal_client


# ---- Авторизация / права -----------------------------------------------

def test_run_ingest_anon_redirected_to_login(portal_client):
    """Без сессии — редирект на /login (LoginRequiredRedirect)."""
    r = portal_client.post("/admin/run-ingest")
    assert r.status_code in (302, 303), r.status_code
    assert "/login" in r.headers.get("location", "")


def test_run_ingest_blocking_anon_redirected_to_login(portal_client):
    r = portal_client.post("/admin/run-ingest-blocking")
    assert r.status_code in (302, 303), r.status_code
    assert "/login" in r.headers.get("location", "")


def test_run_ingest_manager_without_perm_403(portal_client, manager_user_no_perms):
    """Менеджер без `auctions_edit_settings` → 403."""
    from tests.test_portal.conftest import _login_via_portal
    _login_via_portal(
        portal_client,
        manager_user_no_perms["login"],
        manager_user_no_perms["password"],
    )
    r = portal_client.post("/admin/run-ingest")
    assert r.status_code == 403, r.status_code


# ---- Single-flight (общая блокировка) -----------------------------------

def test_run_ingest_busy_returns_409(admin_portal_client_auc):
    """Если ingest_lock занят (например, scheduler сейчас крутится) —
    /admin/run-ingest отвечает 409."""
    from app.services.auctions.ingest.single_flight import ingest_lock
    assert ingest_lock.acquire(blocking=False)
    try:
        r = admin_portal_client_auc.post("/admin/run-ingest")
        assert r.status_code == 409, r.status_code
    finally:
        ingest_lock.release()


def test_run_ingest_blocking_busy_returns_409(admin_portal_client_auc):
    from app.services.auctions.ingest.single_flight import ingest_lock
    assert ingest_lock.acquire(blocking=False)
    try:
        r = admin_portal_client_auc.post("/admin/run-ingest-blocking")
        assert r.status_code == 409, r.status_code
    finally:
        ingest_lock.release()


# ---- Успешные сценарии (run_ingest_once замокан) -----------------------

def _stub_stats():
    """Минимальный заглушечный IngestStats без сетевых вызовов."""
    from app.services.auctions.ingest.orchestrator import IngestStats
    s = IngestStats()
    s.cards_seen = 0
    s.cards_parsed = 0
    s.cards_failed = 0
    s.inserted = 0
    s.updated = 0
    return s


def test_run_ingest_async_admin_returns_started(monkeypatch, admin_portal_client_auc):
    """Хеппи-путь: admin запускает ingest, получает status='started'.
    run_ingest_once мокаем — фоновый поток просто отдаст stub-stats и завершится."""
    monkeypatch.setattr(
        "app.services.auctions.ingest.orchestrator.run_ingest_once",
        lambda engine: _stub_stats(),
    )
    r = admin_portal_client_auc.post("/admin/run-ingest")
    assert r.status_code == 200, r.status_code
    body = r.json()
    assert body == {"status": "started"}

    # Дожидаемся, пока daemon-поток отпустит lock — на slow-CI бывает нужно.
    from app.services.auctions.ingest.single_flight import ingest_lock
    deadline_releases = 0
    while ingest_lock.locked() and deadline_releases < 50:
        threading.Event().wait(0.05)
        deadline_releases += 1


def test_run_ingest_blocking_admin_returns_stats(monkeypatch, admin_portal_client_auc):
    """admin синхронный запуск — IngestStats.as_dict в JSON-теле."""
    monkeypatch.setattr(
        "app.services.auctions.ingest.orchestrator.run_ingest_once",
        lambda engine: _stub_stats(),
    )
    r = admin_portal_client_auc.post("/admin/run-ingest-blocking")
    assert r.status_code == 200, r.status_code
    body = r.json()
    # IngestStats.as_dict содержит эти ключи (см. orchestrator.py).
    for k in (
        "cards_seen", "cards_parsed", "cards_failed",
        "inserted", "updated",
        "flagged_excluded_region", "flagged_below_nmck",
        "flagged_over_unit_price", "flagged_no_watchlist_ktru",
        "flagged_no_positions", "ktru_codes_used",
    ):
        assert k in body, k


def test_run_ingest_blocking_manager_with_perm_succeeds(
    monkeypatch, manager_portal_client_auc,
):
    """Менеджер с auctions_edit_settings тоже может запускать (не только админ)."""
    monkeypatch.setattr(
        "app.services.auctions.ingest.orchestrator.run_ingest_once",
        lambda engine: _stub_stats(),
    )
    r = manager_portal_client_auc.post("/admin/run-ingest-blocking")
    assert r.status_code == 200, r.status_code
