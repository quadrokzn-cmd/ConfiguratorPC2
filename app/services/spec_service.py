# Бизнес-логика проектов и спецификаций (этап 6.2).
#
# Содержит чистые функции над БД:
#   - список проектов пользователя (с агрегатами конфигураций и сумм);
#   - детальная страница проекта (проект + запросы + выбранные позиции);
#   - переименование/удаление проекта, удаление запроса;
#   - select / deselect / update_quantity — изменение спецификации.
#
# Сами HTTP-роуты тонкие и живут в app/routers/project_router.py.

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.routers.main_router import _prepare_variants  # переиспользуем
from app.services.spec_naming import generate_auto_name
from app.services.web_result_view import enrich_variants_with_specs


# ---------------------------------------------------------------------
# Доступ к проекту / проверки
# ---------------------------------------------------------------------

def get_project_or_none(
    session: Session,
    *,
    project_id: int,
    requester_user_id: int,
    requester_is_admin: bool,
) -> dict | None:
    """Возвращает dict с полями проекта + автором, либо None, если
    проект не найден. Если проект принадлежит другому менеджеру
    и запрашивающий — не админ, вернёт {'_forbidden': True}."""
    row = session.execute(
        text(
            "SELECT p.id, p.user_id, p.name, p.created_at, p.updated_at, "
            "       u.login AS author_login, u.name AS author_name "
            "FROM projects p JOIN users u ON u.id = p.user_id "
            "WHERE p.id = :pid"
        ),
        {"pid": project_id},
    ).first()
    if row is None:
        return None
    if not requester_is_admin and int(row.user_id) != int(requester_user_id):
        return {"_forbidden": True}
    return {
        "id":           int(row.id),
        "user_id":      int(row.user_id),
        "name":         row.name,
        "created_at":   row.created_at,
        "updated_at":   row.updated_at,
        "author_login": row.author_login,
        "author_name":  row.author_name,
    }


# ---------------------------------------------------------------------
# Создание/переименование/удаление
# ---------------------------------------------------------------------

def create_empty_project(session: Session, *, user_id: int, name: str) -> int:
    row = session.execute(
        text(
            "INSERT INTO projects (user_id, name) "
            "VALUES (:uid, :name) RETURNING id"
        ),
        {"uid": user_id, "name": name},
    ).first()
    session.commit()
    return int(row.id)


def rename_project(session: Session, *, project_id: int, name: str) -> None:
    session.execute(
        text(
            "UPDATE projects SET name = :name, updated_at = NOW() "
            "WHERE id = :pid"
        ),
        {"pid": project_id, "name": name},
    )
    session.commit()


def delete_project(session: Session, *, project_id: int) -> None:
    # ON DELETE CASCADE из 007/008 удалит queries и specification_items.
    session.execute(
        text("DELETE FROM projects WHERE id = :pid"),
        {"pid": project_id},
    )
    session.commit()


def delete_query(session: Session, *, query_id: int) -> None:
    # ON DELETE CASCADE удалит specification_items, ссылающиеся на этот query.
    session.execute(
        text("DELETE FROM queries WHERE id = :qid"),
        {"qid": query_id},
    )
    session.commit()


# ---------------------------------------------------------------------
# Список проектов для /projects
# ---------------------------------------------------------------------

