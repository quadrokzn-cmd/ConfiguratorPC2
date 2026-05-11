# CLI-скрипт для создания пользователя-администратора (этап 5).
#
# Идемпотентен: если пользователь с логином 'admin' уже есть — ничего не
# меняет. Пароль берёт из переменной окружения ADMIN_INITIAL_PASSWORD.
#
# Использование:
#   python scripts/create_admin.py
#   python scripts/create_admin.py --name "Имя"
#
# Требует, чтобы миграция 007 уже была применена (проверяется в начале
# через SELECT из information_schema).

import argparse
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

from sqlalchemy import text

from shared.auth import hash_password
from shared.config import settings
from shared.db import SessionLocal


_DEFAULT_NAME = "Администратор"


def _check_migration_applied(session) -> bool:
    """Проверяет, применена ли миграция 007 — ищет таблицу users."""
    row = session.execute(
        text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'users'"
        )
    ).first()
    return row is not None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Создаёт пользователя admin при первом запуске веб-сервиса.",
    )
    ap.add_argument(
        "--name", default=_DEFAULT_NAME,
        help=f"Отображаемое имя (по умолчанию «{_DEFAULT_NAME}»)",
    )
    args = ap.parse_args()

    password = (settings.admin_initial_password or "").strip()
    if not password:
        print(
            "ОШИБКА: переменная ADMIN_INITIAL_PASSWORD не задана в .env.\n"
            "Укажите пароль в .env и повторите запуск.",
            file=sys.stderr,
        )
        return 2

    session = SessionLocal()
    try:
        if not _check_migration_applied(session):
            print(
                "ОШИБКА: таблица users не найдена.\n"
                "Сначала примените миграцию 007:\n"
                "    psql -f migrations/007_web_service.sql\n"
                "и повторите запуск.",
                file=sys.stderr,
            )
            return 3

        existing = session.execute(
            text("SELECT id, login FROM users WHERE login = 'admin'")
        ).first()
        if existing is not None:
            print(
                f"Пользователь 'admin' уже существует (id={existing.id}). "
                "Ничего не меняю.\n"
                "Для смены пароля используйте SQL: "
                "UPDATE users SET password_hash = '...' WHERE login = 'admin';"
            )
            return 0

        ph = hash_password(password)
        row = session.execute(
            text(
                "INSERT INTO users (login, password_hash, role, name) "
                "VALUES ('admin', :ph, 'admin', :name) "
                "RETURNING id"
            ),
            {"ph": ph, "name": args.name},
        ).first()
        session.commit()
        print(f"Создан пользователь 'admin' (id={row.id}). Можно логиниться.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
