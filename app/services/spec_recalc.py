# Пересчёт цен в спецификации проекта (этап 9А.2.1).
#
# Спецификация хранит цены-снимки на момент выбора варианта в проект.
# Иногда менеджеру нужно «освежить» старый проект — пересчитать цены
# по актуальным supplier_prices.
#
# Логика:
#   1. Для каждой позиции спецификации читаем сохранённый build_result_json
#      связанного query, находим вариант с указанным variant_manufacturer.
#   2. По каждому компоненту варианта (category, component_id) считаем
#      минимальную актуальную цену через supplier_prices с фильтрами:
#        - is_active = TRUE у поставщика;
#        - is_hidden = FALSE у компонента (в соответствующей таблице);
#        - stock_qty > 0 (transit пересчёт не учитывает: не освежаем
#          под транзит — только реальное наличие).
#   3. Складываем в новую unit_usd. unit_rub считаем по сохранённому
#      курсу варианта (usd_rub_rate из build_result).
#   4. Если сумма не изменилась — recalculated_at не трогаем.
#   5. Если для какого-то компонента нет ни одного активного
#      ненайденного предложения — позиция получает status='unavailable'
#      и в БД не обновляется.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.routers.main_router import _prepare_variants


# Допустимые категории — те же, что в подборе.
_CATEGORY_TO_TABLE: dict[str, str] = {
    "cpu":         "cpus",
    "motherboard": "motherboards",
    "ram":         "rams",
    "gpu":         "gpus",
    "storage":     "storages",
    "case":        "cases",
    "psu":         "psus",
    "cooler":      "coolers",
}


@dataclass
class RecalcDelta:
    """Результат пересчёта одной позиции спецификации."""
    spec_item_id: int
    config_name: str
    qty: int
    old_unit_usd: float
    new_unit_usd: float
    old_total_usd: float
    new_total_usd: float
    delta_pct: float
    changed: bool
    status: str = "ok"               # 'ok' | 'unavailable'
    unavailable_components: list[str] = field(default_factory=list)


@dataclass
class RecalcResult:
    """Сводный результат пересчёта всей спецификации."""
    items: list[RecalcDelta]
    changed_count: int
    total_count: int


def _component_min_price_usd(
    session: Session,
    *,
    category: str,
    component_id: int,
    usd_rub: float,
) -> float | None:
    """Возвращает минимальную актуальную цену компонента в USD среди
    активных поставщиков с stock>0, где компонент не is_hidden.
    Если кандидатов нет — возвращает None.
    """
    table = _CATEGORY_TO_TABLE.get(category)
    if not table:
        return None
    sql = text(
        f"""
        SELECT MIN(
                 CASE WHEN sp.currency = 'USD' THEN sp.price
                      ELSE sp.price / :usd_rub END
               ) AS min_usd
        FROM supplier_prices sp
        JOIN suppliers s ON s.id = sp.supplier_id
        JOIN {table} c ON c.id = sp.component_id
        WHERE sp.category = :cat
          AND sp.component_id = :cid
          AND s.is_active = TRUE
          AND c.is_hidden = FALSE
          AND sp.stock_qty > 0
        """
    )
    row = session.execute(
        sql,
        {"cat": category, "cid": int(component_id), "usd_rub": float(usd_rub)},
    ).first()
    if row is None or row.min_usd is None:
        return None
    return float(row.min_usd)


def _component_label(c: dict) -> str:
    """Человекочитаемое имя компонента для сообщений об ошибках."""
    cat = c.get("category") or "?"
    model = c.get("model") or f"id={c.get('component_id')}"
    return f"{cat}: {model}"


