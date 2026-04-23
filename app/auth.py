# Авторизация веб-сервиса: хеширование паролей, сессии, декораторы.
#
# Сессия хранится в подписанной cookie через starlette.SessionMiddleware.
# В сессии держим:
#   - user_id:   int        — идентификатор залогиненного пользователя;
#   - csrf_token: str       — токен для защиты POST-форм.
#
# Декораторы (FastAPI Depends):
#   - current_user(request) — возвращает пользователя или None;
#   - require_login         — 302-редирект на /login, если не залогинен;
#   - require_admin         — 403, если роль не 'admin'.
#
# Пароли хешируются через passlib + bcrypt.

from __future__ import annotations

import secrets
from dataclasses import dataclass

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db


# --- Хеширование паролей -------------------------------------------------
#
# Используем bcrypt напрямую (без passlib). Passlib 1.7 несовместим с
# bcrypt 4.x — выдаёт ошибки при инициализации backend-а. Bcrypt 4.x
# имеет простой и стабильный API, его достаточно.
#
# Важно: bcrypt ограничивает длину пароля 72 байтами; более длинные
# пароли обрезаются. Для наших 4 пользователей это не проблема.

_BCRYPT_ROUNDS = 12  # разумное время/безопасность (~150 мс на хеш)


def hash_password(plain: str) -> str:
    """Хеширует пароль bcrypt-ом. Возвращает готовую к записи в БД строку.
    Длинные пароли обрезаются до 72 байт — это особенность bcrypt."""
    data = plain.encode("utf-8")[:72]
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(data, salt).decode("ascii")


def verify_password(plain: str, password_hash: str) -> bool:
    """Проверяет пароль против хеша. False — и на несовпадении, и на
    любой ошибке формата (например, пустой/битый хеш)."""
    if not plain or not password_hash:
        return False
    try:
        data = plain.encode("utf-8")[:72]
        return bcrypt.checkpw(data, password_hash.encode("ascii"))
    except Exception:
        return False


# --- Модель пользователя для UI -----------------------------------------

@dataclass
class AuthUser:
    """Минимальный набор полей пользователя для роутов/шаблонов."""
    id: int
    login: str
    role: str
    name: str

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _row_to_user(row) -> AuthUser:
    return AuthUser(id=row.id, login=row.login, role=row.role, name=row.name)


def get_user_by_login(session: Session, login: str) -> tuple[AuthUser, str] | None:
    """Ищет активного пользователя по логину. Возвращает (AuthUser, password_hash)
    или None, если не найден/деактивирован."""
    row = session.execute(
        text(
            "SELECT id, login, role, name, password_hash "
            "FROM users "
            "WHERE login = :login AND is_active = TRUE"
        ),
        {"login": login},
    ).first()
    if row is None:
        return None
    return _row_to_user(row), row.password_hash


def get_user_by_id(session: Session, user_id: int) -> AuthUser | None:
    """Ищет активного пользователя по id (нужно в current_user по сессии)."""
    row = session.execute(
        text(
            "SELECT id, login, role, name "
            "FROM users "
            "WHERE id = :id AND is_active = TRUE"
        ),
        {"id": user_id},
    ).first()
    return _row_to_user(row) if row else None


# --- Сессия и CSRF -------------------------------------------------------

def login_session(request: Request, user: AuthUser) -> None:
    """Помечает сессию как залогиненную, фиксирует CSRF-токен."""
    request.session["user_id"] = user.id
    request.session["csrf_token"] = secrets.token_urlsafe(32)


def logout_session(request: Request) -> None:
    """Очищает сессию полностью (и user_id, и csrf)."""
    request.session.clear()


def get_csrf_token(request: Request) -> str:
    """Возвращает текущий CSRF-токен. Если в сессии нет — выдаёт новый.
    Нужен и неавторизованным страницам (форма логина), и авторизованным."""
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, form_token: str) -> bool:
    """Сравнивает токен из формы с токеном из сессии. constant-time."""
    session_token = request.session.get("csrf_token") or ""
    if not session_token or not form_token:
        return False
    return secrets.compare_digest(session_token, form_token)


# --- FastAPI Depends-зависимости ----------------------------------------

def current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthUser | None:
    """Возвращает залогиненного пользователя либо None."""
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = get_user_by_id(db, int(user_id))
    if user is None:
        # user_id в сессии указывает на удалённого/деактивированного —
        # очищаем сессию, чтобы не зависали «битые» куки.
        request.session.clear()
        return None
    return user


class LoginRequiredRedirect(Exception):
    """Внутреннее исключение: роут просит логин. Обрабатывается
    в app.main через exception_handler и превращается в 302 на /login."""


def require_login(
    user: AuthUser | None = Depends(current_user),
) -> AuthUser:
    """Защита роута: должен быть залогиненный пользователь."""
    if user is None:
        raise LoginRequiredRedirect()
    return user


def require_admin(
    user: AuthUser = Depends(require_login),
) -> AuthUser:
    """Защита роута: только для админов."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Доступ только для администратора.",
        )
    return user
