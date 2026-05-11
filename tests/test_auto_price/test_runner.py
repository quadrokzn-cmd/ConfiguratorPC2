# Тесты runner'а автозагрузок (этап 12.3).

from __future__ import annotations

import pytest
from sqlalchemy import text


# ---- helper: регистрируем фейковый fetcher ----------------------------

def _register_fake_fetcher(monkeypatch, *, slug="fake_supplier", behavior="success"):
    """behavior:
        'success' — fetch_and_save возвращает price_upload_id (нужно
                    предварительно вставить запись в price_uploads).
        'error'   — бросает RuntimeError("boom").
    """
    from portal.services.configurator.auto_price import base as base_mod

    class FakeFetcher(base_mod.BaseAutoFetcher):
        supplier_slug = slug

        def fetch_and_save(self) -> int:
            if behavior == "error":
                raise RuntimeError("boom")
            # success: создаём минимальную запись в suppliers + price_uploads
            # и возвращаем её id.
            from shared.db import SessionLocal
            session = SessionLocal()
            try:
                sup = session.execute(
                    text(
                        "INSERT INTO suppliers (name, is_active) VALUES (:n, TRUE) "
                        "ON CONFLICT (name) DO UPDATE SET is_active=TRUE "
                        "RETURNING id"
                    ),
                    {"n": "FakeSupplier"},
                ).first()
                pu = session.execute(
                    text(
                        "INSERT INTO price_uploads "
                        "  (supplier_id, filename, status, rows_total, rows_matched, rows_unmatched) "
                        "VALUES (:sid, 'fake.json', 'success', 1, 1, 0) "
                        "RETURNING id"
                    ),
                    {"sid": sup.id},
                ).first()
                session.commit()
                return int(pu.id)
            finally:
                session.close()

    # Регистрируем напрямую в реестр (bypass register_fetcher для
    # повторного использования slug между тестами).
    base_mod._REGISTRY[slug] = FakeFetcher

    def _cleanup():
        base_mod._REGISTRY.pop(slug, None)

    return _cleanup


# ---- 1. Успех: success-row + run-row ----------------------------------

def test_run_marks_success_and_creates_run_row(db_session, monkeypatch):
    from portal.services.configurator.auto_price.runner import run_auto_load

    cleanup = _register_fake_fetcher(monkeypatch, slug="fake_supplier", behavior="success")
    try:
        result = run_auto_load("fake_supplier", triggered_by="manual")
    finally:
        cleanup()

    assert result["status"] == "success"
    assert result["price_upload_id"] is not None

    state = db_session.execute(text(
        "SELECT status, last_success_at, last_error_at, last_price_upload_id, "
        "       last_error_message "
        "FROM auto_price_loads WHERE supplier_slug = 'fake_supplier'"
    )).first()
    assert state is not None
    assert state.status == "success"
    assert state.last_success_at is not None
    assert state.last_error_at is None
    assert state.last_error_message is None
    assert state.last_price_upload_id == result["price_upload_id"]

    runs = db_session.execute(text(
        "SELECT status, triggered_by, finished_at, price_upload_id "
        "FROM auto_price_load_runs WHERE supplier_slug = 'fake_supplier'"
    )).all()
    assert len(runs) == 1
    assert runs[0].status == "success"
    assert runs[0].triggered_by == "manual"
    assert runs[0].finished_at is not None
    assert runs[0].price_upload_id == result["price_upload_id"]


# ---- 2. Ошибка: error-row + Sentry-capture ---------------------------

def test_run_marks_error_and_propagates_to_sentry(db_session, monkeypatch):
    from portal.services.configurator.auto_price.runner import run_auto_load

    captured = []

    class FakeSentry:
        def capture_exception(self, exc):
            captured.append(exc)

    fake_sentry = FakeSentry()
    import sys
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)

    cleanup = _register_fake_fetcher(monkeypatch, slug="fake_err", behavior="error")
    try:
        with pytest.raises(RuntimeError, match="boom"):
            run_auto_load("fake_err", triggered_by="scheduled")
    finally:
        cleanup()

    assert len(captured) == 1
    assert isinstance(captured[0], RuntimeError)

    state = db_session.execute(text(
        "SELECT status, last_error_message FROM auto_price_loads "
        "WHERE supplier_slug = 'fake_err'"
    )).first()
    assert state.status == "error"
    assert "boom" in (state.last_error_message or "")

    run = db_session.execute(text(
        "SELECT status, error_message FROM auto_price_load_runs "
        "WHERE supplier_slug = 'fake_err'"
    )).first()
    assert run.status == "error"
    assert "boom" in (run.error_message or "")


# ---- 3. Throttle для manual ------------------------------------------

def test_run_blocks_manual_within_5min_window(db_session, monkeypatch):
    from portal.services.configurator.auto_price.runner import (
        TooFrequentRunError, run_auto_load,
    )

    cleanup = _register_fake_fetcher(monkeypatch, slug="fake_throttle", behavior="success")
    try:
        run_auto_load("fake_throttle", triggered_by="manual")
        with pytest.raises(TooFrequentRunError):
            run_auto_load("fake_throttle", triggered_by="manual")
    finally:
        cleanup()


# ---- 4. Throttle НЕ применяется к scheduled --------------------------

