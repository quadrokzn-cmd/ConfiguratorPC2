# Пересчёт цен (этап 9А.2.1) и полный пересбор спецификации (этап 9А.2.3).
#
# В этом файле живут две родственные функции:
#
#   recalc_*    — старая логика price-only refresh: тот же набор
#                 component_id у выбранного варианта, обновляем
#                 минимальные актуальные цены через supplier_prices.
#                 Используется тестами этапа 9А.2.1 и оставлена как
#                 backwards-compat для возможных скриптов миграции.
#
#   reoptimize_* — новая логика (этап 9А.2.3): берём
#                 parsed_query_snapshot (BuildRequest, который был у
#                 первоначального подбора) и заново вызываем
#                 builder.build_config(req). Состав конфигурации может
#                 смениться, если у поставщиков появилось более
#                 выгодное оборудование. До записи нового билда
#                 сохраняем старый в previous_build_result_json
#                 для отката.
#
# UI этапа 9А.2.3 использует только reoptimize_*. Старые маршруты
# /spec/recalc и /spec/{item_id}/recalc остаются алиасами на reoptimize
# (см. project_router.py) — закладки менеджеров не сломаются.

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from portal.routers.configurator.main import _prepare_variants
from portal.services.configurator.engine import build_config
from portal.services.configurator.engine.schema import (
    BuildResult,
    request_from_dict,
    result_to_dict,
)

logger = logging.getLogger(__name__)


# Категории, которые мы умеем обрабатывать. Подписи нужны для UI-diff'а.
_CATEGORY_LABELS: dict[str, str] = {
    "cpu":         "CPU",
    "motherboard": "Материнская плата",
    "ram":         "Оперативная память",
    "gpu":         "Видеокарта",
    "storage":     "Накопитель",
    "case":        "Корпус",
    "psu":         "Блок питания",
    "cooler":      "Кулер",
}
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


# =====================================================================
# Структуры результата (общие для recalc и reoptimize)
# =====================================================================

@dataclass
class ComponentChange:
    """Что изменилось по одному компоненту между «было» и «стало»."""
    category: str
    category_label: str
    old_brand_model: str | None = None
    new_brand_model: str | None = None
    old_supplier: str | None = None
    new_supplier: str | None = None
    old_usd: float = 0.0
    new_usd: float = 0.0


@dataclass
class RecalcDelta:
    """Результат пересчёта/пересбора одной позиции спецификации.

    Поле `status` принимает значения:
      - 'ok'           — старое поведение recalc; legacy-совместимость с тестами 9А.2.1.
      - 'reoptimized'  — новое поведение reoptimize: состав изменился.
      - 'no_changes'   — reoptimize прошёл, но ничего не изменилось.
      - 'unavailable'  — пересбор/пересчёт невозможен.
    Поле `changed` — для совместимости со старыми тестами (etap 9А.2.1).
    """
    spec_item_id: int
    config_name: str
    qty: int
    old_unit_usd: float
    new_unit_usd: float
    old_total_usd: float
    new_total_usd: float
    delta_pct: float
    changed: bool = False
    status: str = "ok"
    unavailable_components: list[str] = field(default_factory=list)
    # 9А.2.3:
    changed_components: list[ComponentChange] = field(default_factory=list)
    unavailable_reason: str = ""


@dataclass
class RecalcResult:
    """Сводный результат по всем позициям спецификации."""
    items: list[RecalcDelta]
    changed_count: int
    total_count: int


# Алиасы под новые имена (этап 9А.2.3).
ReoptimizeDelta = RecalcDelta
ReoptimizeResult = RecalcResult


# =====================================================================
# СТАРАЯ price-only логика (этап 9А.2.1) — recalc_*
# =====================================================================

def _component_min_price_usd(
    session: Session,
    *,
    category: str,
    component_id: int,
    usd_rub: float,
) -> float | None:
    """Минимальная актуальная цена компонента в USD среди активных
    поставщиков с stock>0, где компонент не is_hidden. None — если нет
    кандидатов."""
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
    cat = c.get("category") or "?"
    model = c.get("model") or f"id={c.get('component_id')}"
    return f"{cat}: {model}"


