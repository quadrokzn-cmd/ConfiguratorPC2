# Сервис ручного сопоставления unmapped_supplier_items с компонентами БД.
#
# Работает только с новой таблицей (миграция 009). Все три действия
# админа — merge, confirm_as_new, defer — проходят здесь; веб-роут
# admin_router только декодирует формы и вызывает эти функции.

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.enrichment.base import ALLOWED_TABLES, CATEGORY_TO_TABLE


# Статусы строки unmapped_supplier_items — единый источник истины.
STATUS_PENDING       = "pending"
STATUS_CREATED_NEW   = "created_new"
STATUS_MERGED        = "merged"
STATUS_CONFIRMED_NEW = "confirmed_new"

# Активные статусы — те, что требуют внимания админа.
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_CREATED_NEW)


# ---------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------

def count_active(session: Session) -> int:
    row = session.execute(
        text(
            "SELECT COUNT(*) AS c FROM unmapped_supplier_items "
            "WHERE status IN ('pending', 'created_new')"
        )
    ).first()
    return int(row.c) if row else 0


@dataclass
class UnmappedRow:
    id: int
    supplier_id: int
    supplier_name: str
    supplier_sku: str
    raw_category: str
    guessed_category: str | None
    brand: str | None
    mpn: str | None
    gtin: str | None
    raw_name: str
    price: float | None
    currency: str | None
    stock: int
    transit: int
    status: str
    notes: str | None
    resolved_component_id: int | None
    created_at: object   # datetime


