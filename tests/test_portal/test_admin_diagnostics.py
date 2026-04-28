# Тесты /admin/sentry-test и /admin/sentry-message (этап 9В.3).
#
# Все запросы — без реального Sentry-DSN (TestClient запускается в окружении
# где SENTRY_DSN не задан, init_sentry в portal/main.py возвращает False
# при импорте модуля). Sentry-функции мокаются прямо на модуле sentry_sdk
# через monkeypatch, чтобы проверить, что роутер действительно их зовёт.

from __future__ import annotations

import pytest


def test_sentry_test_endpoint_requires_admin_anonymous(portal_client):
    """Аноним → 302 на /login (require_admin → require_login → redirect)."""
    r = portal_client.get("/admin/sentry-test")
    assert r.status_code in (302, 303)
    assert "/login" in r.headers["location"]


def test_sentry_test_endpoint_forbids_manager(manager_portal_client):
    """Менеджер → 403 (require_admin)."""
    r = manager_portal_client.get("/admin/sentry-test")
    assert r.status_code == 403


def test_sentry_test_endpoint_raises_500_for_admin(admin_portal_client):
    """Админ → 500 (RuntimeError проброшен наружу).

    TestClient по умолчанию пробрасывает исключение, чтобы тесты ловили
    его явно. Поэтому ожидаем pytest.raises, а не r.status_code == 500.
    """
    with pytest.raises(RuntimeError) as excinfo:
        admin_portal_client.get("/admin/sentry-test")
    assert "Sentry test exception" in str(excinfo.value)


def test_sentry_message_endpoint_requires_admin_anonymous(portal_client):
    r = portal_client.get("/admin/sentry-message")
    assert r.status_code in (302, 303)


def test_sentry_message_endpoint_forbids_manager(manager_portal_client):
    r = manager_portal_client.get("/admin/sentry-message")
    assert r.status_code == 403


def test_sentry_message_endpoint_calls_capture_message(
    admin_portal_client, monkeypatch
):
    """Админ → capture_message с правильными аргументами и {status: sent}."""
    import sentry_sdk

    captured: list[tuple] = []

    def fake_capture_message(message, level=None, **kwargs):
        captured.append((message, level))

    monkeypatch.setattr(sentry_sdk, "capture_message", fake_capture_message)
    # Внутри роутера sentry_sdk импортируется на верхнем уровне, поэтому
    # подмену нужно делать ещё и на этом модуле.
    from portal.routers import admin_diagnostics
    monkeypatch.setattr(
        admin_diagnostics.sentry_sdk, "capture_message", fake_capture_message
    )

    r = admin_portal_client.get("/admin/sentry-message")
    assert r.status_code == 200
    assert r.json() == {"status": "sent"}
    assert captured == [("Sentry test message", "info")]
