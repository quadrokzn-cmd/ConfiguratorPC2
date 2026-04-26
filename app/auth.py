# Совместимость со старыми импортами `from app.auth import ...`.
#
# Этап 9Б.1: вся логика авторизации переехала в shared/auth.py —
# теперь её используют и конфигуратор, и портал. Этот модуль остаётся
# тонким реэкспортом, чтобы существующие импорты в app/* и в
# scripts/bootstrap_admin.py продолжали работать без изменений.

from shared.auth import (
    AuthUser,
    LoginRequiredRedirect,
    build_session_cookie_kwargs,
    current_user,
    get_csrf_token,
    get_user_by_id,
    get_user_by_login,
    hash_password,
    login_session,
    logout_session,
    require_admin,
    require_login,
    require_user,
    verify_csrf,
    verify_password,
)


__all__ = [
    "AuthUser",
    "LoginRequiredRedirect",
    "build_session_cookie_kwargs",
    "current_user",
    "get_csrf_token",
    "get_user_by_id",
    "get_user_by_login",
    "hash_password",
    "login_session",
    "logout_session",
    "require_admin",
    "require_login",
    "require_user",
    "verify_csrf",
    "verify_password",
]