def list_projects(
    session: Session,
    *,
    user_id: int | None,
    is_admin: bool,
    limit: int = 500,
) -> list[dict]:
    """Список проектов. Для менеджера — свои; для админа — все."""
    if is_admin:
        where = ""
        params: dict[str, Any] = {"lim": limit}
    else:
        where = "WHERE p.user_id = :uid"
        params = {"uid": user_id, "lim": limit}

    sql = (
        "SELECT p.id, p.user_id, p.name, p.created_at, p.updated_at, "
        "       u.login AS author_login, u.name AS author_name, "
        "       (SELECT COUNT(*) FROM queries q WHERE q.project_id = p.id) AS qcount, "
        "       (SELECT COALESCE(SUM(total_usd), 0) FROM specification_items s "
        "         WHERE s.project_id = p.id) AS spec_usd, "
        "       (SELECT COALESCE(SUM(total_rub), 0) FROM specification_items s "
        "         WHERE s.project_id = p.id) AS spec_rub "
        "FROM projects p JOIN users u ON u.id = p.user_id "
        f"{where} "
        "ORDER BY p.created_at DESC LIMIT :lim"
    )
    rows = session.execute(text(sql), params).all()
    return [
        {
            "id":            int(r.id),
            "user_id":       int(r.user_id),
            "name":          r.name,
            "created_at":    r.created_at,
            "updated_at":    r.updated_at,
            "author_login":  r.author_login,
            "author_name":   r.author_name,
            "queries_count": int(r.qcount or 0),
            "spec_total_usd": float(r.spec_usd) if r.spec_usd is not None else 0.0,
            "spec_total_rub": float(r.spec_rub) if r.spec_rub is not None else 0.0,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------
# Конфигурации внутри проекта
# ---------------------------------------------------------------------

def list_queries_of_project(session: Session, *, project_id: int) -> list[dict]:
    """Конфигурации проекта в порядке создания."""
    rows = session.execute(
        text(
            "SELECT id, raw_text, status, error_msg, build_result_json, "
            "       created_at "
            "FROM queries WHERE project_id = :pid "
            "ORDER BY created_at ASC, id ASC"
        ),
        {"pid": project_id},
    ).all()
    out: list[dict] = []
    for r in rows:
        short = (r.raw_text or "").strip().replace("\n", " ")
        if len(short) > 140:
            short = short[:137] + "…"
        out.append({
            "id":           int(r.id),
            "raw_text":     r.raw_text,
            "short_text":   short,
            "status":       r.status,
            "error_msg":    r.error_msg,
            "build_result": r.build_result_json,
            "created_at":   r.created_at,
        })
    return out


# ---------------------------------------------------------------------
# Спецификация
# ---------------------------------------------------------------------

def list_spec_items(session: Session, *, project_id: int) -> list[dict]:
    """Строки спецификации проекта в порядке position."""
    rows = session.execute(
        text(
            "SELECT id, query_id, variant_manufacturer, quantity, position, "
            "       auto_name, custom_name, unit_usd, unit_rub, total_usd, total_rub, "
            "       created_at, updated_at "
            "FROM specification_items "
            "WHERE project_id = :pid "
            "ORDER BY position ASC, id ASC"
        ),
        {"pid": project_id},
    ).all()
    return [_spec_row_to_dict(r) for r in rows]


def _spec_row_to_dict(r) -> dict:
    def _num(v) -> float:
        return float(v) if v is not None else 0.0
    return {
        "id":                   int(r.id),
        "query_id":             int(r.query_id),
        "variant_manufacturer": r.variant_manufacturer,
        "quantity":             int(r.quantity),
        "position":             int(r.position),
        "auto_name":            r.auto_name,
        "custom_name":          r.custom_name,
        "display_name":         r.custom_name or r.auto_name,
        "unit_usd":             _num(r.unit_usd),
        "unit_rub":             _num(r.unit_rub),
        "total_usd":            _num(r.total_usd),
        "total_rub":            _num(r.total_rub),
        "created_at":           r.created_at,
        "updated_at":           r.updated_at,
    }


def spec_totals(items: list[dict]) -> dict:
    """Сумма по всем строкам спецификации."""
    total_usd = sum(it["total_usd"] for it in items)
    total_rub = sum(it["total_rub"] for it in items)
    return {
        "total_usd": round(total_usd, 2),
        "total_rub": round(total_rub, 2),
    }


# --- Загрузка варианта для генерации имени/цен ----------------------------

def _load_variant_for_naming(
    session: Session,
    *,
    query_id: int,
    project_id: int,
    manufacturer: str,
) -> dict | None:
    """Достаёт из queries.build_result_json нужный вариант и обогащает
    его сырыми спецификациями. Возвращает dict варианта или None,
    если запрос не принадлежит проекту/варианта с такой маркой нет."""
    row = session.execute(
        text(
            "SELECT build_result_json, project_id "
            "FROM queries WHERE id = :qid"
        ),
        {"qid": query_id},
    ).first()
    if row is None or int(row.project_id) != int(project_id):
        return None
    build_result = row.build_result_json
    if not build_result:
        return None
    variants = _prepare_variants(build_result)
    target = next(
        (v for v in variants if (v.get("manufacturer") or "").lower() == manufacturer.lower()),
        None,
    )
    if target is None:
        return None
    enrich_variants_with_specs([target], session)
    return target


# --- Выбор / снятие / изменение количества --------------------------------

class SpecError(Exception):
    """Ошибка спецификации: запрос не найден в проекте, нет варианта и т.п."""


def _next_position(session: Session, project_id: int) -> int:
    row = session.execute(
        text(
            "SELECT COALESCE(MAX(position), 0) AS p "
            "FROM specification_items WHERE project_id = :pid"
        ),
        {"pid": project_id},
    ).first()
    return int(row.p or 0) + 1


def select_variant(
    session: Session,
    *,
    project_id: int,
    query_id: int,
    manufacturer: str,
    quantity: int,
) -> list[dict]:
    """Добавляет вариант в спецификацию проекта (или игнорирует, если
    такая пара (query, variant) уже выбрана). Возвращает актуальный
    список строк спецификации."""
    if quantity is None or int(quantity) <= 0:
        raise SpecError("Количество должно быть положительным.")
    quantity = int(quantity)

    # Если уже есть — ничего не делаем, просто возвращаем список.
    existing = session.execute(
        text(
            "SELECT id FROM specification_items "
            "WHERE project_id = :pid AND query_id = :qid "
            "  AND variant_manufacturer = :mfg"
        ),
        {"pid": project_id, "qid": query_id, "mfg": manufacturer},
    ).first()
    if existing is not None:
        return list_spec_items(session, project_id=project_id)

    variant = _load_variant_for_naming(
        session,
        project_id=project_id,
        query_id=query_id,
        manufacturer=manufacturer,
    )
    if variant is None:
        raise SpecError("В запросе нет такого варианта.")

    unit_usd = float(variant.get("total_usd") or 0.0)
    unit_rub = float(variant.get("total_rub") or 0.0)
    total_usd = round(unit_usd * quantity, 2)
    total_rub = round(unit_rub * quantity, 2)
    auto_name = generate_auto_name(variant, fallback_id=query_id)
    position = _next_position(session, project_id)

    # Нормализуем: в БД всегда кладём каноничное «Intel»/«AMD» —
    # в точности то, что пришло в variant.manufacturer.
    mfg_canon = variant.get("manufacturer") or manufacturer

    session.execute(
        text(
            "INSERT INTO specification_items "
            "  (project_id, query_id, variant_manufacturer, quantity, position, "
            "   auto_name, unit_usd, unit_rub, total_usd, total_rub) "
            "VALUES (:pid, :qid, :mfg, :q, :pos, :n, :uu, :ur, :tu, :tr) "
            "ON CONFLICT (project_id, query_id, variant_manufacturer) DO NOTHING"
        ),
        {
            "pid": project_id, "qid": query_id, "mfg": mfg_canon,
            "q": quantity, "pos": position, "n": auto_name,
            "uu": round(unit_usd, 2), "ur": round(unit_rub, 2),
            "tu": total_usd, "tr": total_rub,
        },
    )
    session.execute(
        text("UPDATE projects SET updated_at = NOW() WHERE id = :pid"),
        {"pid": project_id},
    )
    session.commit()
    return list_spec_items(session, project_id=project_id)


def deselect_variant(
    session: Session,
    *,
    project_id: int,
    query_id: int,
    manufacturer: str,
) -> list[dict]:
    """Убирает пару (query, variant) из спецификации. Идемпотентно."""
    session.execute(
        text(
            "DELETE FROM specification_items "
            "WHERE project_id = :pid AND query_id = :qid "
            "  AND variant_manufacturer = :mfg"
        ),
        {"pid": project_id, "qid": query_id, "mfg": manufacturer},
    )
    # Пересобираем позиции без пропусков, чтобы порядок оставался
    # компактным 1, 2, 3…
    _renumber_positions(session, project_id)
    session.commit()
    return list_spec_items(session, project_id=project_id)


def update_quantity(
    session: Session,
    *,
    project_id: int,
    query_id: int,
    manufacturer: str,
    quantity: int,
) -> list[dict]:
    """Меняет количество. total_usd/rub пересчитывается из unit_usd/rub,
    сохранённого при первом выборе. Это сохраняет «снимок» цены."""
    if quantity is None or int(quantity) <= 0:
        raise SpecError("Количество должно быть положительным.")
    quantity = int(quantity)

    row = session.execute(
        text(
            "SELECT id, unit_usd, unit_rub FROM specification_items "
            "WHERE project_id = :pid AND query_id = :qid "
            "  AND variant_manufacturer = :mfg"
        ),
        {"pid": project_id, "qid": query_id, "mfg": manufacturer},
    ).first()
    if row is None:
        raise SpecError("Такой позиции нет в спецификации.")

    unit_usd = float(row.unit_usd) if row.unit_usd is not None else 0.0
    unit_rub = float(row.unit_rub) if row.unit_rub is not None else 0.0
    total_usd = round(unit_usd * quantity, 2)
    total_rub = round(unit_rub * quantity, 2)

    session.execute(
        text(
            "UPDATE specification_items "
            "SET quantity = :q, total_usd = :tu, total_rub = :tr, updated_at = NOW() "
            "WHERE id = :id"
        ),
        {"id": int(row.id), "q": quantity, "tu": total_usd, "tr": total_rub},
    )
    session.commit()
    return list_spec_items(session, project_id=project_id)


def _renumber_positions(session: Session, project_id: int) -> None:
    """Переписывает position так, чтобы они шли 1, 2, 3 без разрывов,
    сохраняя порядок по текущему position. Коммит делает вызывающий."""
    rows = session.execute(
        text(
            "SELECT id FROM specification_items "
            "WHERE project_id = :pid ORDER BY position ASC, id ASC"
        ),
        {"pid": project_id},
    ).all()
    for i, r in enumerate(rows, start=1):
        session.execute(
            text(
                "UPDATE specification_items SET position = :p "
                "WHERE id = :id"
            ),
            {"p": i, "id": int(r.id)},
        )


# ---------------------------------------------------------------------
# Вспомогалка: пара {query_id: set(selected manufacturers)} для шаблона
# ---------------------------------------------------------------------

def selected_set(items: list[dict]) -> dict[int, dict[str, dict]]:
    """Индекс выбранных позиций: {query_id: {manufacturer: item}}.
    Шаблону удобнее проверять по .get(qid, {}).get(manuf)."""
    out: dict[int, dict[str, dict]] = {}
    for it in items:
        out.setdefault(it["query_id"], {})[it["variant_manufacturer"]] = it
    return out