def test_run_allows_scheduled_within_5min_window(db_session, monkeypatch):
    from portal.services.configurator.auto_price.runner import run_auto_load

    cleanup = _register_fake_fetcher(monkeypatch, slug="fake_sched", behavior="success")
    try:
        run_auto_load("fake_sched", triggered_by="manual")
        # Сразу же — scheduled, должно пройти.
        result = run_auto_load("fake_sched", triggered_by="scheduled")
    finally:
        cleanup()

    assert result["status"] == "success"


# ---- 5. ValueError для незарегистрированного slug --------------------

def test_run_raises_for_unknown_slug():
    from portal.services.configurator.auto_price.runner import run_auto_load

    with pytest.raises(ValueError, match="fetcher"):
        run_auto_load("__unknown__", triggered_by="manual")


# ---- 6. NoNewDataException → status='no_new_data', orchestrator не зван --

def test_runner_handles_no_new_data_exception(db_session, monkeypatch):
    """Если IMAP-fetcher бросил NoNewDataException — runner помечает run
    'no_new_data', НЕ вызывает orchestrator/save_price_rows и НЕ создаёт
    запись в price_uploads. Это ключевая защита от обнуления остатков.
    """
    from portal.services.configurator.auto_price import base as base_mod
    from portal.services.configurator.auto_price.runner import run_auto_load
    from portal.services.configurator.auto_price.fetchers.base_imap import NoNewDataException

    fetch_calls = {"n": 0}

    class FakeImapFetcher(base_mod.BaseAutoFetcher):
        supplier_slug = "fake_imap_empty"

        def fetch_and_save(self) -> int:
            fetch_calls["n"] += 1
            raise NoNewDataException("нет новых писем за 14 дней")

    base_mod._REGISTRY["fake_imap_empty"] = FakeImapFetcher
    try:
        result = run_auto_load("fake_imap_empty", triggered_by="manual")
    finally:
        base_mod._REGISTRY.pop("fake_imap_empty", None)

    assert fetch_calls["n"] == 1
    assert result["status"] == "no_new_data"
    assert "нет новых писем" in result["message"]
    assert result["price_upload_id"] is None

    state = db_session.execute(text(
        "SELECT status, last_success_at, last_error_at, last_error_message, "
        "       last_run_at "
        "FROM auto_price_loads WHERE supplier_slug = 'fake_imap_empty'"
    )).first()
    assert state is not None
    assert state.status == "no_new_data"
    # last_run_at обновлён (попытка была), last_success/error_at не тронуты,
    # last_error_message очищен (это не ошибка).
    assert state.last_run_at is not None
    assert state.last_success_at is None
    assert state.last_error_at is None
    assert state.last_error_message is None

    runs = db_session.execute(text(
        "SELECT status, error_message, source_ref, finished_at "
        "FROM auto_price_load_runs WHERE supplier_slug = 'fake_imap_empty'"
    )).all()
    assert len(runs) == 1
    assert runs[0].status == "no_new_data"
    assert "нет новых писем" in (runs[0].error_message or "")
    assert runs[0].source_ref is None
    assert runs[0].finished_at is not None

    # Самое важное: orchestrator НЕ был вызван — запись price_uploads
    # не создалась.
    pu_count = db_session.execute(text(
        "SELECT COUNT(*) FROM price_uploads"
    )).scalar()
    assert pu_count == 0


# ---- 7. source_ref пишется при success для IMAP-fetcher'а -------------

def test_runner_records_source_ref_on_imap_success(db_session, monkeypatch):
    """IMAP-fetcher после успешной обработки кладёт Message-ID в
    last_processed_message_id. Runner должен записать его в source_ref
    строки auto_price_load_runs."""
    from portal.services.configurator.auto_price import base as base_mod
    from portal.services.configurator.auto_price.runner import run_auto_load

    class FakeImapFetcher(base_mod.BaseAutoFetcher):
        supplier_slug = "fake_imap_ok"

        def __init__(self):
            self.last_processed_message_id = "<msg-12345@example.com>"

        def fetch_and_save(self) -> int:
            from shared.db import SessionLocal
            session = SessionLocal()
            try:
                sup = session.execute(text(
                    "INSERT INTO suppliers (name, is_active) VALUES ('FakeImap', TRUE) "
                    "ON CONFLICT (name) DO UPDATE SET is_active=TRUE RETURNING id"
                )).first()
                pu = session.execute(text(
                    "INSERT INTO price_uploads (supplier_id, filename, status, "
                    "  rows_total, rows_matched, rows_unmatched) "
                    "VALUES (:sid, 'fake.xlsx', 'success', 1, 1, 0) RETURNING id"
                ), {"sid": sup.id}).first()
                session.commit()
                return int(pu.id)
            finally:
                session.close()

    base_mod._REGISTRY["fake_imap_ok"] = FakeImapFetcher
    try:
        result = run_auto_load("fake_imap_ok", triggered_by="scheduled")
    finally:
        base_mod._REGISTRY.pop("fake_imap_ok", None)

    assert result["status"] == "success"

    run = db_session.execute(text(
        "SELECT source_ref FROM auto_price_load_runs "
        "WHERE supplier_slug = 'fake_imap_ok'"
    )).first()
    assert run is not None
    assert run.source_ref == "<msg-12345@example.com>"
