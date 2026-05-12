"""Тесты shared/config.py — fallback DATABASE_URL → DATABASE_PUBLIC_URL.

Контекст: Railway раздаёт две переменные — DATABASE_URL (внутренний host)
и DATABASE_PUBLIC_URL (внешний proxy host). В dev-env-файлах для подключения
снаружи к prod-БД пишется DATABASE_PUBLIC_URL; fallback позволяет
shared.config работать с такими файлами без переименования переменной.

Backlog #17, см. plans/2026-04-23-platforma-i-aukciony.md (мини-этап
2026-05-13 fallback DATABASE_URL / DATABASE_PUBLIC_URL).
"""

from __future__ import annotations

import pytest

from shared.config import _resolve_database_url


PRIMARY = "postgresql://primary:pwd@internal-host:5432/db"
FALLBACK = "postgresql://primary:pwd@proxy.rlwy.net:54321/db"


def test_database_url_takes_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    """Только DATABASE_URL задан — используется он."""
    monkeypatch.setenv("DATABASE_URL", PRIMARY)
    monkeypatch.delenv("DATABASE_PUBLIC_URL", raising=False)
    assert _resolve_database_url() == PRIMARY


def test_database_public_url_fallback(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Только DATABASE_PUBLIC_URL задан — используется он + INFO-лог."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PUBLIC_URL", FALLBACK)
    with caplog.at_level("INFO", logger="shared.config"):
        assert _resolve_database_url() == FALLBACK
    messages = [rec.message for rec in caplog.records]
    assert any("DATABASE_PUBLIC_URL" in msg for msg in messages)
    # Значение URL в лог не должно попасть (только сам факт fallback'а).
    assert all(FALLBACK not in msg for msg in messages)


def test_database_url_empty_string_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пустая строка DATABASE_URL тоже триггерит fallback (а не возвращает '')."""
    monkeypatch.setenv("DATABASE_URL", "   ")
    monkeypatch.setenv("DATABASE_PUBLIC_URL", FALLBACK)
    assert _resolve_database_url() == FALLBACK


def test_database_url_wins_over_public_when_both_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Обе заданы — primary DATABASE_URL имеет приоритет."""
    monkeypatch.setenv("DATABASE_URL", PRIMARY)
    monkeypatch.setenv("DATABASE_PUBLIC_URL", FALLBACK)
    assert _resolve_database_url() == PRIMARY


def test_both_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ни DATABASE_URL, ни DATABASE_PUBLIC_URL — RuntimeError."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_PUBLIC_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _resolve_database_url()
