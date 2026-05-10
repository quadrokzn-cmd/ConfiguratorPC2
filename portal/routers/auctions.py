# Роуты модуля «Аукционы» в портале (этап 9a слияния).
#
# /auctions               — inbox (список лотов с секциями).
# /auctions/settings      — настройки порогов/стоп-листа/KTRU.
# /auctions/{reg_number}  — карточка одного лота (объявление + позиции).
# POST /auctions/{reg_number}/{status,contract,note}  — мутации tender_status.
# POST /auctions/settings/...                          — мутации settings/regions/ktru.
#
# Доступ:
#   - GET-ы — require_permission('auctions').
#   - POST-ы статуса/контракта/заметки — require_permission('auctions_edit_status').
#   - POST-ы settings — require_permission('auctions_edit_settings').
#
# CSRF: hidden input csrf_token + verify_csrf на каждом POST.

from __future__ import annotations

import json
import logging
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from portal.services import auctions_service as svc
from portal.templating import templates
from shared.audit import extract_request_meta, write_audit
from shared.audit_actions import (
    ACTION_AUCTION_CONTRACT_UPDATE,
    ACTION_AUCTION_KTRU_ADD,
    ACTION_AUCTION_KTRU_TOGGLE,
    ACTION_AUCTION_NOTE_UPDATE,
    ACTION_AUCTION_REGION_TOGGLE,
    ACTION_AUCTION_SETTINGS_UPDATE,
    ACTION_AUCTION_STATUS_CHANGE,
)
from shared.auth import AuthUser, get_csrf_token, verify_csrf
from shared.db import get_db
from shared.permissions import require_permission


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/auctions")


# Все статусы для multi-checkbox фильтра.
_ALL_STATUSES: tuple[str, ...] = (
    "new", "in_review", "will_bid", "submitted", "won", "skipped",
)


# Ключевые даты контракта — фиксированный набор ключей в jsonb.
_CONTRACT_DATE_KEYS: tuple[str, ...] = (
    "signed_at", "delivery_at", "acceptance_at", "payment_at",
)


def _parse_decimal(raw: str | None) -> Decimal | None:
    if not raw:
        return None
    s = str(raw).strip().replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except (InvalidOperation, TypeError):
        return None


def _parse_int_or_none(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return None


# ============================================================
# GET /auctions — inbox
# ============================================================

@router.get("")
def auctions_inbox(
    request: Request,
    user: AuthUser = Depends(require_permission("auctions")),
    db: Session = Depends(get_db),
):
    # Парсим фильтры из query string. Все опциональны.
    qp = request.query_params
    statuses = tuple(s for s in qp.getlist("status") if s in _ALL_STATUSES)
    nmck_min = _parse_decimal(qp.get("nmck_min"))
    nmck_max = _parse_decimal(qp.get("nmck_max"))
    search = (qp.get("q") or "").strip() or None
    urgent_only = qp.get("urgent_only", "") in ("1", "on", "true")

    filters = svc.InboxFilters(
        statuses=statuses,
        nmck_min=nmck_min,
        nmck_max=nmck_max,
        search=search,
        urgent_only=urgent_only,
    )

    margin_threshold = svc.read_setting_int(db, "margin_threshold_pct", 15)
    deadline_alert = svc.read_setting_int(db, "deadline_alert_hours", 24)

    data = svc.get_inbox_data(
        db,
        filters=filters,
        deadline_alert_hours=deadline_alert,
        margin_threshold_pct=margin_threshold,
    )

    flash_error = request.session.pop("flash_error", None)
    flash_info = request.session.pop("flash_info", None)

    return templates.TemplateResponse(
        request,
        "auctions/inbox.html",
        {
            "user":                 user,
            "csrf_token":           get_csrf_token(request),
            "data":                 data,
            "all_statuses":         _ALL_STATUSES,
            "status_labels":        svc.STATUS_LABELS,
            "filters":              {
                "statuses":    list(statuses),
                "nmck_min":    nmck_min,
                "nmck_max":    nmck_max,
                "search":      search or "",
                "urgent_only": urgent_only,
            },
            "fmt_msk":              svc.format_msk_dt,
            "info":                 flash_info,
            "error":                flash_error,
            # has_permission уже зарегистрирован глобально в templating.py.
        },
    )


# ============================================================
# GET /auctions/settings — настройки модуля
# (объявлено ДО /{reg_number}, чтобы не конфликтовать со wildcard)
# ============================================================

@router.get("/settings")
def auctions_settings_page(
    request: Request,
    user: AuthUser = Depends(require_permission("auctions_edit_settings")),
    db: Session = Depends(get_db),
):
    settings_dict = svc.read_settings(db)
    regions = svc.list_excluded_regions(db)
    watchlist = svc.list_ktru_watchlist(db)

    flash_error = request.session.pop("flash_error", None)
    flash_info = request.session.pop("flash_info", None)

    return templates.TemplateResponse(
        request,
        "auctions/settings.html",
        {
            "user":         user,
            "csrf_token":   get_csrf_token(request),
            "settings":     settings_dict,
            "regions":      regions,
            "watchlist":    watchlist,
            "info":         flash_info,
            "error":        flash_error,
        },
    )


@router.post("/settings/save")
def auctions_settings_save(
    request: Request,
    margin_threshold_pct: str = Form(""),
    nmck_min_rub: str = Form(""),
    max_price_per_unit_rub: str = Form(""),
    deadline_alert_hours: str = Form(""),
    contract_reminder_days: str = Form(""),
    auctions_ingest_enabled: str = Form(""),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_permission("auctions_edit_settings")),
    db: Session = Depends(get_db),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    # Поля числовых порогов — сохраняем как есть; если ввод пустой/невалидный —
    # пропускаем (не затирая текущее значение).
    payload: dict[str, str] = {}
    for key, raw in (
        ("margin_threshold_pct",   margin_threshold_pct),
        ("nmck_min_rub",           nmck_min_rub),
        ("max_price_per_unit_rub", max_price_per_unit_rub),
        ("deadline_alert_hours",   deadline_alert_hours),
        ("contract_reminder_days", contract_reminder_days),
    ):
        val = (raw or "").strip()
        if val and _parse_int_or_none(val) is not None:
            svc.save_setting(db, key, val, updated_by=user.login)
            payload[key] = val

    # Тумблер ингеста — чекбокс приходит как 'on' если отмечен, иначе пусто.
    enabled = "true" if auctions_ingest_enabled in ("on", "1", "true") else "false"
    svc.save_setting(db, "auctions_ingest_enabled", enabled, updated_by=user.login)
    payload["auctions_ingest_enabled"] = enabled

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUCTION_SETTINGS_UPDATE,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="settings",
        payload=payload,
        ip=ip, user_agent=ua,
    )

    request.session["flash_info"] = "Настройки сохранены."
    return RedirectResponse(url="/auctions/settings", status_code=status.HTTP_302_FOUND)


