# Основной роутер: /, /query (POST), /query/{id}, /history.

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import AuthUser, get_csrf_token, require_login, verify_csrf
from app.database import get_db
from app.services import budget_guard, web_service
from app.services.nlu import process_query

logger = logging.getLogger(__name__)


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# Человекочитаемые подписи категорий для таблиц результата.
_CATEGORY_LABELS = {
    "cpu":         "CPU",
    "motherboard": "Материнская плата",
    "ram":         "Оперативная память",
    "gpu":         "Видеокарта",
    "storage":     "Накопитель",
    "psu":         "Блок питания",
    "case":        "Корпус",
    "cooler":      "Кулер",
}

# Порядок отображения категорий в таблице варианта.
_CATEGORY_ORDER = ["cpu", "motherboard", "ram", "gpu", "storage", "psu", "case", "cooler"]


@router.get("/")
def index(
    request: Request,
    user: AuthUser = Depends(require_login),
):
    """Главная страница с формой запроса."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "error":      request.session.pop("flash_error", None),
        },
    )


@router.post("/query")
def query_submit(
    request: Request,
    project_name: str = Form(""),
    raw_text: str = Form(...),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Принимает запрос, проверяет бюджет, вызывает NLU, сохраняет в БД."""
    # 1. CSRF
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    raw_clean = (raw_text or "").strip()
    if not raw_clean:
        request.session["flash_error"] = "Введите текст запроса."
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)

    # 2. Создаём проект
    project_id = web_service.create_project(
        db,
        user_id=user.id,
        name=web_service.format_project_name(project_name),
    )

    # 3. Проверяем дневной бюджет
    budget = budget_guard.check_budget(db)
    if budget.is_blocked:
        qid = web_service.save_query(
            db,
            project_id=project_id,
            user_id=user.id,
            raw_text=raw_clean,
            resp=None,
            error_msg=(
                "Дневной бюджет OpenAI исчерпан "
                f"({int(round(budget.spent_rub))} ₽ из {int(round(budget.limit_rub))} ₽). "
                "Новые запросы будут доступны завтра."
            ),
        )
        return RedirectResponse(
            url=f"/query/{qid}", status_code=status.HTTP_302_FOUND
        )

    # 4. Запуск NLU. Засекаем старт, чтобы потом точно посчитать
    # стоимость по api_usage_log именно этого запроса.
    started_at = datetime.now()
    resp = None
    err_msg = None
    try:
        resp = process_query(raw_clean)
    except Exception as exc:
        # Ловим и rate-limit (из openai.RateLimitError), и любую
        # внутреннюю ошибку. Наружу — мягкое сообщение.
        logger.exception("process_query упал: %s", exc)
        # Проверяем по имени класса: в нашей кодовой базе RateLimitError
        # импортирован в nlu.parser и может пробрасываться наверх.
        class_name = type(exc).__name__
        if class_name == "RateLimitError":
            err_msg = (
                "Сервис временно перегружен (OpenAI rate-limit). "
                "Попробуйте через минуту."
            )
        else:
            err_msg = f"Внутренняя ошибка при обработке запроса: {class_name}."

    qid = web_service.save_query(
        db,
        project_id=project_id,
        user_id=user.id,
        raw_text=raw_clean,
        resp=resp,
        error_msg=err_msg,
        run_started_at=started_at,
    )

    # 5. Обновляем дневной снэпшот
    budget_guard.upsert_daily_log(db)

    return RedirectResponse(url=f"/query/{qid}", status_code=status.HTTP_302_FOUND)


@router.get("/query/{query_id}")
def query_detail(
    query_id: int,
    request: Request,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Детальный просмотр одного запроса и его результата."""
    q = web_service.get_query_for_user(
        db,
        query_id=query_id,
        requester_user_id=user.id,
        requester_is_admin=user.is_admin,
    )
    if q is None:
        raise HTTPException(status_code=404, detail="Запрос не найден.")
    if q.get("_forbidden"):
        raise HTTPException(status_code=403, detail="Чужой запрос.")

    variants = _prepare_variants(q.get("build_result"))
    refusal = None
    if q.get("build_result") is not None:
        refusal = q["build_result"].get("refusal_reason")

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "user":          user,
            "csrf_token":    get_csrf_token(request),
            "q":             q,
            "variants":      variants,
            "refusal":       refusal,
            "category_order": _CATEGORY_ORDER,
            "category_label": _CATEGORY_LABELS,
        },
    )


@router.get("/history")
def history(
    request: Request,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Список запросов текущего пользователя."""
    items = web_service.list_user_queries(db, user_id=user.id)
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "items":      items,
        },
    )


# --- Подготовка данных для шаблона --------------------------------------

def _prepare_variants(build_result: dict | None) -> list[dict]:
    """Раскладывает dict-BuildResult в список вариантов для шаблона.
    Каждый вариант: {manufacturer, total_usd, total_rub, components (dict по категориям),
    warnings, used_transit, path_used, comment}."""
    if not build_result:
        return []
    out: list[dict] = []
    for v in build_result.get("variants") or []:
        components_by_cat: dict[str, dict] = {}
        for c in v.get("components") or []:
            cat = c.get("category")
            if cat:
                components_by_cat[cat] = c
        out.append({
            "manufacturer": v.get("manufacturer"),
            "total_usd":    v.get("total_usd"),
            "total_rub":    v.get("total_rub"),
            "components":   components_by_cat,
            "warnings":     v.get("warnings") or [],
            "used_transit": v.get("used_transit"),
            "path_used":    v.get("path_used"),
        })
    return out
