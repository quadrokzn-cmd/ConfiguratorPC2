# /databases/components — комплектующие для ПК на портале (этап UI-2
# Пути B, 2026-05-11). Перенесён из конфигуратора
# (app/routers/admin_router.py, блок /admin/components/*).
#
# UI-лейбл изменился: «Компоненты» → «Комплектующие для ПК».
# URL: /databases/components (старый /admin/components даёт 301).
# Доступ: require_admin (как и в исходной реализации).

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from portal.services.databases import component_service
from portal.templating import templates
from shared.audit import extract_request_meta, write_audit
from shared.audit_actions import (
    ACTION_COMPONENT_HIDE,
    ACTION_COMPONENT_SHOW,
    ACTION_COMPONENT_UPDATE,
)
from shared.auth import (
    AuthUser,
    get_csrf_token,
    require_admin,
    verify_csrf,
)
from shared.db import get_db


router = APIRouter(prefix="/databases/components")


@router.get("")
def components_list(
    request: Request,
    category: str = "",
    q: str = "",
    status: str = "",
    sort: str = "",
    partial: str = "",
    page: int = 1,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Список комплектующих с фильтрами и сортировкой.

    9А.2.2: единый параметр `status` (full | skeleton | hidden | with_price |
    no_price). Параметр `sort` — '<column>,<asc|desc>'. При `?partial=1`
    отдаём только partial-фрагмент (таблицу + чипы + счётчик)."""
    cat = category if category in component_service.EDITABLE_FIELDS else ""
    result = component_service.list_components(
        db,
        category=cat or None,
        search=q.strip(),
        status=status.strip().lower(),
        sort=sort.strip(),
        page=int(page or 1),
        per_page=30,
    )
    ctx = {
        "user":        user,
        "csrf_token":  get_csrf_token(request),
        "result":      result,
        "category":    cat,
        "search":      q,
        "status":      status,
        "sort":        sort,
        "categories":  component_service.CATEGORY_LABELS,
        "info":        request.session.pop("flash_info",  None),
        "error":       request.session.pop("flash_error", None),
    }
    if partial == "1":
        return templates.TemplateResponse(
            request, "databases/_components_table.html", ctx
        )
    return templates.TemplateResponse(
        request, "databases/components_list.html", ctx
    )


@router.get("/{cat}/{component_id}")
def component_detail(
    cat: str,
    component_id: int,
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if cat not in component_service.EDITABLE_FIELDS:
        raise HTTPException(status_code=404, detail="Неизвестная категория.")
    item = component_service.get_component(
        db, category=cat, component_id=component_id,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Компонент не найден.")
    supplier_prices = component_service.list_supplier_prices_for_component(
        db, category=cat, component_id=component_id,
    )
    return templates.TemplateResponse(
        request,
        "databases/component_detail.html",
        {
            "user":            user,
            "csrf_token":      get_csrf_token(request),
            "item":            item,
            "supplier_prices": supplier_prices,
            "info":            request.session.pop("flash_info",  None),
            "error":           request.session.pop("flash_error", None),
        },
    )


@router.post("/{cat}/{component_id}/edit")
async def component_update(
    cat: str,
    component_id: int,
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Сохраняет редактируемые поля компонента."""
    if cat not in component_service.EDITABLE_FIELDS:
        raise HTTPException(status_code=404, detail="Неизвестная категория.")
    form = await request.form()
    csrf_token = form.get("csrf_token", "")
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    raw_fields = {k: v for k, v in form.items() if k != "csrf_token"}
    try:
        ok = component_service.update_component_fields(
            db, category=cat, component_id=component_id, raw_fields=raw_fields,
        )
    except (ValueError, TypeError) as exc:
        request.session["flash_error"] = f"Ошибка сохранения: {exc}"
        return RedirectResponse(
            url=f"/databases/components/{cat}/{component_id}",
            status_code=status.HTTP_302_FOUND,
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Компонент не найден.")

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_COMPONENT_UPDATE,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type=f"component.{cat}",
        target_id=component_id,
        payload={"fields": list(raw_fields.keys())},
        ip=ip,
        user_agent=ua,
    )

    request.session["flash_info"] = "Характеристики обновлены."
    return RedirectResponse(
        url=f"/databases/components/{cat}/{component_id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{cat}/{component_id}/toggle-hidden")
def component_toggle_hidden(
    cat: str,
    component_id: int,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if cat not in component_service.EDITABLE_FIELDS:
        raise HTTPException(status_code=404, detail="Неизвестная категория.")
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")
    new_state = component_service.toggle_hidden(
        db, category=cat, component_id=component_id,
    )
    if new_state is None:
        raise HTTPException(status_code=404, detail="Компонент не найден.")
    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_COMPONENT_HIDE if new_state else ACTION_COMPONENT_SHOW,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type=f"component.{cat}",
        target_id=component_id,
        ip=ip,
        user_agent=ua,
    )
    request.session["flash_info"] = (
        "Компонент скрыт из подбора." if new_state else "Компонент возвращён в подбор."
    )
    return RedirectResponse(
        url=f"/databases/components/{cat}/{component_id}",
        status_code=status.HTTP_302_FOUND,
    )
