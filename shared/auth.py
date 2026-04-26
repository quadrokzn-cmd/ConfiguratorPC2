# Авторизация: хеширование паролей, сессии, FastAPI-зависимости.
#
# Этап 9Б.1. Этот модуль — общий для конфигуратора (app/) и портала
# (portal/). Раньше жил в app/auth.py; теперь app/auth.py — тонкий
# реэкспорт, чтобы не ломать существующие импорты.
#
# Сессия хранится в подписанной cookie через starlette.SessionMiddleware.
# Имя cookie ("kt_session") и секрет (settings.session_secret_key)
# одинаковые для конфигуратора и портала — поэтому login через
# app.quadro.tatar пускает сразу и в config.quadro.tatar (cookie
# выставляется на .quadro.tatar — APP_COOKIE_DOMAIN). Локально cookie
# живёт по hostname (APP_COOKIE_DOMAIN пустой).
#
# В сессии держим:
#   - user_id:    int — id пользователя в users;
#   - csrf_token: str — токен для защиты POST-форм.
#
# Зависимости (FastAPI Depends):
#   - current_user(request) — возвращает AuthUser или None;
#   - require_login         — исключение LoginRequiredRedirect (поднимается
#                             на 302 в exception_handler конкретного app-а);
#   - require_admin         — 403, если роль не 'admin'.
#
# Permissions-зависимости — в shared/permissions.py.

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

import bcrypt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from shared.db import get_db


# --- Хеширование паролей -------------------------------------------------
#
# Используем bcrypt напрямую (без passlib). Passlib 1.7 несовместим с
# bcrypt 4.x — выдаёт ошибки при инициализации backend-а. Bcrypt 4.x
# имеет простой и стабильный API, его достаточно.
#
# Важно: bcrypt ограничивает длину пароля 72 байтами; более длинные
# пароли обрезаются. Для наших пользователей это не проблема.

_BCRYPT_ROUNDS = 12  # ~150 мс на хеш — разумный баланс безопасности и UX.


def hash_password(plain: str) -> str:
    """Хеширует пароль bcrypt-ом. Длинные пароли обрезаются до 72 байт."""
    data = plain.encode("utf-8")[:72]
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(data, salt).decode("ascii")


def verify_password(plain: str, password_hash: str) -> bool:
    """Проверяет пароль против хеша. False и при несовпадении, и при
    битом формате (пустой/обрезанный хеш)."""
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
    permissions: dict[str, Any] = field(default_factory=dict)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _row_to_user(row) -> AuthUser:
    perms = getattr(row, "permissions", None)
    if perms is None:
        perms = {}
    elif isinstance(perms, str):
        # Если драйвер вернул jsonb как строку — не падаем, парсим.
        import json
        try:
            perms = json.loads(perms)
        except Exception:
            perms = {}
    return AuthUser(
        id=row.id, login=row.login, role=row.role, name=row.name,
        permissions=dict(perms or {}),
    )


def get_user_by_login(session: Session, login: str) -> tuple[AuthUser, str] | None:
    """Активный пользователь по логину. Возвращает (AuthUser, password_hash)."""
    row = session.execute(
        text(
            "SELECT id, login, role, name, password_hash, permissions "
            "FROM users "
            "WHERE login = :login AND is_active = TRUE"
        ),
        {"login": login},
    ).first()
    if row is None:
        return None
    return _row_to_user(row), row.password_hash


def get_user_by_id(session: Session, user_id: int) -> AuthUser | None:
    """Активный пользователь по id."""
    row = session.execute(
        text(
            "SELECT id, login, role, name, permissions "
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
    """Полностью очищает сессию (и user_id, и csrf)."""
    request.session.clear()


def get_csrf_token(request: Request) -> str:
    """Текущий CSRF-токен. Если в сессии нет — выдаёт новый."""
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, form_token: str) -> bool:
    """Сравнивает токен из формы с сессионным. constant-time."""
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
        # user_id указывает на удалённого/деактивированного — чистим
        # сессию, чтобы в браузере не зависала «битая» кука.
        request.session.clear()
        return None
    return user


class LoginRequiredRedirect(Exception):
    """Внутреннее исключение: роут требует логин. Перехватывается
    в *.main через exception_handler и превращается в 302:
    в конфигураторе → ${PORTAL_URL}/login?next=<encoded URL>;
    в портале → /login?next=<encoded URL>."""


def require_login(
    user: AuthUser | None = Depends(current_user),
) -> AuthUser:
    """Защита роута: должен быть залогиненный пользователь."""
    if user is None:
        raise LoginRequiredRedirect()
    return user


# Алиас в стиле брифа 9Б.1 — оба имени поддерживаются.
require_user = require_login


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


# --- Cookie-конфигурация (этап 9Б.1) ------------------------------------

def build_session_cookie_kwargs(settings) -> dict[str, Any]:
    """Собирает kwargs для starlette SessionMiddleware из проектного settings.

    Используется и app/main.py, и portal/main.py — чтобы оба сервиса
    подписывали cookie одинаково и шарили сессию через .quadro.tatar.
    Если settings.cookie_domain пусто, ключ domain не выставляется
    вообще (иначе starlette вписал бы 'domain=' в Set-Cookie с пустым
    значением — некоторые браузеры на это смотрят косо)."""
    kwargs: dict[str, Any] = {
        "secret_key": settings.session_secret_key,
        "session_cookie": "kt_session",
        # 30 дней; менеджер вряд ли хочет логиниться каждую неделю.
        "max_age": 60 * 60 * 24 * 30,
        "same_site": "lax",
        "https_only": settings.is_production,
    }
    if settings.cookie_domain:
        kwargs["domain"] = settings.cookie_domain
    return kwargs
