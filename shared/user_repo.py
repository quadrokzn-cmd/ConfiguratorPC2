# CRUD пользователей через text-SQL (этап 9Б.1).
#
# Раньше эти функции жили в app/services/web_service.py (list_users,
# create_manager, toggle_user_active) — оттуда их использовал
# /admin/users в конфигураторе. После переезда /admin/users в портал
# их нужно сделать общими, а заодно добавить операции с
# users.permissions (миграция 017).
#
# В app/services/web_service.py соответствующие функции остаются как
# тонкие реэкспорты — старые места уже их импортируют, и без шага
# совместимости зацепило бы пол-репозитория.

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from shared.permissions import MODULE_KEYS


# --- Чтение списка ------------------------------------------------------

def list_users(session: Session) -> list[dict[str, Any]]:
    """Все пользователи (активные и нет), отсортированы по дате создания."""
    rows = session.execute(
        text(
            "SELECT id, login, role, name, is_active, permissions, created_at "
            "FROM users ORDER BY created_at ASC"
        )
    ).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        perms = r.permissions or {}
        if isinstance(perms, str):
            try:
                perms = json.loads(perms)
            except Exception:
                perms = {}
        out.append({
            "id":          int(r.id),
            "login":       r.login,
            "role":        r.role,
            "name":        r.name,
            "is_active":   bool(r.is_active),
            "permissions": dict(perms),
            "created_at":  r.created_at,
        })
    return out


# --- Создание / изменение -----------------------------------------------

def _default_manager_permissions() -> dict[str, Any]:
    """По умолчанию у нового менеджера открыт только конфигуратор."""
    return {"configurator": True}


def create_manager(
    session: Session,
    *,
    login: str,
    password_hash: str,
    name: str,
    role: str = "manager",
    permissions: dict[str, Any] | None = None,
) -> int:
    """Создаёт пользователя. Возвращает id. При конфликте логина —
    ValueError('login_taken'). При невалидной роли — ValueError('invalid_role').

    role: 'manager' (по умолчанию) или 'admin'. Для admin permissions
    по умолчанию пустые (admin видит все модули и без прав); для manager —
    {"configurator": True}."""
    if role not in ("admin", "manager"):
        raise ValueError("invalid_role")
    exists = session.execute(
        text("SELECT 1 FROM users WHERE login = :login"),
        {"login": login},
    ).first()
    if exists:
        raise ValueError("login_taken")
    if permissions is None:
        permissions = {} if role == "admin" else _default_manager_permissions()
    row = session.execute(
        text(
            "INSERT INTO users (login, password_hash, role, name, permissions) "
            "VALUES (:login, :ph, :role, :name, CAST(:perms AS JSONB)) "
            "RETURNING id"
        ),
        {
            "login": login,
            "ph":    password_hash,
            "role":  role,
            "name":  name,
            "perms": json.dumps(permissions, ensure_ascii=False),
        },
    ).first()
    session.commit()
    return int(row.id)


def toggle_user_active(session: Session, user_id: int) -> bool:
    """Переключает is_active. Возвращает новое значение."""
    row = session.execute(
        text(
            "UPDATE users SET is_active = NOT is_active "
            "WHERE id = :id "
            "RETURNING is_active"
        ),
        {"id": user_id},
    ).first()
    session.commit()
    return bool(row.is_active) if row else False


def count_admins(session: Session) -> int:
    """Сколько пользователей с role='admin' в БД (включая неактивных).
    Используется для запрета понизить последнего админа в /admin/users."""
    row = session.execute(
        text("SELECT COUNT(*) AS n FROM users WHERE role = 'admin'")
    ).first()
    return int(row.n) if row else 0


def get_role(session: Session, user_id: int) -> str | None:
    """Текущая роль пользователя. None — если пользователя нет."""
    row = session.execute(
        text("SELECT role FROM users WHERE id = :id"),
        {"id": user_id},
    ).first()
    return row.role if row else None


def set_role(session: Session, user_id: int, role: str) -> bool:
    """Меняет users.role. Возвращает True, если строка обновлена.
    role: 'admin' или 'manager'."""
    if role not in ("admin", "manager"):
        raise ValueError("invalid_role")
    row = session.execute(
        text("UPDATE users SET role = :role WHERE id = :id RETURNING id"),
        {"id": user_id, "role": role},
    ).first()
    session.commit()
    return row is not None


def update_permissions(
    session: Session,
    user_id: int,
    permissions: dict[str, Any],
) -> bool:
    """Перезаписывает users.permissions. Возвращает True, если строка обновлена."""
    # Нормализуем — только известные ключи и только bool-значения.
    cleaned: dict[str, Any] = {}
    for k in MODULE_KEYS:
        if k in permissions:
            cleaned[k] = bool(permissions[k])
    row = session.execute(
        text(
            "UPDATE users SET permissions = CAST(:perms AS JSONB) "
            "WHERE id = :id "
            "RETURNING id"
        ),
        {"id": user_id, "perms": json.dumps(cleaned, ensure_ascii=False)},
    ).first()
    session.commit()
    return row is not None
