# Админский роутер /admin/mapping: ручное сопоставление неопределённых
# строк прайсов с компонентами БД (этап 7).

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import AuthUser, get_csrf_token, require_admin, verify_csrf
from app.database import get_db
from app.services import mapping_service


router = APIRouter(prefix="/admin/mapping")
templates = Jinja2Templates(directory="app/templates")


CATEGORY_OPTIONS = [
    ("cpu", "Процессоры"),
    ("motherboard", "Материнские платы"),
    ("ram", "Оперативная память"),
    ("gpu", "Видеокарты"),
    ("storage", "Накопители"),
    ("case", "Корпуса"),
    ("psu", "Блоки питания"),
    ("cooler", "Охлаждение"),
]

# Размер страницы. 50 — удобно для обычной работы, увеличит нагрузку
# не больше, чем /admin/queries.
PAGE_SIZE = 50


@router.get("")
def mapping_list(
    request: Request,
    supplier: int | None = Query(default=None),
    category: str | None = Query(default=None),
    score: str = Query(default=mapping_service.SCORE_FILTER_SUSPICIOUS),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Список активных записей (pending + created_new) с фильтрами.

    По умолчанию показываем только «подозрительные» (score >= 50) —
    это сокращает ручной поток работы с 2000+ до ~300-500 записей.
    Фильтр score: suspicious | new | all.
    """
    # Нормализуем значение фильтра, чтобы опечатка в URL не уронила запрос.
    if score not in (
        mapping_service.SCORE_FILTER_SUSPICIOUS,
        mapping_service.SCORE_FILTER_NEW,
        mapping_service.SCORE_FILTER_ALL,
    ):
        score = mapping_service.SCORE_FILTER_SUSPICIOUS

    # Сначала досчитаем score у активных записей, где он не считался.
    # Иначе при фильтре «подозрительные» (score >= 50) они даже не попадут
    # в выборку, т. к. NULL не проходит условие по значению.
    # Для тяжёлой миграции 2000+ записей пользователь запускает
    # scripts/recalculate_unmapped_scores.py; здесь — лёгкий добор на
    # PAGE_SIZE за раз.
    missing = mapping_service.list_ids_missing_score(
        db,
        supplier_id=supplier,
        category=category or None,
        limit=PAGE_SIZE,
    )
    for rid in missing:
        try:
            mapping_service.ensure_score(db, rid)
        except Exception:
            # Сбой на одной записи не должен валить всю страницу.
            pass

    rows = mapping_service.list_active_with_score(
        db,
        supplier_id=supplier,
        category=category or None,
        score_filter=score,
        limit=PAGE_SIZE,
    )

    score_counts = mapping_service.count_by_score(db)
    suppliers = mapping_service.list_suppliers(db)

    return templates.TemplateResponse(
        request,
        "admin/mapping_list.html",
        {
            "user":            user,
            "csrf_token":      get_csrf_token(request),
            "rows":            rows,
            "score_counts":    score_counts,
            "suppliers":       suppliers,
            "categories":      CATEGORY_OPTIONS,
            "filter_supplier": supplier,
            "filter_category": category or "",
            "filter_score":    score,
            "score_filter_suspicious": mapping_service.SCORE_FILTER_SUSPICIOUS,
            "score_filter_new":        mapping_service.SCORE_FILTER_NEW,
            "score_filter_all":        mapping_service.SCORE_FILTER_ALL,
            "flash_info":      request.session.pop("mapping_flash_info", None),
            "flash_error":     request.session.pop("mapping_flash_error", None),
        },
    )


@router.get("/{row_id}")
def mapping_detail(
    row_id: int,
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Детальная страница: вся информация + топ-10 похожих кандидатов.

    Используем тот же скоринг, что на списочной странице
    (calculate_candidates_ranked) — иначе список и деталь расходятся.
    """
    row = mapping_service.get_by_id(db, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Запись не найдена.")

    try:
        candidates = mapping_service.calculate_candidates_ranked(
            db, row, limit=10,
        )
    except Exception:
        candidates = []

    return templates.TemplateResponse(
        request,
        "admin/mapping_detail.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "row":        row,
            "candidates": candidates,
        },
    )


def _require_csrf(request: Request, token: str) -> None:
    if not verify_csrf(request, token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")


@router.post("/{row_id}/merge")
def mapping_merge(
    row_id: int,
    request: Request,
    target_component_id: int = Form(..., alias="target_component_id"),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Объединяет запись с выбранным компонентом. См. mapping_service.merge_with_component."""
    _require_csrf(request, csrf_token)
    try:
        mapping_service.merge_with_component(
            db,
            unmapped_id=row_id,
            target_component_id=int(target_component_id),
            admin_user_id=user.id,
        )
        request.session["mapping_flash_info"] = (
            f"Запись #{row_id} объединена с компонентом id={target_component_id}."
        )
    except ValueError as exc:
        request.session["mapping_flash_error"] = f"Не удалось объединить: {exc}"
    return RedirectResponse(url="/admin/mapping", status_code=status.HTTP_302_FOUND)


@router.post("/{row_id}/confirm_as_new")
def mapping_confirm_new(
    row_id: int,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """«Это точно новый товар» — меняем статус на confirmed_new, компонент оставляем."""
    _require_csrf(request, csrf_token)
    mapping_service.confirm_as_new(db, unmapped_id=row_id, admin_user_id=user.id)
    request.session["mapping_flash_info"] = (
        f"Запись #{row_id} отмечена как новый товар."
    )
    return RedirectResponse(url="/admin/mapping", status_code=status.HTTP_302_FOUND)


@router.post("/{row_id}/defer")
def mapping_defer(
    row_id: int,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """«Разобраться потом» — просто возвращаемся к списку."""
    _require_csrf(request, csrf_token)
    mapping_service.defer(db, unmapped_id=row_id)
    return RedirectResponse(url="/admin/mapping", status_code=status.HTTP_302_FOUND)


@router.post("/bulk_confirm_new")
def mapping_bulk_confirm_new(
    request: Request,
    supplier: int | None = Form(default=None),
    category: str | None = Form(default=None),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Массовое действие: все created_new со score ниже порога →
    confirmed_new. Используется для очистки очереди от «точно новых»
    позиций после первичной загрузки прайсов."""
    _require_csrf(request, csrf_token)
    updated = mapping_service.bulk_confirm_new(
        db,
        admin_user_id=user.id,
        supplier_id=supplier,
        category=category,
    )
    if updated == 0:
        request.session["mapping_flash_info"] = (
            "Нет записей, подходящих под массовое подтверждение."
        )
    else:
        request.session["mapping_flash_info"] = (
            f"Подтверждено {updated} товаров как «новые»."
        )
    # Сохраняем исходные фильтры в query-string, чтобы админ после
    # массовой операции остался в «вероятно новых».
    qs = f"?score={mapping_service.SCORE_FILTER_NEW}"
    if supplier is not None:
        qs += f"&supplier={supplier}"
    if category:
        qs += f"&category={category}"
    return RedirectResponse(
        url=f"/admin/mapping{qs}", status_code=status.HTTP_302_FOUND,
    )
