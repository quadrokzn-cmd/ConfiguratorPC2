# Админский роутер: /admin, /admin/users, /admin/budget, /admin/queries.
# Доступ закрыт require_admin.

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import (
    AuthUser,
    get_csrf_token,
    hash_password,
    require_admin,
    verify_csrf,
)
from app.database import get_db
from app.services import budget_guard, mapping_service, web_service


router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")


@router.get("")
def dashboard(
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Главная админки: виджеты расходов + последние запросы системы."""
    budget = budget_guard.check_budget(db)
    month_total = web_service.get_month_total_rub(db)
    recent = web_service.list_all_queries(db, limit=20)
    mapping_count = mapping_service.count_active(db)
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user":          user,
            "csrf_token":    get_csrf_token(request),
            "budget":        budget,
            "month_total":   month_total,
            "recent":        recent,
            "mapping_count": mapping_count,
        },
    )


@router.get("/users")
def users_list(
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Список пользователей + форма создания менеджера."""
    users = web_service.list_users(db)
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "users":      users,
            "error":      request.session.pop("flash_error", None),
            "info":       request.session.pop("flash_info",  None),
        },
    )


@router.post("/users")
def users_create(
    request: Request,
    login: str = Form(...),
    name: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Создаёт нового менеджера."""
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    login_clean = (login or "").strip()
    name_clean = (name or "").strip()
    if not login_clean or not name_clean or not password:
        request.session["flash_error"] = (
            "Заполните логин, имя и пароль."
        )
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)

    if len(password) < 6:
        request.session["flash_error"] = "Пароль должен быть не короче 6 символов."
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)

    try:
        web_service.create_manager(
            db,
            login=login_clean,
            password_hash=hash_password(password),
            name=name_clean,
        )
    except ValueError as exc:
        if str(exc) == "login_taken":
            request.session["flash_error"] = (
                f"Логин «{login_clean}» уже занят."
            )
        else:
            request.session["flash_error"] = f"Ошибка создания: {exc}"
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)

    request.session["flash_info"] = f"Пользователь «{login_clean}» создан."
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


@router.post("/users/{user_id}/toggle")
def users_toggle(
    user_id: int,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Активировать / деактивировать пользователя."""
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")
    # Нельзя деактивировать самого себя — защита от самоблокировки.
    if int(user_id) == int(user.id):
        request.session["flash_error"] = "Нельзя деактивировать собственную учётку."
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)
    new_state = web_service.toggle_user_active(db, user_id)
    request.session["flash_info"] = (
        f"Пользователь переведён в состояние: {'активен' if new_state else 'отключён'}."
    )
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


@router.get("/budget")
def budget_detail(
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Детальная статистика расходов по дням."""
    budget = budget_guard.check_budget(db)
    by_day = web_service.get_budget_by_day(db, days=30)
    month_total = web_service.get_month_total_rub(db)
    return templates.TemplateResponse(
        request,
        "admin/budget.html",
        {
            "user":        user,
            "csrf_token":  get_csrf_token(request),
            "budget":      budget,
            "by_day":      by_day,
            "month_total": month_total,
        },
    )


@router.get("/queries")
def all_queries(
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Все запросы всех пользователей."""
    items = web_service.list_all_queries(db, limit=500)
    return templates.TemplateResponse(
        request,
        "admin/all_queries.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "items":      items,
        },
    )
