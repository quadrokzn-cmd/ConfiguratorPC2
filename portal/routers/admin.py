# Админский роутер конфигуратора: /admin (dashboard), /admin/budget,
# /admin/queries. Доступ — require_admin.
#
# UI-5 (Путь B, 2026-05-11): три admin-страницы конфигуратора переехали
# из app/routers/admin_router.py в portal/routers/admin.py при удалении
# папки app/. URL'ы /admin, /admin/budget, /admin/queries сохранены —
# собственник 2026-05-11 подтвердил, что менеджеров с закладками нет,
# редиректы не нужны. /admin/users, /admin/backups, /admin/audit живут
# отдельно (settings/*), /admin/price-uploads, /admin/auto-price-loads,
# /admin/auctions, /admin/diagnostics — отдельные роутеры.

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from portal.services.configurator import (
    budget_guard,
    web_service,
)
from portal.templating import templates
from shared.auth import AuthUser, get_csrf_token, require_admin
from shared.db import get_db


router = APIRouter(prefix="/admin")


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
    # Очередь маппинга — inline COUNT, чтобы не тянуть лишний service-импорт.
    mapping_count = int(db.execute(
        text(
            "SELECT COUNT(*) FROM unmapped_supplier_items "
            "WHERE status IN ('pending', 'created_new')"
        )
    ).scalar() or 0)
    # Сводные счётчики для метрик дашборда.
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
