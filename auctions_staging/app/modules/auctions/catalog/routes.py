"""HTTP-роуты справочника `/nomenclature` (Jinja + HTMX).

Эндпоинты:
- GET  /nomenclature                       — список SKU (фильтры/поиск)
- GET  /nomenclature/{id}/attrs/edit       — модальная форма правки атрибутов
- POST /nomenclature/{id}/attrs            — сохранить атрибуты вручную
- POST /nomenclature/{id}/cost-base        — inline-сохранение cost_base_rub
- POST /nomenclature/{id}/ktru             — мультиселект КТРУ-кодов
- POST /nomenclature/{id}/enrich           — добавить SKU в pending для Claude Code
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

from app.core.auth import require_user
from app.core.templates import templates
from app.modules.auctions.catalog import service
from app.modules.auctions.catalog.enrichment.exporter import export_manual_single
from app.modules.auctions.catalog.enrichment.schema import (
    NA,
    PRINTER_MFU_ATTRS,
)

router = APIRouter()


@router.get("/nomenclature", response_class=HTMLResponse)
def nomenclature_page(
    request: Request,
    category: str | None = None,
    brand: str | None = None,
    q: str | None = None,
    username: str = Depends(require_user),
):
    rows = service.list_nomenclature(category=category, brand=brand, search=q)
    return templates.TemplateResponse(
        "nomenclature.html",
        {
            "request":    request,
            "username":   username,
            "active":     "nomenclature",
            "rows":       rows,
            "brands":     service.list_brands(),
            "categories": service.list_categories(),
            "attr_keys":  list(PRINTER_MFU_ATTRS.keys()),
            "ktru_active": service.list_ktru_active(),
            "filter_category": category or "",
            "filter_brand":    brand or "",
            "filter_q":        q or "",
            "na":              NA,
        },
    )


@router.get("/nomenclature/{nomenclature_id}/attrs/edit", response_class=HTMLResponse)
def attrs_edit_form(
    nomenclature_id: int,
    request: Request,
    _: str = Depends(require_user),
):
    row = service.get_by_id(nomenclature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SKU не найден")
    return templates.TemplateResponse(
        "_attrs_edit_form.html",
        {
            "request":   request,
            "row":       row,
            "attrs":     row.get("attrs_jsonb") or {},
            "attr_keys": list(PRINTER_MFU_ATTRS.keys()),
            "attr_types": dict(PRINTER_MFU_ATTRS),
            "na":        NA,
        },
    )


@router.post("/nomenclature/{nomenclature_id}/attrs", response_class=HTMLResponse)
async def attrs_save(
    nomenclature_id: int,
    request: Request,
    _: str = Depends(require_user),
):
    row = service.get_by_id(nomenclature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SKU не найден")

    form = await request.form()
    attrs: dict = {}
    for key in PRINTER_MFU_ATTRS:
        # network_interface — список (несколько чекбоксов с одним именем).
        if key == "network_interface":
            values = form.getlist(key)
            if not values or values == [NA]:
                attrs[key] = NA
            else:
                attrs[key] = [v for v in values if v != NA]
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

    service.update_attrs_manual(nomenclature_id, attrs)
    # Возвращаем обновлённую строку (HTMX подставит её на место).
    rows = service.list_nomenclature()
    updated = next((r for r in rows if r["id"] == nomenclature_id), None)
    if updated is None:
        raise HTTPException(status_code=404, detail="SKU не найден после сохранения")
    return templates.TemplateResponse(
        "_nomenclature_row.html",
        {
            "request":   request,
            "row":       updated,
            "attr_keys": list(PRINTER_MFU_ATTRS.keys()),
            "na":        NA,
        },
    )


@router.post("/nomenclature/{nomenclature_id}/cost-base", response_class=HTMLResponse)
async def cost_base_save(
    nomenclature_id: int,
    request: Request,
    cost_base_rub: str = Form(""),
    _: str = Depends(require_user),
):
    row = service.get_by_id(nomenclature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SKU не найден")

    raw = (cost_base_rub or "").strip().replace(",", ".")
    value = None
    if raw:
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise HTTPException(status_code=400, detail=f"Не число: {raw}")

    service.update_cost_base_manual(nomenclature_id, value)
    return PlainTextResponse(f"{value if value is not None else '—'}")


@router.post("/nomenclature/{nomenclature_id}/ktru", response_class=HTMLResponse)
async def ktru_save(
    nomenclature_id: int,
    request: Request,
    _: str = Depends(require_user),
):
    row = service.get_by_id(nomenclature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SKU не найден")

    form = await request.form()
    codes = form.getlist("ktru_codes")
    service.update_ktru_codes(nomenclature_id, codes)
    return PlainTextResponse(", ".join(codes) if codes else "—")


@router.post("/nomenclature/{nomenclature_id}/enrich", response_class=HTMLResponse)
def enrich_single(
    nomenclature_id: int,
    _: str = Depends(require_user),
):
    row = service.get_by_id(nomenclature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SKU не найден")
    out_path = export_manual_single(row["sku"])
    return PlainTextResponse(
        f"отправлено в очередь: {out_path.name}. оркестратор запустит Claude Code."
    )
