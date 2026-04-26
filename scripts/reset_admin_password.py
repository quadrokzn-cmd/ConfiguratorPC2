# Сброс пароля пользователя-админа (этап 10.3).
#
# В отличие от bootstrap_admin.py, который НИКОГДА не трогает
# существующего пользователя, этот скрипт делает upsert:
#   - если пользователь с логином ADMIN_USERNAME уже есть — обновляет
#     password_hash (и сбрасывает is_active=TRUE на всякий случай);
#   - если нет — создаёт его с ролью 'admin'.
#
# Зачем нужен: после переноса данных на Railway через pg_dump/pg_restore
# пароль admin-а в БД равен локальному (admin123). На production
# должен быть другой пароль. Скрипт также пригодится в дальнейшем,
# если нужно срочно сбросить пароль админу через CLI.
#
# Запуск:
#   ADMIN_USERNAME=admin ADMIN_PASSWORD=Quadro1017 \
#       python -m scripts.reset_admin_password
#
# Подключение к БД берётся из стандартной DATABASE_URL — если нужно
# работать с Railway-БД с локалки, переопредели DATABASE_URL в окружении
# перед запуском.

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

from app.auth import hash_password, verify_password  # noqa: E402
from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402


_DEFAULT_NAME = "Администратор"


def main() -> int:
    username = (settings.admin_username or "").strip()
    password = settings.admin_password or ""

    if not username or not password:
        print(
            "ОШИБКА: ADMIN_USERNAME и/или ADMIN_PASSWORD не заданы.\n"
            "Задай переменные в окружении или в .env и повтори запуск.",
            file=sys.stderr,
        )
        return 2

    new_hash = hash_password(password)

    session = SessionLocal()
    try:
        existing = session.execute(
            text(
                "SELECT id, password_hash FROM users WHERE login = :l"
            ),
            {"l": username},
        ).first()

        if existing is None:
            row = session.execute(
                text(
                    "INSERT INTO users (login, password_hash, role, name, is_active) "
                    "VALUES (:l, :ph, 'admin', :name, TRUE) "
                    "RETURNING id"
                ),
                {"l": username, "ph": new_hash, "name": _DEFAULT_NAME},
            ).first()
            session.commit()
            print(
                f"reset_admin_password: создан пользователь '{username}' "
                f"(id={row.id}, role=admin). Новый пароль установлен."
            )
            return 0

        old_hash = existing.password_hash or ""
        session.execute(
            text(
                "UPDATE users "
                "SET password_hash = :ph, is_active = TRUE "
                "WHERE id = :id"
            ),
            {"ph": new_hash, "id": existing.id},
        )
        session.commit()

        # Контроль: новый хеш отличается от старого, и новый пароль
        # действительно с ним сверяется. Не выводим сами хеши в лог.
        verified = verify_password(password, new_hash)
        changed = new_hash != old_hash
        print(
            f"reset_admin_password: обновлён пользователь '{username}' "
            f"(id={existing.id}). hash_changed={changed}, verify_ok={verified}."
        )
        return 0 if (changed and verified) else 1
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