@router.post("/settings/region/{region_code}/toggle")
def auctions_region_toggle(
    region_code: str,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_permission("auctions_edit_settings")),
    db: Session = Depends(get_db),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    try:
        new_value = svc.toggle_region(db, region_code, changed_by=user.login)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUCTION_REGION_TOGGLE,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="region",
        target_id=region_code,
        payload={"excluded": new_value},
        ip=ip, user_agent=ua,
    )

    request.session["flash_info"] = (
        f"Регион «{region_code}» теперь "
        f"{'в стоп-листе' if new_value else 'разрешён'}."
    )
    return RedirectResponse(url="/auctions/settings", status_code=status.HTTP_302_FOUND)


@router.post("/settings/ktru/add")
def auctions_ktru_add(
    request: Request,
    code: str = Form(""),
    display_name: str = Form(""),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_permission("auctions_edit_settings")),
    db: Session = Depends(get_db),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    try:
        svc.add_ktru(db, code=code, display_name=display_name)
    except ValueError as exc:
        request.session["flash_error"] = str(exc)
        return RedirectResponse(url="/auctions/settings", status_code=status.HTTP_302_FOUND)

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUCTION_KTRU_ADD,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="ktru",
        target_id=code.strip(),
        payload={"display_name": (display_name or "").strip()},
        ip=ip, user_agent=ua,
    )

    request.session["flash_info"] = f"KTRU «{code.strip()}» добавлен (или активирован)."
    return RedirectResponse(url="/auctions/settings", status_code=status.HTTP_302_FOUND)


@router.post("/settings/ktru/{code}/toggle")
def auctions_ktru_toggle(
    code: str,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_permission("auctions_edit_settings")),
    db: Session = Depends(get_db),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    try:
        new_value = svc.toggle_ktru(db, code)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUCTION_KTRU_TOGGLE,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="ktru",
        target_id=code,
        payload={"is_active": new_value},
        ip=ip, user_agent=ua,
    )

    request.session["flash_info"] = (
        f"KTRU «{code}» {'активирован' if new_value else 'выключен'}."
    )
    return RedirectResponse(url="/auctions/settings", status_code=status.HTTP_302_FOUND)


# ============================================================
# GET /auctions/sku/{nomenclature_id}/details — HTML-фрагмент с
# attrs_jsonb для модалки на карточке лота (#7 9a-fixes).
# Объявлено ДО /{reg_number}, чтобы wildcard не перехватил «sku»
# как reg_number.
# ============================================================

@router.get("/sku/{nomenclature_id}/details")
def auction_sku_details(
    nomenclature_id: int,
    request: Request,
    user: AuthUser = Depends(require_permission("auctions")),
):
    from app.services.auctions.catalog import service as _cat
    row = _cat.get_by_id(nomenclature_id)
    if row is None:
        raise HTTPException(status_code=404, detail="SKU не найден.")

    return templates.TemplateResponse(
        request,
        "auctions/_sku_details.html",
        {
            "user":      user,
            "sku":       row,
            "attrs":     row.get("attrs_jsonb") or {},
            "ktru":      list(row.get("ktru_codes_array") or []),
        },
    )