def _recalc_one_spec_item(
    session: Session,
    *,
    spec_item: dict,
) -> RecalcDelta:
    """Пересчитывает одну позицию спецификации, БЕЗ записи в БД.
    Возвращает дельту с changed/status и новыми ценами.
    """
    item_id = spec_item["id"]
    qty = int(spec_item["quantity"])
    old_unit_usd = float(spec_item["unit_usd"] or 0.0)
    old_total_usd = float(spec_item["total_usd"] or 0.0)
    config_name = spec_item.get("display_name") or spec_item.get("auto_name") or f"#{item_id}"

    # Достаём build_result_json и находим нужный вариант.
    row = session.execute(
        text(
            "SELECT build_result_json FROM queries WHERE id = :qid"
        ),
        {"qid": int(spec_item["query_id"])},
    ).first()
    build_result = row.build_result_json if row else None
    variants = _prepare_variants(build_result) if build_result else []
    target_mfg = (spec_item["variant_manufacturer"] or "").lower()
    target = next(
        (v for v in variants if (v.get("manufacturer") or "").lower() == target_mfg),
        None,
    )
    if target is None:
        # Конфигурация была удалена/повреждена — пометим как недоступную.
        return RecalcDelta(
            spec_item_id=item_id,
            config_name=config_name,
            qty=qty,
            old_unit_usd=old_unit_usd,
            new_unit_usd=old_unit_usd,
            old_total_usd=old_total_usd,
            new_total_usd=old_total_usd,
            delta_pct=0.0,
            changed=False,
            status="unavailable",
            unavailable_components=["Конфигурация повреждена"],
        )

    # Курс берём тот же, что был при сохранении конфигурации,
    # чтобы пересчёт оставался стабильным независимо от текущего курса.
    usd_rub = float((build_result or {}).get("usd_rub_rate") or 90.0)

    # Считаем сумму по компонентам варианта. Учитываем quantity внутри
    # компонента (для RAM это число модулей).
    new_unit_usd = 0.0
    unavailable: list[str] = []
    for c in target.get("components") or {}:
        # components у _prepare_variants — dict[cat → component-dict]
        pass
    # _prepare_variants даёт components в виде dict[cat → comp]; нам же
    # нужен исходный список с количеством — берём напрямую из BuildResult.
    raw_components = []
    for v in (build_result or {}).get("variants") or []:
        if (v.get("manufacturer") or "").lower() == target_mfg:
            raw_components = v.get("components") or []
            break

    for c in raw_components:
        cat = c.get("category")
        cid = c.get("component_id")
        c_qty = int(c.get("quantity") or 1)
        if not cat or cid is None:
            continue
        price = _component_min_price_usd(
            session, category=cat, component_id=int(cid), usd_rub=usd_rub,
        )
        if price is None:
            unavailable.append(_component_label(c))
        else:
            new_unit_usd += price * c_qty

    new_unit_usd = round(new_unit_usd, 2)

    if unavailable:
        return RecalcDelta(
            spec_item_id=item_id,
            config_name=config_name,
            qty=qty,
            old_unit_usd=old_unit_usd,
            new_unit_usd=old_unit_usd,
            old_total_usd=old_total_usd,
            new_total_usd=old_total_usd,
            delta_pct=0.0,
            changed=False,
            status="unavailable",
            unavailable_components=unavailable,
        )

    new_total_usd = round(new_unit_usd * qty, 2)
    changed = abs(new_unit_usd - old_unit_usd) >= 0.01
    delta_pct = 0.0
    if old_unit_usd > 0.0001:
        delta_pct = round(((new_unit_usd - old_unit_usd) / old_unit_usd) * 100.0, 2)

    return RecalcDelta(
        spec_item_id=item_id,
        config_name=config_name,
        qty=qty,
        old_unit_usd=old_unit_usd,
        new_unit_usd=new_unit_usd,
        old_total_usd=old_total_usd,
        new_total_usd=new_total_usd,
        delta_pct=delta_pct,
        changed=changed,
        status="ok",
        unavailable_components=[],
    )


def _apply_delta(session: Session, delta: RecalcDelta, *, usd_rub: float) -> None:
    """Если позиция изменилась — обновляет unit/total + recalculated_at."""
    if not delta.changed or delta.status != "ok":
        return
    new_unit_rub = round(delta.new_unit_usd * usd_rub, 2)
    new_total_rub = round(delta.new_total_usd * usd_rub, 2)
    session.execute(
        text(
            "UPDATE specification_items "
            "SET unit_usd = :uu, unit_rub = :ur, "
            "    total_usd = :tu, total_rub = :tr, "
            "    recalculated_at = NOW(), updated_at = NOW() "
            "WHERE id = :id"
        ),
        {
            "id": delta.spec_item_id,
            "uu": delta.new_unit_usd, "ur": new_unit_rub,
            "tu": delta.new_total_usd, "tr": new_total_rub,
        },
    )


