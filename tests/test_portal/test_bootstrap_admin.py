# Тесты scripts/bootstrap_admin.py (этап 10.1).
#
# Поведение:
#   - оба env пустые → молча выходит без записей в users;
#   - оба env заданы и пользователя нет → создаёт его с ролью admin;
#   - оба env заданы и пользователь уже есть → НЕ перезаписывает.

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import text


@pytest.fixture
def reloaded_settings(monkeypatch):
    """Перезагружает shared.config с новыми ADMIN_USERNAME/ADMIN_PASSWORD
    и в финале возвращает исходные настройки, чтобы не повлиять на
    другие тесты, которые могут читать settings."""
    def _set(*, username: str | None, password: str | None):
        if username is None:
            monkeypatch.delenv("ADMIN_USERNAME", raising=False)
        else:
            monkeypatch.setenv("ADMIN_USERNAME", username)
        if password is None:
            monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
        else:
            monkeypatch.setenv("ADMIN_PASSWORD", password)
        import shared.config as cfg
        importlib.reload(cfg)
        return cfg

    yield _set
    # Возвращаем settings к состоянию из conftest.py (env уже откачен
    # monkeypatch-ем — reload пересоздаст исходный singleton).
    import shared.config as cfg
    importlib.reload(cfg)


def _import_bootstrap():
    """Импорт bootstrap_admin происходит после reload settings, чтобы он
    подхватил новый settings.admin_username/admin_password."""
    import scripts.bootstrap_admin as ba
    importlib.reload(ba)
    return ba


def _count_users(db_session, login: str) -> int:
    return int(db_session.execute(
        text("SELECT count(*) FROM users WHERE login = :l"), {"l": login}
    ).scalar() or 0)


def test_bootstrap_admin_silent_when_env_missing(reloaded_settings, db_session, capsys):
    reloaded_settings(username=None, password=None)
    ba = _import_bootstrap()
    rc = ba.main()
    assert rc == 0
    assert _count_users(db_session, "any") == 0
    assert "пропускаю" in capsys.readouterr().out


def test_bootstrap_admin_creates_user_when_absent(reloaded_settings, db_session):
    reloaded_settings(username="root", password="s3cret-pass")
    ba = _import_bootstrap()
    rc = ba.main()
    assert rc == 0
    row = db_session.execute(
        text("SELECT login, role FROM users WHERE login = 'root'")
    ).first()
    assert row is not None
    assert row.role == "admin"


def test_bootstrap_admin_keeps_existing_user(reloaded_settings, db_session):
    # Создаём пользователя руками с известным хешом.
    from shared.auth import hash_password
    original_hash = hash_password("original-pass")
    db_session.execute(
        text(
            "INSERT INTO users (login, password_hash, role, name) "
            "VALUES ('root', :ph, 'admin', 'Existing')"
        ),
        {"ph": original_hash},
    )
    db_session.commit()

    # Просим bootstrap создать root с другим паролем.
    reloaded_settings(username="root", password="different-pass")
    ba = _import_bootstrap()
    rc = ba.main()
    assert rc == 0

    row = db_session.execute(
        text("SELECT password_hash, name FROM users WHERE login = 'root'")
    ).first()
    # Хеш не должен измениться — пользователя не трогали.
    assert row.password_hash == original_hash
    assert row.name == "Existing"
