# Сервис управления поставщиками (этап 9А.2 — закрытие техдолга 8.3).
#
# До этого этапа email/контакты у suppliers редактировались только
# через psql. Теперь админ ведёт справочник через /admin/suppliers.
#
# Поля таблицы (после миграций 011 и 012):
#   id, name, email, contact_person, contact_phone, is_active, created_at
#
# Деактивированный поставщик (is_active=False) не участвует в подборе
# цен — фильтр прокинут в configurator/prices.py:fetch_offers и
# email-сервис, чтобы запросы цен не уходили на отключённых.

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def list_suppliers(session: Session) -> list[dict]:
    """Все поставщики, активные сверху."""
    rows = session.execute(
        text(
            "SELECT id, name, email, contact_person, contact_phone, "
            "       is_active, created_at "
            "FROM suppliers "
            "ORDER BY is_active DESC, name ASC"
        )
    ).all()
    return [
        {
            "id":             int(r.id),
            "name":           r.name,
            "email":          r.email,
            "contact_person": r.contact_person,
            "contact_phone":  r.contact_phone,
            "is_active":      bool(r.is_active),
            "created_at":     r.created_at,
        }
        for r in rows
    ]


def get_supplier(session: Session, supplier_id: int) -> dict | None:
    """Один поставщик по id или None."""
    r = session.execute(
        text(
            "SELECT id, name, email, contact_person, contact_phone, "
            "       is_active, created_at "
            "FROM suppliers WHERE id = :id"
        ),
        {"id": supplier_id},
    ).first()
    if r is None:
        return None
    return {
        "id":             int(r.id),
        "name":           r.name,
        "email":          r.email,
        "contact_person": r.contact_person,
        "contact_phone":  r.contact_phone,
        "is_active":      bool(r.is_active),
        "created_at":     r.created_at,
    }


def create_supplier(
    session: Session,
    *,
    name: str,
    email: str | None = None,
    contact_person: str | None = None,
    contact_phone: str | None = None,
    is_active: bool = True,
) -> int:
    """Создаёт поставщика. Возвращает id.

    На конфликт по UNIQUE(name) — ValueError('name_taken').
    """
    exists = session.execute(
        text("SELECT 1 FROM suppliers WHERE name = :n"),
        {"n": name},
    ).first()
    if exists:
        raise ValueError("name_taken")
    row = session.execute(
        text(
            "INSERT INTO suppliers (name, email, contact_person, "
            "                       contact_phone, is_active) "
            "VALUES (:n, :e, :cp, :ph, :a) "
            "RETURNING id"
        ),
        {"n": name, "e": email, "cp": contact_person,
         "ph": contact_phone, "a": is_active},
    ).first()
    session.commit()
    return int(row.id)


def update_supplier(
    session: Session,
    supplier_id: int,
    *,
    name: str,
    email: str | None,
    contact_person: str | None,
    contact_phone: str | None,
    is_active: bool,
) -> bool:
    """Обновляет поставщика. Возвращает True если запись была.

    На конфликт по UNIQUE(name) с другой записью — ValueError('name_taken').
    """
    other = session.execute(
        text("SELECT 1 FROM suppliers WHERE name = :n AND id <> :id"),
        {"n": name, "id": supplier_id},
    ).first()
    if other:
        raise ValueError("name_taken")
    res = session.execute(
        text(
            "UPDATE suppliers "
            "SET name = :n, email = :e, contact_person = :cp, "
            "    contact_phone = :ph, is_active = :a "
            "WHERE id = :id"
        ),
        {"id": supplier_id, "n": name, "e": email, "cp": contact_person,
         "ph": contact_phone, "a": is_active},
    )
    session.commit()
    return res.rowcount > 0


def toggle_active(session: Session, supplier_id: int) -> bool | None:
    """Переключает is_active. Возвращает новое значение или None если нет записи."""
    r = session.execute(
        text(
            "UPDATE suppliers SET is_active = NOT is_active "
            "WHERE id = :id RETURNING is_active"
        ),
        {"id": supplier_id},
    ).first()
    session.commit()
    return bool(r.is_active) if r else None


def has_dependencies(session: Session, supplier_id: int) -> dict[str, int]:
    """Сколько связанных записей у поставщика — supplier_prices,
    sent_emails, unmapped_supplier_items, price_uploads.

    Если хоть одна не ноль, удалять нельзя — только деактивировать.
    """
    out: dict[str, int] = {}
    for table, key in [
        ("supplier_prices",          "prices"),
        ("sent_emails",              "emails"),
        ("unmapped_supplier_items",  "unmapped"),
        ("price_uploads",            "uploads"),
    ]:
        cnt = session.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE supplier_id = :id"),
            {"id": supplier_id},
        ).scalar()
        out[key] = int(cnt or 0)
    return out


def delete_supplier(session: Session, supplier_id: int) -> str:
    """Удаляет поставщика. Если есть связи — поднимает ValueError('has_links')
    и предлагает деактивировать вместо удаления.

    Возвращает 'deleted' при успехе.
    """
    deps = has_dependencies(session, supplier_id)
    if any(v > 0 for v in deps.values()):
        raise ValueError("has_links")
    res = session.execute(
        text("DELETE FROM suppliers WHERE id = :id"),
        {"id": supplier_id},
    )
    session.commit()
    if res.rowcount == 0:
        raise ValueError("not_found")
    return "deleted"
