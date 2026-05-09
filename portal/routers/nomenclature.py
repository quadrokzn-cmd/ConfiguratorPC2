# Справочник «печать» (printer/mfu) в портале — этап 9a слияния.
#
# Полная замена `app/services/auctions/catalog/routes.py` (QT-овский
# роутер с `app.core.auth`/`app.core.templates`, который C-PC2 не имеет).
# Здесь — портальные паттерны: require_permission/CSRF/get_csrf_token/
# portal.templating.templates.
#
# Эндпоинты:
#   GET  /nomenclature                       — список SKU + фильтры (auctions).
#   POST /nomenclature/{id}/cost-base        — inline cost_base_rub
#                                              (auctions_edit_settings).
#   POST /nomenclature/{id}/attrs            — модалка атрибутов
#                                              (auctions_edit_settings).
#   POST /nomenclature/{id}/ktru             — мульти-список kdtru-кодов
#                                              (auctions_edit_settings).
#   POST /nomenclature/{id}/enrich           — pending в enrichment-очередь
#                                              (auctions_edit_settings).
#
# Все POST-ответы — JSON {ok, ...}, чтобы JS на странице сразу понимал
# результат без перезагрузки. Ошибки CSRF/прав/значений — стандартными
# HTTP-кодами 400/403/404 и {ok:false, detail:...}.

from __future__ import annotations

import json
import logging
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.services.auctions.catalog import service as catalog_service
from app.services.auctions.catalog.enrichment.exporter import export_manual_single
from app.services.auctions.catalog.enrichment.schema import (
    NA,
    PRINTER_MFU_ATTRS,
)
from portal.templating import templates
from shared.audit import extract_request_meta, write_audit
from shared.audit_actions import (
    ACTION_AUCTION_ENRICH_REQUEST,
    ACTION_AUCTION_NOMENCLATURE_EDIT,
)
from shared.auth import AuthUser, get_csrf_token, verify_csrf
from shared.db import get_db
from shared.permissions import require_permission


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/nomenclature")


def _safe_list(*, category: str | None, brand: str | None, search: str | None):
    """Безопасный список SKU. Если аукционных таблиц нет (test-БД без 030
    /031), возвращаем пустой список — страница рендерится с empty state."""
    try:
        return catalog_service.list_nomenclature(
            category=category, brand=brand, search=search,
        )
    except Exception as exc:
        logger.warning("nomenclature.list failed: %s: %s", type(exc).__name__, exc)
        return []


def _safe_brands() -> list[str]:
    try:
        return catalog_service.list_brands()
    except Exception:
        return []


def _safe_categories() -> list[str]:
    try:
        return catalog_service.list_categories()
    except Exception:
        return []


# ============================================================
# GET /nomenclature
# ============================================================

@router.get("")
def nomenclature_index(
    request: Request,
    category: str | None = None,
    brand: str | None = None,
    q: str | None = None,
    user: AuthUser = Depends(require_permission("auctions")),
):
    rows = _safe_list(category=category, brand=brand, search=q)
    brands = _safe_brands()
    categories = _safe_categories()

    return templates.TemplateResponse(
        request,
        "nomenclature/index.html",
        {
            "user":            user,
            "csrf_token":      get_csrf_token(request),
            "rows":            rows,
            "brands":          brands,
            "categories":      categories,
            "filter_category": category or "",
            "filter_brand":    brand or "",
            "filter_q":        q or "",
            "attr_keys":       list(PRINTER_MFU_ATTRS.keys()),
            "attr_types":      dict(PRINTER_MFU_ATTRS),
            "na":              NA,
        },
    )


# ============================================================
# POST /nomenclature/{id}/cost-base
# ============================================================