# ============================================================
# GET /auctions/{reg_number} — карточка лота
# (объявлено ПОСЛЕ /settings, чтобы wildcard не перехватывал)
# ============================================================

@router.get("/{reg_number}")
def auction_card(
    reg_number: str,
    request: Request,
    user: AuthUser = Depends(require_permission("auctions")),
    db: Session = Depends(get_db),
):
    tender = svc.get_tender(db, reg_number)
    if tender is None:
        raise HTTPException(status_code=404, detail=f"Лот {reg_number} не найден.")

    items = svc.get_tender_items_with_matches(db, reg_number)

    # Доступные переходы из текущего статуса (для рендера кнопок).
    transitions = sorted(svc.ALLOWED_TRANSITIONS.get(tender["status"], frozenset()))

    margin_threshold = svc.read_setting_int(db, "margin_threshold_pct", 15)

    flash_error = request.session.pop("flash_error", None)
    flash_info = request.session.pop("flash_info", None)

    return templates.TemplateResponse(
        request,
        "auctions/card.html",
        {
            "user":               user,
            "csrf_token":         get_csrf_token(request),
            "t":                  tender,
            "items":              items,
            "transitions":        transitions,
            "all_statuses":       _ALL_STATUSES,
            "status_labels":      svc.STATUS_LABELS,
            "fmt_msk":            svc.format_msk_dt,
            "fmt_msk_date":       svc.format_msk_date,
            "margin_threshold_pct": margin_threshold,
            "contract_date_keys": _CONTRACT_DATE_KEYS,
            "info":               flash_info,
            "error":              flash_error,
        },
    )


# ============================================================
# POST /auctions/{reg_number}/status — переход по state-machine
# ============================================================

@router.post("/{reg_number}/status")
def auction_status_change(
    reg_number: str,
    request: Request,
    new_status: str = Form(...),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_permission("auctions_edit_status")),
    db: Session = Depends(get_db),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    try:
        previous = svc.update_status(
            db, reg_number=reg_number, new_status=new_status, changed_by=user.login,
        )
    except ValueError as exc:
        # Невалидный переход или несуществующий лот — 400.
        raise HTTPException(status_code=400, detail=str(exc))

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUCTION_STATUS_CHANGE,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="tender",
        target_id=reg_number,
        payload={"from": previous, "to": new_status},
        ip=ip, user_agent=ua,
    )

    request.session["flash_info"] = (
        f"Статус: {svc.STATUS_LABELS.get(previous, previous)} → "
        f"{svc.STATUS_LABELS.get(new_status, new_status)}."
    )
    return RedirectResponse(
        url=f"/auctions/{reg_number}", status_code=status.HTTP_302_FOUND,
    )


# ============================================================
# POST /auctions/{reg_number}/contract — реквизиты контракта (после won)
# ============================================================

@router.post("/{reg_number}/contract")
async def auction_contract_update(
    reg_number: str,
    request: Request,
    user: AuthUser = Depends(require_permission("auctions_edit_status")),
    db: Session = Depends(get_db),
):
    form = await request.form()
    csrf_token = form.get("csrf_token", "")
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    tender = svc.get_tender(db, reg_number)
    if tender is None:
        raise HTTPException(status_code=404, detail=f"Лот {reg_number} не найден.")

    crn = (form.get("contract_registry_number") or "").strip()
    key_dates: dict[str, str] = {}
    for k in _CONTRACT_DATE_KEYS:
        v = (form.get(k) or "").strip()
        if v:
            key_dates[k] = v

    svc.update_contract(
        db,
        reg_number=reg_number,
        contract_registry_number=crn,
        key_dates=key_dates,
        changed_by=user.login,
    )

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUCTION_CONTRACT_UPDATE,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="tender",
        target_id=reg_number,
        payload={
            "contract_registry_number": crn,
            "key_dates":                key_dates,
        },
        ip=ip, user_agent=ua,
    )

    request.session["flash_info"] = "Реквизиты контракта сохранены."
    return RedirectResponse(
        url=f"/auctions/{reg_number}", status_code=status.HTTP_302_FOUND,
    )


# ============================================================
# POST /auctions/{reg_number}/note — заметка по лоту
# ============================================================

@router.post("/{reg_number}/note")
def auction_note_update(
    reg_number: str,
    request: Request,
    note: str = Form(""),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_permission("auctions_edit_status")),
    db: Session = Depends(get_db),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    tender = svc.get_tender(db, reg_number)
    if tender is None:
        raise HTTPException(status_code=404, detail=f"Лот {reg_number} не найден.")

    svc.update_note(db, reg_number=reg_number, note=note, changed_by=user.login)

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUCTION_NOTE_UPDATE,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="tender",
        target_id=reg_number,
        payload={"length": len((note or "").strip())},
        ip=ip, user_agent=ua,
    )

    request.session["flash_info"] = "Заметка сохранена."
    return RedirectResponse(
        url=f"/auctions/{reg_number}", status_code=status.HTTP_302_FOUND,
    )
