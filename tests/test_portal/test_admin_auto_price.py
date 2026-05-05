# Тесты UI /admin/auto-price-loads (этап 12.3).

from __future__ import annotations

import pytest
from sqlalchemy import text

from tests.test_portal.conftest import extract_csrf


@pytest.fixture(autouse=True)
def _clean_auto_tables(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE "
            "  auto_price_load_runs, auto_price_loads, "
            "  unmapped_supplier_items, supplier_prices, price_uploads, "
            "  suppliers, "
            "  cpus, motherboards, rams, gpus, storages, cases, psus, coolers "
            "RESTART IDENTITY CASCADE"
        ))
        # seed как в миграции 028
        conn.execute(text(
            "INSERT INTO auto_price_loads (supplier_slug, enabled) VALUES "
            "  ('treolan', FALSE), ('ocs', FALSE), ('merlion', FALSE), "
            "  ('netlab', FALSE), ('resurs_media', FALSE), ('green_place', FALSE) "
            "ON CONFLICT (supplier_slug) DO NOTHING"
        ))
    yield


# ---- 1. Список из 6 поставщиков -------------------------------------

def test_list_renders_six_suppliers(admin_portal_client):
    r = admin_portal_client.get("/admin/auto-price-loads")
    assert r.status_code == 200
    for slug in ("treolan", "ocs", "merlion", "netlab", "resurs_media", "green_place"):
        assert f'data-testid="auto-row-{slug}"' in r.text


# ---- 2. anon → 302 на /login ----------------------------------------

def test_list_blocks_anonymous(portal_client):
    r = portal_client.get("/admin/auto-price-loads")
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


# ---- 3. manager → 403 -----------------------------------------------

def test_list_blocks_manager(manager_portal_client):
    r = manager_portal_client.get("/admin/auto-price-loads")
    assert r.status_code == 403


# ---- 4. POST /run без CSRF → 400 ------------------------------------

def test_run_requires_csrf(admin_portal_client):
    r = admin_portal_client.post(
        "/admin/auto-price-loads/treolan/run",
        data={"csrf_token": "wrong"},
    )
    assert r.status_code == 400


# ---- 5. POST /run для незарегистрированного slug → 400 --------------

def test_run_blocks_unregistered_fetcher(admin_portal_client):
    r0 = admin_portal_client.get("/admin/auto-price-loads")
    token = extract_csrf(r0.text)
    r = admin_portal_client.post(
        "/admin/auto-price-loads/ocs/run",
        data={"csrf_token": token},
    )
    # OCS пока без fetcher'а (12.1 будет), но slug валиден в seed —
    # ожидаем 400 «канал не подключён».
    assert r.status_code == 400


# ---- 6. 429 при too-frequent ---------------------------------------

def test_run_returns_429_on_too_frequent(admin_portal_client, db_session, monkeypatch):
    """Регистрируем фейк-fetcher для slug 'treolan' и проверяем, что
    второй вызов в окне 5 мин возвращает 429."""
    from app.services.auto_price import base as base_mod

    class FakeFetcher(base_mod.BaseAutoFetcher):
        supplier_slug = "fake_run_treolan"

        def fetch_and_save(self) -> int:
            from shared.db import SessionLocal
            session = SessionLocal()
            try:
                sup = session.execute(text(
                    "INSERT INTO suppliers (name, is_active) VALUES ('FakeT', TRUE) "
                    "ON CONFLICT (name) DO UPDATE SET is_active=TRUE RETURNING id"
                )).first()
                pu = session.execute(text(
                    "INSERT INTO price_uploads (supplier_id, filename, status, "
                    "  rows_total, rows_matched, rows_unmatched) "
                    "VALUES (:sid, 'fake.json', 'success', 1, 1, 0) RETURNING id"
                ), {"sid": sup.id}).first()
                session.commit()
                return int(pu.id)
            finally:
                session.close()

    base_mod._REGISTRY["fake_run_treolan"] = FakeFetcher
    db_session.execute(text(
        "INSERT INTO auto_price_loads (supplier_slug, enabled) "
        "VALUES ('fake_run_treolan', FALSE) "
        "ON CONFLICT (supplier_slug) DO NOTHING"
    ))
    db_session.commit()

    try:
        r0 = admin_portal_client.get("/admin/auto-price-loads")
        token = extract_csrf(r0.text)

        r1 = admin_portal_client.post(
            "/admin/auto-price-loads/fake_run_treolan/run",
            data={"csrf_token": token},
        )
        assert r1.status_code == 302, r1.text[:200]

        r2 = admin_portal_client.post(
            "/admin/auto-price-loads/fake_run_treolan/run",
            data={"csrf_token": token},
        )
        assert r2.status_code == 429
    finally:
        base_mod._REGISTRY.pop("fake_run_treolan", None)


# ---- 7. Toggle блокирует включение для slug без fetcher'а -----------

def test_toggle_blocks_enabling_unregistered_fetcher(admin_portal_client, db_session):
    """Пытаемся включить ocs — там нет fetcher'а на 12.3."""
    r0 = admin_portal_client.get("/admin/auto-price-loads")
    token = extract_csrf(r0.text)

    r = admin_portal_client.post(
        "/admin/auto-price-loads/ocs/toggle",
        data={"csrf_token": token},
    )
    assert r.status_code == 400

    # auto_price_loads.enabled остался FALSE.
    state = db_session.execute(text(
        "SELECT enabled FROM auto_price_loads WHERE supplier_slug = 'ocs'"
    )).first()
    assert state.enabled is False


# ---- 8. Toggle включает treolan (fetcher зарегистрирован) -----------

def test_toggle_enables_treolan(admin_portal_client, db_session):
    r0 = admin_portal_client.get("/admin/auto-price-loads")
    token = extract_csrf(r0.text)

    r = admin_portal_client.post(
        "/admin/auto-price-loads/treolan/toggle",
        data={"csrf_token": token},
    )
    assert r.status_code == 302

    state = db_session.execute(text(
        "SELECT enabled FROM auto_price_loads WHERE supplier_slug = 'treolan'"
    )).first()
    assert state.enabled is True


# ---- 9. Toggle переключает в обе стороны ----------------------------

def test_toggle_round_trip(admin_portal_client, db_session):
    r0 = admin_portal_client.get("/admin/auto-price-loads")
    token = extract_csrf(r0.text)

    admin_portal_client.post(
        "/admin/auto-price-loads/treolan/toggle",
        data={"csrf_token": token},
    )
    state1 = db_session.execute(text(
        "SELECT enabled FROM auto_price_loads WHERE supplier_slug = 'treolan'"
    )).first().enabled
    db_session.expire_all()

    admin_portal_client.post(
        "/admin/auto-price-loads/treolan/toggle",
        data={"csrf_token": token},
    )
    state2 = db_session.execute(text(
        "SELECT enabled FROM auto_price_loads WHERE supplier_slug = 'treolan'"
    )).first().enabled

    assert state1 is True
    assert state2 is False
