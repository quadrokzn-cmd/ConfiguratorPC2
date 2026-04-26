# Админский роутер: /admin, /admin/users, /admin/budget, /admin/queries.
# Доступ закрыт require_admin.

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import (
    AuthUser,
    get_csrf_token,
    require_admin,
    verify_csrf,
)
from app.config import settings
from app.database import get_db
from app.services import (
    budget_guard,
    component_service,
    mapping_service,
    supplier_service,
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
    mapping_count = mapping_service.count_active(db)
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
    """Этап 9Б.1: страница пользователей переехала в портал
    (portal/admin/users). Здесь оставлен только редирект — старые
    закладки и ссылки в шаблонах конфигуратора продолжают работать."""
    return RedirectResponse(
        url=f"{settings.portal_url}/admin/users",
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


# =====================================================================
# /admin/suppliers — справочник поставщиков (этап 9А.2)
# =====================================================================

@router.get("/suppliers")
def suppliers_list(
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Список поставщиков + кнопка добавления."""
    items = supplier_service.list_suppliers(db)
    return templates.TemplateResponse(
        request,
        "admin/suppliers_list.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "items":      items,
            "info":       request.session.pop("flash_info",  None),
            "error":      request.session.pop("flash_error", None),
        },
    )


@router.get("/suppliers/new")
def supplier_new_form(
    request: Request,
    user: AuthUser = Depends(require_admin),
):
    """Форма создания поставщика."""
    return templates.TemplateResponse(
        request,
        "admin/supplier_form.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "supplier":   None,
            "error":      request.session.pop("flash_error", None),
        },
    )


@router.post("/suppliers/new")
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
        return RedirectResponse(url="/admin/suppliers/new", status_code=status.HTTP_302_FOUND)

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
        return RedirectResponse(url="/admin/suppliers/new", status_code=status.HTTP_302_FOUND)

    request.session["flash_info"] = f"Поставщик «{name_clean}» добавлен."
    return RedirectResponse(url="/admin/suppliers", status_code=status.HTTP_302_FOUND)


@router.get("/suppliers/{supplier_id}/edit")
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
        "admin/supplier_form.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "supplier":   sup,
            "error":      request.session.pop("flash_error", None),
        },
    )


@router.post("/suppliers/{supplier_id}/edit")
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
            url=f"/admin/suppliers/{supplier_id}/edit",
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
            url=f"/admin/suppliers/{supplier_id}/edit",
            status_code=status.HTTP_302_FOUND,
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Поставщик не найден.")

    request.session["flash_info"] = "Изменения сохранены."
    return RedirectResponse(url="/admin/suppliers", status_code=status.HTTP_302_FOUND)


@router.post("/suppliers/{supplier_id}/toggle")
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
    return RedirectResponse(url="/admin/suppliers", status_code=status.HTTP_302_FOUND)


@router.post("/suppliers/{supplier_id}/delete")
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
        return RedirectResponse(url="/admin/suppliers", status_code=status.HTTP_302_FOUND)

    request.session["flash_info"] = "Поставщик удалён."
    return RedirectResponse(url="/admin/suppliers", status_code=status.HTTP_302_FOUND)


# =====================================================================
# /admin/components — справочник компонентов с ручной правкой (этап 9А.2)
# =====================================================================

@router.get("/components")
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
    """Список компонентов с фильтрами и сортировкой.

    9А.2.2: вместо чекбоксов «Только скелеты»/«Только скрытые»
    единый параметр `status` (full | skeleton | hidden | with_price |
    no_price). Параметр `sort` — '<column>,<asc|desc>'. При
    `?partial=1` отдаём только partial-фрагмент (таблицу + чипы +
    счётчик), чтобы фронт мог обновить таблицу без перезагрузки.
    """
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
            request, "admin/_components_table.html", ctx
        )
    return templates.TemplateResponse(
        request, "admin/components_list.html", ctx
    )


@router.get("/components/{cat}/{component_id}")
def component_detail(
    cat: str,
    component_id: int,
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if cat not in component_service.EDITABLE_FIELDS:
        raise HTTPException(status_code=404, detail="Неизвестная категория.")
    item = component_service.get_component(db, category=cat, component_id=component_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Компонент не найден.")
    supplier_prices = component_service.list_supplier_prices_for_component(
        db, category=cat, component_id=component_id,
    )
    return templates.TemplateResponse(
        request,
        "admin/component_detail.html",
        {
            "user":            user,
            "csrf_token":      get_csrf_token(request),
            "item":            item,
            "supplier_prices": supplier_prices,
            "info":            request.session.pop("flash_info",  None),
            "error":           request.session.pop("flash_error", None),
        },
    )


@router.post("/components/{cat}/{component_id}/edit")
async def component_update(
    cat: str,
    component_id: int,
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Сохраняет редактируемые поля компонента.

    Принимаем как form-data: ключи — имена полей из EDITABLE_FIELDS,
    значения — строки. Bool-поля приходят при toggle on/off.
    """
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
            url=f"/admin/components/{cat}/{component_id}",
            status_code=status.HTTP_302_FOUND,
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Компонент не найден.")

    request.session["flash_info"] = "Характеристики обновлены."
    return RedirectResponse(
        url=f"/admin/components/{cat}/{component_id}",
        status_code=status.HTTP_302_FOUND,
    )


@router.post("/components/{cat}/{component_id}/toggle-hidden")
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
    new_state = component_service.toggle_hidden(db, category=cat, component_id=component_id)
    if new_state is None:
        raise HTTPException(status_code=404, detail="Компонент не найден.")
    request.session["flash_info"] = (
        "Компонент скрыт из подбора." if new_state else "Компонент возвращён в подбор."
    )
    return RedirectResponse(
        url=f"/admin/components/{cat}/{component_id}",
        status_code=status.HTTP_302_FOUND,
    )
