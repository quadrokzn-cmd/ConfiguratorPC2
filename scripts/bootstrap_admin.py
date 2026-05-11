# Идемпотентный bootstrap пользователя-админа при старте сервиса (этап 10.1).
#
# В отличие от старого scripts/create_admin.py, этот скрипт:
#   - читает логин и пароль из ADMIN_USERNAME / ADMIN_PASSWORD,
#     а не из ADMIN_INITIAL_PASSWORD (там был зашит логин 'admin');
#   - не падает, если переменные не заданы — просто молча выходит,
#     чтобы Procfile-цепочка `apply_migrations && bootstrap_admin && uvicorn`
#     не блокировала старт на свежем инстансе с пустым окружением;
#   - не трогает существующего пользователя — даже если в env лежит
#     другой пароль. Сменить пароль администратора можно либо в UI,
#     либо вручную через UPDATE users.
#
# Запуск:
#   python -m scripts.bootstrap_admin
# (используется в Procfile / railway.json как pre-start команда).

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from shared.auth import hash_password  # noqa: E402
from shared.config import settings  # noqa: E402
from shared.db import SessionLocal  # noqa: E402


_DEFAULT_NAME = "Администратор"


def _users_table_exists(session) -> bool:
    """Миграция 007 могла ещё не примениться (например, БД пустая
    и apply_migrations упал). В этом случае молча выходим — bootstrap
    не его дело чинить миграции."""
    row = session.execute(text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'users'"
    )).first()
    return row is not None


def main() -> int:
    username = (settings.admin_username or "").strip()
    password = settings.admin_password or ""

    if not username or not password:
        print(
            "bootstrap_admin: ADMIN_USERNAME/ADMIN_PASSWORD не заданы — пропускаю."
        )
        return 0

    session = SessionLocal()
    try:
        if not _users_table_exists(session):
            print(
                "bootstrap_admin: таблица users не найдена (миграции ещё не "
                "применены?) — пропускаю.",
                file=sys.stderr,
            )
            return 0

        existing = session.execute(
            text("SELECT id FROM users WHERE login = :l"),
            {"l": username},
        ).first()
        if existing is not None:
            print(
                f"bootstrap_admin: пользователь '{username}' уже существует "
                f"(id={existing.id}) — ничего не меняю."
            )
            return 0

        ph = hash_password(password)
        row = session.execute(
            text(
                "INSERT INTO users (login, password_hash, role, name) "
                "VALUES (:l, :ph, 'admin', :name) "
                "RETURNING id"
            ),
            {"l": username, "ph": ph, "name": _DEFAULT_NAME},
        ).first()
        session.commit()
        print(
            f"bootstrap_admin: создан пользователь '{username}' "
            f"с ролью admin (id={row.id})."
        )
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
