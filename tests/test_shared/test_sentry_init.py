# Тесты shared/sentry_init.py (этап 9В.3).
#
# Цель: убедиться, что:
#   - без SENTRY_DSN init молча выключается;
#   - с DSN — sentry_sdk.init вызывается с правильными аргументами;
#   - before_send корректно фильтрует 4xx и CancelledError;
#   - mask_dsn прячет публичную часть DSN.
#
# Требование: тесты НЕ должны реально слать события в Sentry. Реальный
# init с тестовым DSN мы не делаем — везде мокаем sentry_sdk.init.

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from shared.sentry_init import (
    _make_before_send,
    _resolve_dsn,
    init_sentry,
    mask_dsn,
)


# --- mask_dsn -----------------------------------------------------------

def test_mask_dsn_hides_public_key():
    dsn = "https://abcdef1234567890@o123.ingest.sentry.io/456"
    assert mask_dsn(dsn) == "https://****@o123.ingest.sentry.io/456"


def test_mask_dsn_handles_empty_string():
    assert mask_dsn("") == ""


def test_mask_dsn_keeps_string_without_at_sign():
    # Если на вход прилетела не-DSN строка — не падаем, отдаём как есть.
    assert mask_dsn("not-a-dsn") == "not-a-dsn"


# --- _resolve_dsn -------------------------------------------------------

def test_resolve_dsn_prefers_per_service(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://generic@example.com/0")
    monkeypatch.setenv("SENTRY_DSN_PORTAL", "https://portal@example.com/1")
    assert _resolve_dsn("portal") == "https://portal@example.com/1"


def test_resolve_dsn_falls_back_to_generic(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN_PORTAL", raising=False)
    monkeypatch.setenv("SENTRY_DSN", "https://generic@example.com/0")
    assert _resolve_dsn("portal") == "https://generic@example.com/0"


def test_resolve_dsn_returns_empty_when_none_set(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_DSN_PORTAL", raising=False)
    monkeypatch.delenv("SENTRY_DSN_CONFIGURATOR", raising=False)
    assert _resolve_dsn("portal") == ""


# --- init_sentry --------------------------------------------------------

def test_init_sentry_returns_false_when_dsn_missing(monkeypatch):
    """Без SENTRY_DSN init молча выключается."""
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.delenv("SENTRY_DSN_PORTAL", raising=False)
    monkeypatch.delenv("SENTRY_DSN_CONFIGURATOR", raising=False)
    assert init_sentry("portal") is False


def test_init_sentry_returns_true_when_dsn_set(monkeypatch):
    """С DSN init вызывает sentry_sdk.init и возвращает True."""
    import sentry_sdk

    monkeypatch.setenv("SENTRY_DSN_PORTAL", "https://test@localhost.invalid/0")
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    captured: dict = {}

    def fake_init(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(sentry_sdk, "init", fake_init)
    monkeypatch.setattr(sentry_sdk, "set_tag", lambda *a, **k: None)

    result = init_sentry("portal")

    assert result is True
    assert captured["dsn"] == "https://test@localhost.invalid/0"
    assert captured["server_name"] == "portal"
    assert captured["send_default_pii"] is False
    # Sample rate не должен быть выше 0.1 (см. ОГРАНИЧЕНИЯ этапа).
    assert captured["traces_sample_rate"] <= 0.1


def test_init_sentry_rejects_unknown_service(monkeypatch):
    monkeypatch.setenv("SENTRY_DSN", "https://x@example.com/1")
    with pytest.raises(ValueError):
        init_sentry("unknown")


# --- before_send --------------------------------------------------------

def _run_before_send(exc: BaseException) -> dict | None:
    bs = _make_before_send("portal")
    event = {"message": "stub"}
    hint = {"exc_info": (type(exc), exc, None)}
    return bs(event, hint)


def test_before_send_drops_4xx_http_exceptions():
    # 404 — пользовательская ошибка, в Sentry не нужна.
    assert _run_before_send(HTTPException(status_code=404, detail="Not found")) is None


def test_before_send_drops_401_403():
    assert _run_before_send(HTTPException(status_code=401)) is None
    assert _run_before_send(HTTPException(status_code=403)) is None


def test_before_send_keeps_5xx_http_exceptions():
    event = _run_before_send(HTTPException(status_code=500, detail="Boom"))
    assert event is not None and event.get("message") == "stub"


def test_before_send_keeps_runtime_error():
    event = _run_before_send(RuntimeError("non-http error"))
    assert event is not None and event.get("message") == "stub"


def test_before_send_drops_cancelled_error():
    assert _run_before_send(asyncio.CancelledError()) is None


def test_before_send_keeps_event_without_exc_info():
    bs = _make_before_send("portal")
    event = {"message": "log entry"}
    assert bs(event, {}) is event