@router.post("/{nomenclature_id}/cost-base")
def cost_base_save(
    nomenclature_id: int,
    request: Request,
    cost_base_rub: str = Form(""),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_permission("auctions_edit_settings")),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    row = catalog_service.get_by_id(nomenclature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SKU не найден.")

    raw = (cost_base_rub or "").strip().replace(",", ".")
    value: Decimal | None = None
    if raw:
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise HTTPException(status_code=400, detail=f"Не число: {raw!r}")

    catalog_service.update_cost_base_manual(nomenclature_id, value)

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUCTION_NOMENCLATURE_EDIT,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="printers_mfu",
        target_id=nomenclature_id,
        payload={"field": "cost_base_rub", "value": str(value) if value is not None else None},
        ip=ip, user_agent=ua,
    )

    return JSONResponse({
        "ok":    True,
        "value": str(value) if value is not None else None,
    })


# ============================================================
# POST /nomenclature/{id}/attrs
# ============================================================

@router.post("/{nomenclature_id}/attrs")
async def attrs_save(
    nomenclature_id: int,
    request: Request,
    user: AuthUser = Depends(require_permission("auctions_edit_settings")),
):
    form = await request.form()
    csrf_token = form.get("csrf_token", "")
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    row = catalog_service.get_by_id(nomenclature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SKU не найден.")

    attrs: dict[str, Any] = {}
    for key in PRINTER_MFU_ATTRS:
        if key == "network_interface":
            values = form.getlist(key)
            cleaned = [v for v in values if v != NA]
            attrs[key] = cleaned if cleaned else NA
        else:
            raw = (form.get(key) or "").strip()
            if not raw or raw == NA:
                attrs[key] = NA
            elif key in ("print_speed_ppm", "resolution_dpi", "starter_cartridge_pages"):
                try:
                    attrs[key] = int(raw)
                except ValueError:
                    attrs[key] = NA
            else:
                attrs[key] = raw

    catalog_service.update_attrs_manual(nomenclature_id, attrs)

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUCTION_NOMENCLATURE_EDIT,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="printers_mfu",
        target_id=nomenclature_id,
        payload={"field": "attrs_jsonb", "n_keys": len(attrs)},
        ip=ip, user_agent=ua,
    )

    return JSONResponse({"ok": True, "attrs": attrs})


# ============================================================
# POST /nomenclature/{id}/ktru
# ============================================================

@router.post("/{nomenclature_id}/ktru")
async def ktru_save(
    nomenclature_id: int,
    request: Request,
    user: AuthUser = Depends(require_permission("auctions_edit_settings")),
):
    form = await request.form()
    csrf_token = form.get("csrf_token", "")
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    row = catalog_service.get_by_id(nomenclature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SKU не найден.")

    # Принимаем как textarea «code1, code2, code3», так и multi-input
    # name="ktru_codes". Оба варианта разруливаем здесь.
    raw_text = (form.get("ktru_codes_text") or "").strip()
    if raw_text:
        codes = [c.strip() for c in raw_text.replace("\n", ",").split(",") if c.strip()]
    else:
        codes = [c for c in form.getlist("ktru_codes") if c]

    catalog_service.update_ktru_codes(nomenclature_id, codes)

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUCTION_NOMENCLATURE_EDIT,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="printers_mfu",
        target_id=nomenclature_id,
        payload={"field": "ktru_codes_array", "codes": codes},
        ip=ip, user_agent=ua,
    )

    return JSONResponse({"ok": True, "codes": codes})


# ============================================================
# POST /nomenclature/{id}/enrich
# ============================================================

@router.post("/{nomenclature_id}/enrich")
def enrich_request(
    nomenclature_id: int,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_permission("auctions_edit_settings")),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    row = catalog_service.get_by_id(nomenclature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SKU не найден.")

    try:
        out_path: Path = export_manual_single(row["sku"])
    except Exception as exc:
        logger.warning("enrich export failed for sku=%s: %s", row["sku"], exc)
        raise HTTPException(
            status_code=500,
            detail=f"Не удалось положить в enrichment/pending/: {exc}",
        )

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUCTION_ENRICH_REQUEST,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="printers_mfu",
        target_id=nomenclature_id,
        payload={"sku": row["sku"], "file": out_path.name},
        ip=ip, user_agent=ua,
    )

    return JSONResponse({
        "ok":   True,
        "file": out_path.name,
        "msg":  f"в очереди: {out_path.name}",
    })