def _spec_item_with_query(session: Session, item_id: int) -> dict | None:
    """Чтение одной позиции с полем variant_manufacturer и query_id."""
    row = session.execute(
        text(
            "SELECT id, project_id, query_id, variant_manufacturer, quantity, "
            "       auto_name, custom_name, unit_usd, unit_rub, total_usd, total_rub "
            "FROM specification_items WHERE id = :id"
        ),
        {"id": int(item_id)},
    ).first()
    if row is None:
        return None
    return {
        "id":                   int(row.id),
        "project_id":           int(row.project_id),
        "query_id":             int(row.query_id),
        "variant_manufacturer": row.variant_manufacturer,
        "quantity":             int(row.quantity),
        "auto_name":            row.auto_name,
        "custom_name":          row.custom_name,
        "display_name":         row.custom_name or row.auto_name,
        "unit_usd":             float(row.unit_usd) if row.unit_usd is not None else 0.0,
        "unit_rub":             float(row.unit_rub) if row.unit_rub is not None else 0.0,
        "total_usd":            float(row.total_usd) if row.total_usd is not None else 0.0,
        "total_rub":            float(row.total_rub) if row.total_rub is not None else 0.0,
    }


def _usd_rate_for_query(session: Session, query_id: int) -> float:
    """Курс из build_result_json query."""
    row = session.execute(
        text("SELECT build_result_json FROM queries WHERE id = :qid"),
        {"qid": int(query_id)},
    ).first()
    if row is None or row.build_result_json is None:
        return 90.0
    return float((row.build_result_json or {}).get("usd_rub_rate") or 90.0)


def recalc_specification(
    session: Session,
    *,
    project_id: int,
) -> RecalcResult:
    """Пересчитывает все позиции спецификации проекта.
    Изменённые сразу пишутся в БД, в результате — список дельт по всем."""
    # Загружаем все позиции с минимально нужным набором полей.
    rows = session.execute(
        text(
            "SELECT id, project_id, query_id, variant_manufacturer, quantity, "
            "       auto_name, custom_name, unit_usd, unit_rub, total_usd, total_rub "
            "FROM specification_items "
            "WHERE project_id = :pid "
            "ORDER BY position ASC, id ASC"
        ),
        {"pid": int(project_id)},
    ).all()

    deltas: list[RecalcDelta] = []
    for r in rows:
        item = {
            "id":                   int(r.id),
            "project_id":           int(r.project_id),
            "query_id":             int(r.query_id),
            "variant_manufacturer": r.variant_manufacturer,
            "quantity":             int(r.quantity),
            "auto_name":            r.auto_name,
            "custom_name":          r.custom_name,
            "display_name":         r.custom_name or r.auto_name,
            "unit_usd":             float(r.unit_usd) if r.unit_usd is not None else 0.0,
            "unit_rub":             float(r.unit_rub) if r.unit_rub is not None else 0.0,
            "total_usd":            float(r.total_usd) if r.total_usd is not None else 0.0,
            "total_rub":            float(r.total_rub) if r.total_rub is not None else 0.0,
        }
        delta = _recalc_one_spec_item(session, spec_item=item)
        if delta.changed and delta.status == "ok":
            usd_rub = _usd_rate_for_query(session, item["query_id"])
            _apply_delta(session, delta, usd_rub=usd_rub)
        deltas.append(delta)

    # Один общий commit на весь пересчёт.
    if any(d.changed and d.status == "ok" for d in deltas):
        session.commit()

    changed = sum(1 for d in deltas if d.changed)
    return RecalcResult(items=deltas, changed_count=changed, total_count=len(deltas))


def recalc_specification_item(
    session: Session,
    *,
    item_id: int,
) -> RecalcDelta | None:
    """Пересчитывает одну позицию. Возвращает RecalcDelta или None,
    если позиции нет."""
    item = _spec_item_with_query(session, item_id)
    if item is None:
        return None
    delta = _recalc_one_spec_item(session, spec_item=item)
    if delta.changed and delta.status == "ok":
        usd_rub = _usd_rate_for_query(session, item["query_id"])
        _apply_delta(session, delta, usd_rub=usd_rub)
        session.commit()
    return delta


def delta_to_dict(delta: RecalcDelta) -> dict[str, Any]:
    """Сериализация дельты в JSON-совместимый dict для роутов."""
    return {
        "spec_item_id":            delta.spec_item_id,
        "config_name":             delta.config_name,
        "qty":                     delta.qty,
        "old_unit_usd":            delta.old_unit_usd,
        "new_unit_usd":            delta.new_unit_usd,
        "old_total_usd":           delta.old_total_usd,
        "new_total_usd":           delta.new_total_usd,
        "delta_pct":               delta.delta_pct,
        "changed":                 delta.changed,
        "status":                  delta.status,
        "unavailable_components":  delta.unavailable_components,
    }