def list_active(
    session: Session, *,
    supplier_id: int | None = None,
    category: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[UnmappedRow]:
    """Список активных (pending + created_new) строк. Фильтры по
    supplier_id, category, status применяются при наличии."""
    where = ["u.status IN ('pending', 'created_new')"]
    params: dict[str, object] = {}
    if supplier_id is not None:
        where.append("u.supplier_id = :sid")
        params["sid"] = supplier_id
    if category:
        where.append("u.guessed_category = :cat")
        params["cat"] = category
    if status:
        # перезаписываем верхний предикат на конкретный статус
        where[0] = "u.status = :st"
        params["st"] = status

    where_sql = " AND ".join(where)
    params["lim"] = int(limit)
    params["off"] = int(offset)

    rows = session.execute(
        text(
            "SELECT u.id, u.supplier_id, s.name AS supplier_name, "
            "       u.supplier_sku, u.raw_category, u.guessed_category, "
            "       u.brand, u.mpn, u.gtin, u.raw_name, "
            "       u.price, u.currency, u.stock, u.transit, "
            "       u.status, u.notes, u.resolved_component_id, u.created_at "
            "FROM unmapped_supplier_items u "
            "JOIN suppliers s ON s.id = u.supplier_id "
            f"WHERE {where_sql} "
            "ORDER BY u.created_at DESC, u.id DESC "
            "LIMIT :lim OFFSET :off"
        ),
        params,
    ).mappings().all()
    return [UnmappedRow(**dict(r)) for r in rows]


def get_by_id(session: Session, row_id: int) -> UnmappedRow | None:
    row = session.execute(
        text(
            "SELECT u.id, u.supplier_id, s.name AS supplier_name, "
            "       u.supplier_sku, u.raw_category, u.guessed_category, "
            "       u.brand, u.mpn, u.gtin, u.raw_name, "
            "       u.price, u.currency, u.stock, u.transit, "
            "       u.status, u.notes, u.resolved_component_id, u.created_at "
            "FROM unmapped_supplier_items u "
            "JOIN suppliers s ON s.id = u.supplier_id "
            "WHERE u.id = :id"
        ),
        {"id": row_id},
    ).mappings().first()
    return UnmappedRow(**dict(row)) if row else None


def list_suppliers(session: Session) -> list[dict]:
    rows = session.execute(
        text("SELECT id, name FROM suppliers WHERE is_active = TRUE ORDER BY name")
    ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Действия
# ---------------------------------------------------------------------------

def _table_for(category: str) -> str:
    if category not in CATEGORY_TO_TABLE:
        raise ValueError(f"Неизвестная категория: {category!r}")
    table = CATEGORY_TO_TABLE[category]
    if table not in ALLOWED_TABLES:
        raise RuntimeError(f"Таблица {table} вне whitelist")
    return table


def merge_with_component(
    session: Session, *,
    unmapped_id: int, target_component_id: int, admin_user_id: int,
) -> None:
    """Переносит supplier_prices на target_component_id и, если для
    этой записи был создан скелет-дубликат (status='created_new' или
    resolved_component_id указывает на другой компонент) — удаляет его.

    Три случая:
      а) status='pending' (ambiguous): supplier_prices уже привязан
         к resolved_component_id (первый из кандидатов). Если админ
         выбрал ДРУГОГО кандидата — переносим supplier_prices на него.
      б) status='created_new': supplier_prices привязан к свежесозданному
         скелету. Если target ≠ скелет — переносим supplier_prices и
         удаляем скелет (он не нужен: админ распознал, что это дубликат
         существующего компонента).
      в) target_component_id совпадает с текущим resolved_component_id —
         всё уже правильно привязано, просто меняем статус.
    """
    u = get_by_id(session, unmapped_id)
    if u is None:
        raise ValueError(f"unmapped_supplier_items id={unmapped_id} не найден")
    if u.guessed_category is None:
        raise ValueError(
            f"У записи id={unmapped_id} не задана guessed_category — "
            "нельзя определить таблицу компонента."
        )

    category = u.guessed_category
    table = _table_for(category)

    # Проверяем, что target_component_id существует в нужной таблице.
    exists = session.execute(
        text(f"SELECT id FROM {table} WHERE id = :id"),
        {"id": target_component_id},
    ).first()
    if exists is None:
        raise ValueError(
            f"Компонент id={target_component_id} в таблице {table} не найден."
        )

    current_cid = u.resolved_component_id
    skeleton_to_delete: int | None = None

    # Если был создан скелет (created_new) и админ выбирает другой компонент —
    # скелет нужно удалить после переноса supplier_prices.
    if (
        u.status == STATUS_CREATED_NEW
        and current_cid is not None
        and current_cid != target_component_id
    ):
        skeleton_to_delete = current_cid

    # Перенос supplier_prices (если компонент поменялся).
    if current_cid is not None and current_cid != target_component_id:
        # Сначала подчищаем возможную существующую строку на target
        # (теоретически её нет — supplier_prices уникальна по
        # (supplier_id, category, component_id)), но перестрахуемся:
        # переносим через UPDATE с подменой component_id. Если коллизия —
        # падаем понятной ошибкой.
        session.execute(
            text(
                "UPDATE supplier_prices "
                "SET component_id = :new_cid "
                "WHERE supplier_id = :sid AND category = :cat "
                "  AND component_id = :old_cid"
            ),
            {
                "new_cid": target_component_id,
                "sid":     u.supplier_id,
                "cat":     category,
                "old_cid": current_cid,
            },
        )

    # Удаляем скелет (если нужно). Делаем это ПОСЛЕ переноса
    # supplier_prices, иначе ON DELETE CASCADE снесёт и цену.
    # В наших таблицах FK нет, но порядок всё равно логичнее такой.
    if skeleton_to_delete is not None:
        # Дополнительная проверка: скелет — это тот, что мы создали
        # автоматически (у него обычно manufacturer='unknown'). Но
        # даже если админ назначил другое — у нас статус 'created_new'
        # и resolved_component_id указывает на скелет, так что он точно
        # не используется другими записями unmapped.
        # Проверим, что на этом id нет других supplier_prices:
        other_prices = session.execute(
            text(
                "SELECT COUNT(*) AS c FROM supplier_prices "
                "WHERE category = :cat AND component_id = :id"
            ),
            {"cat": category, "id": skeleton_to_delete},
        ).first()
        if other_prices and int(other_prices.c) == 0:
            session.execute(
                text(f"DELETE FROM {table} WHERE id = :id"),
                {"id": skeleton_to_delete},
            )

    # Обновляем статус unmapped_supplier_items.
    session.execute(
        text(
            "UPDATE unmapped_supplier_items SET "
            "    status                = :st, "
            "    resolved_component_id = :cid, "
            "    resolved_at           = NOW(), "
            "    resolved_by           = :uid "
            "WHERE id = :id"
        ),
        {
            "st":  STATUS_MERGED,
            "cid": target_component_id,
            "uid": admin_user_id,
            "id":  unmapped_id,
        },
    )
    session.commit()


def confirm_as_new(
    session: Session, *, unmapped_id: int, admin_user_id: int,
) -> None:
    """Админ подтверждает, что это отдельный новый товар. Статус
    переходит в 'confirmed_new'; компонент (если был создан скелет)
    остаётся, supplier_prices — тоже."""
    session.execute(
        text(
            "UPDATE unmapped_supplier_items SET "
            "    status      = :st, "
            "    resolved_at = NOW(), "
            "    resolved_by = :uid "
            "WHERE id = :id"
        ),
        {
            "st":  STATUS_CONFIRMED_NEW,
            "uid": admin_user_id,
            "id":  unmapped_id,
        },
    )
    session.commit()


def defer(session: Session, *, unmapped_id: int) -> None:
    """«Разобраться потом» — ничего не меняем, просто noop. Отдельный
    метод оставлен, чтобы роут всегда делал одно и то же: вызвать сервис
    и сделать redirect."""
    _ = session, unmapped_id  # noqa — явное «ничего не делаем»
