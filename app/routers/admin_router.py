# Админский роутер: /admin, /admin/users, /admin/budget, /admin/queries.
# Доступ закрыт require_admin.
#
# UI-2 (Путь B, 2026-05-11): /admin/suppliers/* и /admin/components/*
# переехали в портал (portal/routers/databases/{suppliers,components}).
# 301-редиректы со старых URL стоят в app/main.py — здесь обработчиков
# для этих путей больше нет.

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import (
    AuthUser,
    get_csrf_token,
    require_admin,
)
from app.config import settings
from app.database import get_db
# UI-4 (Путь B, 2026-05-11): сервисы конфигуратора переехали в
# portal/services/configurator/. admin_router.py остаётся в app/ для
# страниц /admin (dashboard), /admin/budget, /admin/queries и
# legacy-редиректа /admin/users → portal/settings/users.
from portal.services.configurator import (
    budget_guard,
    web_service,
)
from app.templating import templates


router = APIRouter(prefix="/admin")


@router.get("/dashboard")
def dashboard_legacy_alias():
    """Алиас /admin/dashboard → /admin.

    Исторически ссылка была такой. 301 — чтобы браузер/боты
    закешировали правильный URL.
    """
    return RedirectResponse(url="/admin", status_code=301)


@router.get("")
def dashboard(
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Главная админки: метрики, бюджет OpenAI, последние запросы системы."""
    budget = budget_guard.check_budget(db)
    month_total = web_service.get_month_total_rub(db)
    recent = web_service.list_all_queries(db, limit=20)
    # UI-2: mapping_service переехал в portal, но dashboard конфигуратора
    # пока остаётся в app/ (его перенос — этап UI-3..UI-4). Inline-COUNT,
    # чтобы не тянуть кросс-сервисный импорт portal.services из app/.
    mapping_count = int(db.execute(
        text(
            "SELECT COUNT(*) FROM unmapped_supplier_items "
            "WHERE status IN ('pending', 'created_new')"
        )
    ).scalar() or 0)
    # Сводные счётчики для метрик дашборда (этап 9А.2). Считаем тонко
    # отдельными SELECT COUNT(*), без новых сервисных функций.
    total_queries  = int(db.execute(text("SELECT COUNT(*) FROM queries")).scalar() or 0)
    total_projects = int(db.execute(text("SELECT COUNT(*) FROM projects")).scalar() or 0)
    active_users   = int(
        db.execute(text("SELECT COUNT(*) FROM users WHERE is_active = TRUE")).scalar() or 0
    )
    return templates.TemplateResponse(
        request,
        "admin/dashboard.html",
        {
            "user":           user,
            "csrf_token":     get_csrf_token(request),
            "budget":         budget,
            "month_total":    month_total,
            "recent":         recent,
            "mapping_count":  mapping_count,
            "total_queries":  total_queries,
            "total_projects": total_projects,
            "active_users":   active_users,
        },
    )


@router.get("/users")
def users_redirect_to_portal():
    """Этап 9Б.1: страница пользователей переехала в портал.
    UI-3 (Путь B, 2026-05-11): URL в портале сменился с /admin/users на
    /settings/users — этот редирект ведёт сразу на новый URL, чтобы не
    делать двойной hop (config → portal/admin/users → portal/settings/users)."""
    return RedirectResponse(
        url=f"{settings.portal_url}/settings/users",
        status_code=status.HTTP_302_FOUND,
    )


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