def _recalc_one_spec_item(
    session: Session,
    *,
    spec_item: dict,
) -> RecalcDelta:
    """Пересчёт price-only для одной позиции, БЕЗ записи в БД."""
    item_id = spec_item["id"]
    qty = int(spec_item["quantity"])
    old_unit_usd = float(spec_item["unit_usd"] or 0.0)
    old_total_usd = float(spec_item["total_usd"] or 0.0)
    config_name = spec_item.get("display_name") or spec_item.get("auto_name") or f"#{item_id}"

    row = session.execute(
        text("SELECT build_result_json FROM queries WHERE id = :qid"),
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
        return RecalcDelta(
            spec_item_id=item_id, config_name=config_name, qty=qty,
            old_unit_usd=old_unit_usd, new_unit_usd=old_unit_usd,
            old_total_usd=old_total_usd, new_total_usd=old_total_usd,
            delta_pct=0.0, changed=False, status="unavailable",
            unavailable_components=["Конфигурация повреждена"],
        )

    usd_rub = float((build_result or {}).get("usd_rub_rate") or 90.0)

    raw_components: list[dict] = []
    for v in (build_result or {}).get("variants") or []:
        if (v.get("manufacturer") or "").lower() == target_mfg:
            raw_components = v.get("components") or []
            break

    new_unit_usd = 0.0
    unavailable: list[str] = []
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
            spec_item_id=item_id, config_name=config_name, qty=qty,
            old_unit_usd=old_unit_usd, new_unit_usd=old_unit_usd,
            old_total_usd=old_total_usd, new_total_usd=old_total_usd,
            delta_pct=0.0, changed=False, status="unavailable",
            unavailable_components=unavailable,
        )

    new_total_usd = round(new_unit_usd * qty, 2)
    changed = abs(new_unit_usd - old_unit_usd) >= 0.01
    delta_pct = 0.0
    if old_unit_usd > 0.0001:
        delta_pct = round(((new_unit_usd - old_unit_usd) / old_unit_usd) * 100.0, 2)

    return RecalcDelta(
        spec_item_id=item_id, config_name=config_name, qty=qty,
        old_unit_usd=old_unit_usd, new_unit_usd=new_unit_usd,
        old_total_usd=old_total_usd, new_total_usd=new_total_usd,
        delta_pct=delta_pct, changed=changed, status="ok",
        unavailable_components=[],
    )


