# Тесты shared/audit.py: write_audit + extract_request_meta (Этап 9В.4).
#
# Структурно повторяем подход test_sentry_init.py: тесты unit-стиля,
# с monkeypatch'ем там, где надо. Для write_audit — реальную БД не
# дёргаем (используем AUDIT_DISABLED=1 / monkeypatch engine).

from __future__ import annotations

import json
import logging

import pytest
from sqlalchemy import text

from shared import audit


# --- write_audit -------------------------------------------------------

def test_write_audit_inserts_row(db_engine, db_session):
    """Базовый кейс: write_audit пишет ровно одну строку."""
    audit.write_audit(
        action="test.action",
        service="portal",
        user_id=None,
        user_login="anon",
        target_type="thing",
        target_id=42,
        payload={"foo": "bar"},
        ip="1.2.3.4",
        user_agent="UA-test",
    )

    rows = db_session.execute(
        text(
            "SELECT action, service, user_login, target_type, target_id, "
            "       payload, host(ip) AS ip, user_agent "
            "FROM audit_log ORDER BY id DESC LIMIT 1"
        )
    ).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.action == "test.action"
    assert r.service == "portal"
    assert r.user_login == "anon"
    assert r.target_type == "thing"
    # target_id хранится как TEXT — int конвертируется в строку.
    assert r.target_id == "42"
    payload = r.payload if isinstance(r.payload, dict) else json.loads(r.payload)
    assert payload == {"foo": "bar"}
    assert r.ip == "1.2.3.4"
    assert r.user_agent == "UA-test"


def test_write_audit_serializes_payload_to_jsonb(db_engine, db_session):
    """payload — словарь с разными типами; в БД он лежит как JSONB."""
    audit.write_audit(
        action="test.payload",
        service="configurator",
        payload={"name": "Проект 1", "count": 7, "list": [1, 2]},
    )
    row = db_session.execute(
        text("SELECT payload FROM audit_log ORDER BY id DESC LIMIT 1")
    ).first()
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    assert payload == {"name": "Проект 1", "count": 7, "list": [1, 2]}


def test_write_audit_swallows_db_errors(monkeypatch, caplog):
    """Если engine.begin() кидает — write_audit ловит и пишет WARNING."""
    class _BoomEngine:
        def begin(self):
            raise RuntimeError("DB unavailable")

    monkeypatch.setattr("shared.db.engine", _BoomEngine())

    with caplog.at_level(logging.WARNING, logger="shared.audit"):
        # Не должно бросить наружу.
        audit.write_audit(action="test.boom", service="portal")
    assert any(
        "не удалось записать" in rec.message
        for rec in caplog.records
    )


def test_write_audit_disabled_via_env(monkeypatch, db_session):
    """AUDIT_DISABLED=1 → ничего не пишет, даже engine не дёргается."""
    monkeypatch.setenv("AUDIT_DISABLED", "1")

    audit.write_audit(action="test.disabled", service="portal", payload={"x": 1})

    n = db_session.execute(
        text("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'test.disabled'")
    ).scalar() or 0
    assert int(n) == 0


def test_write_audit_target_id_string_passthrough(db_engine, db_session):
    """target_id: строка передаётся как есть, без изменения."""
    audit.write_audit(
        action="test.str_target",
        service="portal",
        target_type="custom",
        target_id="abc-123",
    )
    row = db_session.execute(
        text("SELECT target_id FROM audit_log ORDER BY id DESC LIMIT 1")
    ).first()
    assert row.target_id == "abc-123"


# --- extract_request_meta ----------------------------------------------

class _FakeClient:
    def __init__(self, host: str | None):
        self.host = host


class _FakeRequest:
    """Минимальный mock fastapi.Request — нам нужны только headers и client."""

    def __init__(self, *, headers: dict[str, str], client_host: str | None):
        self.headers = headers
        self.client = _FakeClient(client_host) if client_host else None


def test_extract_request_meta_uses_x_forwarded_for():
    """X-Forwarded-For: client, p1, p2 → берём первый IP."""
    req = _FakeRequest(
        headers={
            "x-forwarded-for": "203.0.113.10, 10.0.0.1, 10.0.0.2",
            "user-agent":      "Mozilla/5.0",
        },
        client_host="10.0.0.99",
    )
    ip, ua = audit.extract_request_meta(req)
    assert ip == "203.0.113.10"
    assert ua == "Mozilla/5.0"


def test_extract_request_meta_falls_back_to_client_host():
    """Без X-Forwarded-For — берём client.host."""
    req = _FakeRequest(
        headers={"user-agent": "TestAgent"},
        client_host="192.168.1.5",
    )
    ip, _ = audit.extract_request_meta(req)
    assert ip == "192.168.1.5"


def test_extract_request_meta_handles_missing_headers():
    """Без UA и без client — оба значения None."""
    req = _FakeRequest(headers={}, client_host=None)
    ip, ua = audit.extract_request_meta(req)
    assert ip is None
    assert ua is None


def test_extract_request_meta_xff_empty_falls_back():
    """XFF присутствует, но пустой — fallback на client.host."""
    req = _FakeRequest(
        headers={"x-forwarded-for": "  ", "user-agent": "X"},
        client_host="10.0.0.7",
    )
    ip, _ = audit.extract_request_meta(req)
    assert ip == "10.0.0.7"


def test_user_agent_truncated_to_500(db_engine, db_session):
    """Длинный UA обрезается до 500 символов в записи в БД."""
    long_ua = "A" * 1200
    audit.write_audit(
        action="test.long_ua",
        service="portal",
        user_agent=long_ua,
    )
    row = db_session.execute(
        text(
            "SELECT user_agent FROM audit_log "
            "WHERE action = 'test.long_ua' ORDER BY id DESC LIMIT 1"
        )
    ).first()
    assert row is not None
    assert len(row.user_agent) == 500
    assert row.user_agent == "A" * 500


# --- Конфтест-зависимости ---------------------------------------------
#
# Тесты используют db_engine/db_session фикстуры из tests/test_portal/
# conftest.py — этот файл живёт в tests/test_shared/, у которого своих
# фикстур нет. Чтобы не дублировать setup, локальный conftest перенаправляет
# на портальный (через импорт фикстур).
