# Тесты healthcheck-эндпоинта /healthz (этап 10.1).
#
# Эндпоинт без авторизации, дёргает SELECT 1 в БД. Должен возвращать:
#   - 200 / {"status": "ok", "db": "ok"} при живой БД;
#   - 503 / {"status": "error", "db": "error"} при упавшей сессии.

from __future__ import annotations

from unittest.mock import patch


def test_healthz_returns_ok_when_db_alive(app_client):
    """Живая тестовая БД (поднята фикстурой db_engine из conftest)."""
    r = app_client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body == {"status": "ok", "db": "ok"}


def test_healthz_returns_503_when_db_unavailable(app_client):
    """Если SessionLocal() возвращает сессию, на которой execute падает —
    эндпоинт обязан отдать 503, а не уронить весь процесс."""
    class _BrokenSession:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("database is down")

        def close(self) -> None:
            pass

    with patch("app.main.SessionLocal", return_value=_BrokenSession()):
        r = app_client.get("/healthz")
    assert r.status_code == 503
    body = r.json()
    assert body == {"status": "error", "db": "error"}


def test_healthz_does_not_require_auth(app_client):
    """Healthcheck должен быть доступен без сессии — Railway дёргает
    его до того, как пользователь залогинится."""
    # app_client из conftest специально без логина.
    r = app_client.get("/healthz")
    # Не 401/403/302 — должен быть 200 (БД живая) или 503 (БД мертва).
    assert r.status_code in (200, 503)