def _apply_recalc_delta(session: Session, delta: RecalcDelta, *, usd_rub: float) -> None:
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
    row = session.execute(
        text(
            "SELECT id, project_id, query_id, variant_manufacturer, quantity, "
            "       auto_name, custom_name, unit_usd, unit_rub, total_usd, total_rub, "
            "       parsed_query_snapshot "
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
        "parsed_query_snapshot": row.parsed_query_snapshot,
    }


def _usd_rate_for_query(session: Session, query_id: int) -> float:
    row = session.execute(
        text("SELECT build_result_json FROM queries WHERE id = :qid"),
        {"qid": int(query_id)},
    ).first()
    if row is None or row.build_result_json is None:
        return 90.0
    return float((row.build_result_json or {}).get("usd_rub_rate") or 90.0)


def recalc_specification(session: Session, *, project_id: int) -> RecalcResult:
    """Старая логика price-only refresh для всей спецификации.

    UI этапа 9А.2.3 эту функцию не вызывает — есть reoptimize_specification.
    Сохранена для совместимости и для прямых вызовов из скриптов миграции."""
    rows = session.execute(
        text(
            "SELECT id, project_id, query_id, variant_manufacturer, quantity, "
            "       auto_name, custom_name, unit_usd, unit_rub, total_usd, total_rub, "
            "       parsed_query_snapshot "
            "FROM specification_items "
            "WHERE project_id = :pid "
            "ORDER BY position ASC, id ASC"
        ),
        {"pid": int(project_id)},
    ).all()

    deltas: list[RecalcDelta] = []
    for r in rows:
        item = {
            "id": int(r.id), "project_id": int(r.project_id),
            "query_id": int(r.query_id),
            "variant_manufacturer": r.variant_manufacturer,
            "quantity": int(r.quantity),
            "auto_name": r.auto_name, "custom_name": r.custom_name,
            "display_name": r.custom_name or r.auto_name,
            "unit_usd": float(r.unit_usd) if r.unit_usd is not None else 0.0,
            "unit_rub": float(r.unit_rub) if r.unit_rub is not None else 0.0,
            "total_usd": float(r.total_usd) if r.total_usd is not None else 0.0,
            "total_rub": float(r.total_rub) if r.total_rub is not None else 0.0,
            "parsed_query_snapshot": r.parsed_query_snapshot,
        }
        delta = _recalc_one_spec_item(session, spec_item=item)
        if delta.changed and delta.status == "ok":
            usd_rub = _usd_rate_for_query(session, item["query_id"])
            _apply_recalc_delta(session, delta, usd_rub=usd_rub)
        deltas.append(delta)

    if any(d.changed and d.status == "ok" for d in deltas):
        session.commit()

    changed = sum(1 for d in deltas if d.changed)
    return RecalcResult(items=deltas, changed_count=changed, total_count=len(deltas))


def recalc_specification_item(session: Session, *, item_id: int) -> RecalcDelta | None:
    """Точечный price-only refresh одной позиции."""
    item = _spec_item_with_query(session, item_id)
    if item is None:
        return None
    delta = _recalc_one_spec_item(session, spec_item=item)
    if delta.changed and delta.status == "ok":
        usd_rub = _usd_rate_for_query(session, item["query_id"])
        _apply_recalc_delta(session, delta, usd_rub=usd_rub)
        session.commit()
    return delta


# =====================================================================
# НОВАЯ reoptimize-логика (этап 9А.2.3)
# =====================================================================

def _component_brand_model(c: dict) -> str:
    parts = []
    mfg = c.get("manufacturer") or ""
    model = c.get("model") or ""
    if mfg:
        parts.append(str(mfg))
    if model:
        parts.append(str(model))
    return " ".join(parts) or "—"


def _components_to_dict(components: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for c in components or []:
        cat = c.get("category")
        if cat:
            out[cat] = c
    return out


def _build_changes(old_variant: dict, new_variant: dict) -> list[ComponentChange]:
    """Сравнивает компоненты двух вариантов (по category) и возвращает дельту."""
    old_map = _components_to_dict(old_variant.get("components") or [])
    new_map = _components_to_dict(new_variant.get("components") or [])
    cats = set(old_map.keys()) | set(new_map.keys())
    cat_order = list(_CATEGORY_LABELS.keys())
    sorted_cats = sorted(cats, key=lambda c: cat_order.index(c) if c in cat_order else 99)
    changes: list[ComponentChange] = []
    for cat in sorted_cats:
        oc = old_map.get(cat) or {}
        nc = new_map.get(cat) or {}
        old_id = oc.get("component_id")
        new_id = nc.get("component_id")
        old_supplier = oc.get("supplier")
        new_supplier = nc.get("supplier")
        old_usd = float(oc.get("price_usd") or 0.0) * int(oc.get("quantity") or 1)
        new_usd = float(nc.get("price_usd") or 0.0) * int(nc.get("quantity") or 1)
        if (
            old_id != new_id
            or old_supplier != new_supplier
            or abs(old_usd - new_usd) >= 0.01
        ):
            changes.append(ComponentChange(
                category=cat,
                category_label=_CATEGORY_LABELS.get(cat, cat),
                old_brand_model=_component_brand_model(oc) if oc else None,
                new_brand_model=_component_brand_model(nc) if nc else None,
                old_supplier=old_supplier,
                new_supplier=new_supplier,
                old_usd=round(old_usd, 2),
                new_usd=round(new_usd, 2),
            ))
    return changes


def _variant_dict_from_result(result: BuildResult, manufacturer: str) -> dict | None:
    serialised = result_to_dict(result)
    target = (manufacturer or "").lower()
    for v in serialised.get("variants") or []:
        if (v.get("manufacturer") or "").lower() == target:
            return v
    return None


def _spec_item_query_data(session: Session, spec_item: dict) -> tuple[Any, Any]:
    """Возвращает (parsed_query_snapshot, build_result_json) для spec_item."""
    parsed_snap = spec_item.get("parsed_query_snapshot")
    qid = int(spec_item["query_id"])
    row = session.execute(
        text(
            "SELECT build_result_json, build_request_json "
            "FROM queries WHERE id = :qid"
        ),
        {"qid": qid},
    ).first()
    build_result_json = row.build_result_json if row else None
    if parsed_snap is None and row is not None:
        parsed_snap = row.build_request_json
    return parsed_snap, build_result_json


def _reoptimize_one(
    session: Session, *, spec_item: dict,
) -> tuple[RecalcDelta, dict | None, dict | None]:
    """Прогон reoptimize для одной позиции. БД не пишет.

    Возвращает (delta, old_variant_dict, new_variant_dict)."""
    item_id = int(spec_item["id"])
    qty = int(spec_item["quantity"])
    old_unit_usd = float(spec_item["unit_usd"] or 0.0)
    old_total_usd = float(spec_item["total_usd"] or 0.0)
    config_name = (
        spec_item.get("display_name")
        or spec_item.get("auto_name")
        or f"#{item_id}"
    )
    target_mfg = (spec_item["variant_manufacturer"] or "").lower()

    parsed_snap, build_result_json = _spec_item_query_data(session, spec_item)

    old_variant: dict | None = None
    if build_result_json:
        for v in (build_result_json or {}).get("variants") or []:
            if (v.get("manufacturer") or "").lower() == target_mfg:
                old_variant = v
                break

    if not parsed_snap:
        return (
            RecalcDelta(
                spec_item_id=item_id, config_name=config_name, qty=qty,
                old_unit_usd=old_unit_usd, new_unit_usd=old_unit_usd,
                old_total_usd=old_total_usd, new_total_usd=old_total_usd,
                delta_pct=0.0, status="unavailable", changed=False,
                unavailable_reason=(
                    "У этой позиции нет сохранённых параметров запроса — "
                    "вероятно, конфигурация была собрана до этапа 9А.2.3. "
                    "Удалите и добавьте её заново через новый запрос."
                ),
                unavailable_components=["parsed_query_snapshot отсутствует"],
            ),
            old_variant, None,
        )

    try:
        if isinstance(parsed_snap, str):
            parsed_dict = json.loads(parsed_snap)
        else:
            parsed_dict = dict(parsed_snap)
        req = request_from_dict(parsed_dict)
    except Exception as exc:
        logger.warning("reoptimize: не удалось распарсить parsed_query_snapshot: %s", exc)
        return (
            RecalcDelta(
                spec_item_id=item_id, config_name=config_name, qty=qty,
                old_unit_usd=old_unit_usd, new_unit_usd=old_unit_usd,
                old_total_usd=old_total_usd, new_total_usd=old_total_usd,
                delta_pct=0.0, status="unavailable", changed=False,
                unavailable_reason=f"Не удалось разобрать сохранённый запрос ({type(exc).__name__})",
                unavailable_components=[type(exc).__name__],
            ),
            old_variant, None,
        )

    try:
        result = build_config(req)
    except Exception as exc:
        logger.exception("reoptimize: build_config упал: %s", exc)
        return (
            RecalcDelta(
                spec_item_id=item_id, config_name=config_name, qty=qty,
                old_unit_usd=old_unit_usd, new_unit_usd=old_unit_usd,
                old_total_usd=old_total_usd, new_total_usd=old_total_usd,
                delta_pct=0.0, status="unavailable", changed=False,
                unavailable_reason=f"Внутренняя ошибка подбора: {type(exc).__name__}",
                unavailable_components=[type(exc).__name__],
            ),
            old_variant, None,
        )

    new_variant_dict = _variant_dict_from_result(result, target_mfg)
    if new_variant_dict is None:
        return (
            RecalcDelta(
                spec_item_id=item_id, config_name=config_name, qty=qty,
                old_unit_usd=old_unit_usd, new_unit_usd=old_unit_usd,
                old_total_usd=old_total_usd, new_total_usd=old_total_usd,
                delta_pct=0.0, status="unavailable", changed=False,
                unavailable_reason=(
                    "Подбор не нашёл совместимых компонентов для этой "
                    f"конфигурации ({target_mfg.upper()}). Возможно, "
                    "у поставщиков нет нужных позиций в наличии."
                ),
                unavailable_components=["no compatible build"],
            ),
            old_variant, None,
        )

    new_unit_usd = round(float(new_variant_dict.get("total_usd") or 0.0), 2)
    new_total_usd = round(new_unit_usd * qty, 2)
    changes = _build_changes(old_variant or {}, new_variant_dict)

    if not changes:
        return (
            RecalcDelta(
                spec_item_id=item_id, config_name=config_name, qty=qty,
                old_unit_usd=old_unit_usd, new_unit_usd=new_unit_usd,
                old_total_usd=old_total_usd, new_total_usd=new_total_usd,
                delta_pct=0.0, status="no_changes", changed=False,
            ),
            old_variant, new_variant_dict,
        )

    delta_pct = 0.0
    if old_unit_usd > 0.0001:
        delta_pct = round(((new_unit_usd - old_unit_usd) / old_unit_usd) * 100.0, 2)

    return (
        RecalcDelta(
            spec_item_id=item_id, config_name=config_name, qty=qty,
            old_unit_usd=old_unit_usd, new_unit_usd=new_unit_usd,
            old_total_usd=old_total_usd, new_total_usd=new_total_usd,
            delta_pct=delta_pct, status="reoptimized", changed=True,
            changed_components=changes,
        ),
        old_variant, new_variant_dict,
    )


def _apply_reoptimize_delta(
    session: Session,
    *,
    delta: RecalcDelta,
    old_variant: dict | None,
    new_variant: dict | None,
) -> None:
    """Применяет результат reoptimize в БД для одной позиции."""
    if delta.status != "reoptimized" or new_variant is None:
        return

    item_id = delta.spec_item_id
    session.execute(
        text(
            "UPDATE specification_items "
            "SET previous_build_result_json = CAST(:prev AS JSONB), "
            "    previous_unit_usd  = :prev_unit, "
            "    previous_total_usd = :prev_total, "
            "    unit_usd  = :nu, "
            "    total_usd = :nt, "
            "    reoptimized_at = NOW(), "
            "    updated_at     = NOW() "
            "WHERE id = :id"
        ),
        {
            "id":         item_id,
            "prev":       json.dumps(old_variant) if old_variant else None,
            "prev_unit":  delta.old_unit_usd,
            "prev_total": delta.old_total_usd,
            "nu":         delta.new_unit_usd,
            "nt":         delta.new_total_usd,
        },
    )

    sm = session.execute(
        text(
            "SELECT s.variant_manufacturer, s.query_id, q.build_result_json "
            "FROM specification_items s JOIN queries q ON q.id = s.query_id "
            "WHERE s.id = :id"
        ),
        {"id": item_id},
    ).first()
    if sm is None or sm.build_result_json is None:
        return
    br = dict(sm.build_result_json)
    target_mfg = (sm.variant_manufacturer or "").lower()
    variants = list(br.get("variants") or [])
    found = False
    for i, v in enumerate(variants):
        if (v.get("manufacturer") or "").lower() == target_mfg:
            variants[i] = new_variant
            found = True
            break
    if not found:
        variants.append(new_variant)
    br["variants"] = variants
    session.execute(
        text(
            "UPDATE queries SET build_result_json = CAST(:br AS JSONB) "
            "WHERE id = :qid"
        ),
        {"qid": int(sm.query_id), "br": json.dumps(br)},
    )


def reoptimize_specification(session: Session, *, project_id: int) -> RecalcResult:
    """Reoptimize всех позиций спецификации проекта."""
    rows = session.execute(
        text(
            "SELECT id, project_id, query_id, variant_manufacturer, quantity, "
            "       auto_name, custom_name, unit_usd, total_usd, "
            "       parsed_query_snapshot "
            "FROM specification_items "
            "WHERE project_id = :pid "
            "ORDER BY position ASC, id ASC"
        ),
        {"pid": int(project_id)},
    ).all()

    deltas: list[RecalcDelta] = []
    changed = 0
    for r in rows:
        item = {
            "id":                     int(r.id),
            "project_id":             int(r.project_id),
            "query_id":               int(r.query_id),
            "variant_manufacturer":   r.variant_manufacturer,
            "quantity":               int(r.quantity),
            "auto_name":              r.auto_name,
            "custom_name":            r.custom_name,
            "display_name":           r.custom_name or r.auto_name,
            "unit_usd":               float(r.unit_usd) if r.unit_usd is not None else 0.0,
            "total_usd":              float(r.total_usd) if r.total_usd is not None else 0.0,
            "parsed_query_snapshot":  r.parsed_query_snapshot,
        }
        delta, old_variant, new_variant = _reoptimize_one(session, spec_item=item)
        if delta.status == "reoptimized":
            _apply_reoptimize_delta(
                session, delta=delta,
                old_variant=old_variant, new_variant=new_variant,
            )
            changed += 1
        deltas.append(delta)

    if changed:
        session.commit()

    return RecalcResult(items=deltas, changed_count=changed, total_count=len(deltas))


def reoptimize_specification_item(session: Session, *, item_id: int) -> RecalcDelta | None:
    """Reoptimize одной позиции."""
    item = _spec_item_with_query(session, item_id)
    if item is None:
        return None
    delta, old_variant, new_variant = _reoptimize_one(session, spec_item=item)
    if delta.status == "reoptimized":
        _apply_reoptimize_delta(
            session, delta=delta,
            old_variant=old_variant, new_variant=new_variant,
        )
        session.commit()
    return delta


def rollback_specification_item(session: Session, *, item_id: int) -> bool:
    """Откатывает последний reoptimize для одной позиции.

    Возвращает True, если что-то откатили; False — если откатывать нечего.
    """
    row = session.execute(
        text(
            "SELECT id, query_id, variant_manufacturer, quantity, "
            "       previous_build_result_json, previous_unit_usd, "
            "       previous_total_usd "
            "FROM specification_items WHERE id = :id"
        ),
        {"id": int(item_id)},
    ).first()
    if row is None or row.previous_build_result_json is None:
        return False

    qty = int(row.quantity)
    prev_unit = float(row.previous_unit_usd) if row.previous_unit_usd is not None else 0.0
    prev_total = (
        float(row.previous_total_usd)
        if row.previous_total_usd is not None
        else round(prev_unit * qty, 2)
    )

    # Восстанавливаем variant в queries.build_result_json.
    qrow = session.execute(
        text("SELECT id, build_result_json FROM queries WHERE id = :qid"),
        {"qid": int(row.query_id)},
    ).first()
    if qrow is not None and qrow.build_result_json is not None:
        br = dict(qrow.build_result_json)
        target_mfg = (row.variant_manufacturer or "").lower()
        if isinstance(row.previous_build_result_json, dict):
            prev_variant = dict(row.previous_build_result_json)
        else:
            prev_variant = json.loads(row.previous_build_result_json)
        variants = list(br.get("variants") or [])
        replaced = False
        for i, v in enumerate(variants):
            if (v.get("manufacturer") or "").lower() == target_mfg:
                variants[i] = prev_variant
                replaced = True
                break
        if not replaced:
            variants.append(prev_variant)
        br["variants"] = variants
        session.execute(
            text(
                "UPDATE queries SET build_result_json = CAST(:br AS JSONB) "
                "WHERE id = :qid"
            ),
            {"qid": int(qrow.id), "br": json.dumps(br)},
        )

    session.execute(
        text(
            "UPDATE specification_items SET "
            "  unit_usd  = :uu, "
            "  total_usd = :tu, "
            "  previous_build_result_json = NULL, "
            "  previous_unit_usd  = NULL, "
            "  previous_total_usd = NULL, "
            "  reoptimized_at     = NULL, "
            "  updated_at = NOW() "
            "WHERE id = :id"
        ),
        {"id": int(row.id), "uu": prev_unit, "tu": prev_total},
    )
    session.commit()
    return True


def rollback_specification(session: Session, *, project_id: int) -> int:
    rows = session.execute(
        text(
            "SELECT id FROM specification_items "
            "WHERE project_id = :pid AND previous_build_result_json IS NOT NULL"
        ),
        {"pid": int(project_id)},
    ).all()
    cnt = 0
    for r in rows:
        if rollback_specification_item(session, item_id=int(r.id)):
            cnt += 1
    return cnt


# =====================================================================
# Сериализация
# =====================================================================

def delta_to_dict(delta: RecalcDelta) -> dict[str, Any]:
    """Сериализация дельты в JSON-совместимый dict для роутов и тестов."""
    return {
        "spec_item_id":           delta.spec_item_id,
        "config_name":            delta.config_name,
        "qty":                    delta.qty,
        "old_unit_usd":           delta.old_unit_usd,
        "new_unit_usd":           delta.new_unit_usd,
        "old_total_usd":          delta.old_total_usd,
        "new_total_usd":          delta.new_total_usd,
        "delta_pct":              delta.delta_pct,
        "changed":                delta.changed,
        "status":                 delta.status,
        "unavailable_components": list(delta.unavailable_components),
        "unavailable_reason":     delta.unavailable_reason,
        "changed_components":     [asdict(c) for c in delta.changed_components],
    }
