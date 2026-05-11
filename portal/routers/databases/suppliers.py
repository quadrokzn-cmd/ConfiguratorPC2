# /databases/suppliers — справочник поставщиков на портале (этап UI-2
# Пути B, 2026-05-11). Перенесён без изменения логики из конфигуратора
# (app/routers/admin_router.py, блок /admin/suppliers/*).
#
# UI-лейбл «Поставщики» сохраняется. Таблица БД — `suppliers`.
# Доступ: require_admin (как и в исходной реализации).

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from portal.services.databases import supplier_service
from portal.templating import templates
from shared.auth import (
    AuthUser,
    get_csrf_token,
    require_admin,
    verify_csrf,
)
from shared.db import get_db


router = APIRouter(prefix="/databases/suppliers")


@router.get("")
def suppliers_list(
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Список поставщиков + кнопка добавления."""
    items = supplier_service.list_suppliers(db)
    return templates.TemplateResponse(
        request,
        "databases/suppliers_list.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "items":      items,
            "info":       request.session.pop("flash_info",  None),
            "error":      request.session.pop("flash_error", None),
        },
    )


@router.get("/new")
def supplier_new_form(
    request: Request,
    user: AuthUser = Depends(require_admin),
):
    """Форма создания поставщика."""
    return templates.TemplateResponse(
        request,
        "databases/supplier_form.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "supplier":   None,
            "error":      request.session.pop("flash_error", None),
        },
    )


@router.post("/new")
def supplier_create(
    request: Request,
    name: str = Form(...),
    email: str = Form(""),
    contact_person: str = Form(""),
    contact_phone: str = Form(""),
    is_active: str = Form(""),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    name_clean = (name or "").strip()
    if not name_clean:
        request.session["flash_error"] = "Введите название поставщика."
        return RedirectResponse(
            url="/databases/suppliers/new",
            status_code=status.HTTP_302_FOUND,
        )

    try:
        supplier_service.create_supplier(
            db,
            name=name_clean,
            email=(email or "").strip() or None,
            contact_person=(contact_person or "").strip() or None,
            contact_phone=(contact_phone or "").strip() or None,
            is_active=(is_active == "on" or is_active == "true"),
        )
    except ValueError as exc:
        if str(exc) == "name_taken":
            request.session["flash_error"] = (
                f"Поставщик с именем «{name_clean}» уже существует."
            )
        else:
            request.session["flash_error"] = f"Ошибка создания: {exc}"
        return RedirectResponse(
            url="/databases/suppliers/new",
            status_code=status.HTTP_302_FOUND,
        )

    request.session["flash_info"] = f"Поставщик «{name_clean}» добавлен."
    return RedirectResponse(
        url="/databases/suppliers",
        status_code=status.HTTP_302_FOUND,
    )


@router.get("/{supplier_id}/edit")
def supplier_edit_form(
    supplier_id: int,
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    sup = supplier_service.get_supplier(db, supplier_id)
    if sup is None:
        raise HTTPException(status_code=404, detail="Поставщик не найден.")
    return templates.TemplateResponse(
        request,
        "databases/supplier_form.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "supplier":   sup,
            "error":      request.session.pop("flash_error", None),
        },
    )


@router.post("/{supplier_id}/edit")
def supplier_update(
    supplier_id: int,
    request: Request,
    name: str = Form(...),
    email: str = Form(""),
    contact_person: str = Form(""),
    contact_phone: str = Form(""),
    is_active: str = Form(""),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    name_clean = (name or "").strip()
    if not name_clean:
        request.session["flash_error"] = "Имя не может быть пустым."
        return RedirectResponse(
            url=f"/databases/suppliers/{supplier_id}/edit",
            status_code=status.HTTP_302_FOUND,
        )

    try:
        ok = supplier_service.update_supplier(
            db, supplier_id,
            name=name_clean,
            email=(email or "").strip() or None,
            contact_person=(contact_person or "").strip() or None,
            contact_phone=(contact_phone or "").strip() or None,
            is_active=(is_active == "on" or is_active == "true"),
        )
    except ValueError as exc:
        if str(exc) == "name_taken":
            request.session["flash_error"] = (
                f"Имя «{name_clean}» занято другим поставщиком."
            )
        else:
            request.session["flash_error"] = f"Ошибка сохранения: {exc}"
        return RedirectResponse(
            url=f"/databases/suppliers/{supplier_id}/edit",
            status_code=status.HTTP_302_FOUND,
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Поставщик не найден.")

    request.session["flash_info"] = "Изменения сохранены."
    return RedirectResponse(
        url="/databases/suppliers",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{supplier_id}/toggle")
def supplier_toggle(
    supplier_id: int,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")
    new_state = supplier_service.toggle_active(db, supplier_id)
    if new_state is None:
        raise HTTPException(status_code=404, detail="Поставщик не найден.")
    request.session["flash_info"] = (
        f"Поставщик переведён в состояние: {'активен' if new_state else 'отключён'}."
    )
    return RedirectResponse(
        url="/databases/suppliers",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/{supplier_id}/delete")
def supplier_delete(
    supplier_id: int,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")
    try:
        supplier_service.delete_supplier(db, supplier_id)
    except ValueError as exc:
        if str(exc) == "has_links":
            request.session["flash_error"] = (
                "Удалить нельзя — у поставщика есть цены, история писем "
                "или необработанные позиции. Деактивируйте вместо удаления."
            )
        elif str(exc) == "not_found":
            request.session["flash_error"] = "Поставщик не найден."
        else:
            request.session["flash_error"] = f"Ошибка удаления: {exc}"
        return RedirectResponse(
            url="/databases/suppliers",
            status_code=status.HTTP_302_FOUND,
        )

    request.session["flash_info"] = "Поставщик удалён."
    return RedirectResponse(
        url="/databases/suppliers",
        status_code=status.HTTP_302_FOUND,
    )
