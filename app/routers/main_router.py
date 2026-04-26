# Основной роутер: /, /query (POST), /query/{id}, /history.

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import AuthUser, get_csrf_token, require_login, verify_csrf
from app.database import get_db
from app.services import budget_guard, web_service
from app.services.nlu import process_query
from app.services.web_result_view import enrich_variants_with_specs
from app.templating import templates

logger = logging.getLogger(__name__)


router = APIRouter()


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
# Бизнес-порядок сборки ПК: процессор → охлаждение → материнка →
# память → накопители → видеокарта → корпус → БП.
# Отсутствующие категории (например, GPU в офисной сборке) просто
# пропускаются, соседние ряды встают подряд.
_CATEGORY_ORDER = ["cpu", "cooler", "motherboard", "ram", "storage", "gpu", "case", "psu"]


@router.get("/")
def index(
    request: Request,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Главная страница с формой запроса.
    Этап 9А.1: добавлен блок «Последние ваши запросы» и боковая
    мини-сводка для админа (без новых эндпоинтов — данные собираем
    из существующих сервисов)."""
    recent_queries = web_service.list_user_queries(db, user_id=user.id, limit=4)

    admin_summary = None
    if user.is_admin:
        # Сводка для админа: число проектов, запросов сегодня, бюджет.
        # Используем уже существующие сервисы — никакой новой логики.
        from sqlalchemy import text as _t
        total_projects = db.execute(
            _t("SELECT COUNT(*) AS c FROM projects")
        ).scalar() or 0
        today_queries = db.execute(
            _t(
                "SELECT COUNT(*) AS c FROM queries "
                "WHERE created_at::date = CURRENT_DATE"
            )
        ).scalar() or 0
        admin_summary = {
            "total_projects": int(total_projects),
            "today_queries":  int(today_queries),
            "budget":         budget_guard.check_budget(db),
        }

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "user":           user,
            "csrf_token":     get_csrf_token(request),
            "error":          request.session.pop("flash_error", None),
            "recent_queries": recent_queries,
            "admin_summary":  admin_summary,
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
            url=f"/project/{project_id}?highlight={qid}",
            status_code=status.HTTP_302_FOUND,
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

    # Этап 6.2: ведём не на одиночный /query/{id}, а в карточку проекта
    # с якорем на только что добавленную конфигурацию — менеджер сразу
    # видит результат и может поставить галочку «в спецификацию».
    return RedirectResponse(
        url=f"/project/{project_id}?highlight={qid}",
        status_code=status.HTTP_302_FOUND,
    )


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
    # Обогащаем компоненты короткой строкой характеристик (этап 6.1)
    # и сырыми полями raw_specs (для автоназвания при выборе в спецификацию).
    if variants:
        enrich_variants_with_specs(variants, db)
    refusal = None
    if q.get("build_result") is not None:
        refusal = q["build_result"].get("refusal_reason")

    # Этап 6.2: какие варианты уже выбраны в спецификацию проекта —
    # чтобы чекбоксы на этой странице отражали актуальное состояние.
    # Локальный импорт, чтобы не плодить циклы на уровне модуля.
    from app.services import spec_service
    spec_items = spec_service.list_spec_items(db, project_id=q["project_id"])
    selected_of_cfg = spec_service.selected_set(spec_items).get(q["id"], {})

    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "user":            user,
            "csrf_token":      get_csrf_token(request),
            "q":               q,
            "variants":        variants,
            "refusal":         refusal,
            "selected_of_cfg": selected_of_cfg,
            "category_order":  _CATEGORY_ORDER,
            "category_label":  _CATEGORY_LABELS,
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
    Каждый вариант: {manufacturer, total_usd, total_rub,
    components (dict по категориям — для карточек в variant_block),
    storages_list (список всех накопителей варианта — для автоназвания
    со строкой «1TB SSD + 2TB HDD»), warnings, used_transit, path_used}."""
    if not build_result:
        return []
    out: list[dict] = []
    for v in build_result.get("variants") or []:
        components_by_cat: dict[str, dict] = {}
        storages_list: list[dict] = []
        for c in v.get("components") or []:
            cat = c.get("category")
            if not cat:
                continue
            components_by_cat[cat] = c
            if cat == "storage":
                storages_list.append(c)
        out.append({
            "manufacturer":  v.get("manufacturer"),
            "total_usd":     v.get("total_usd"),
            "total_rub":     v.get("total_rub"),
            "components":    components_by_cat,
            "storages_list": storages_list,
            "warnings":      v.get("warnings") or [],
            "used_transit":  v.get("used_transit"),
            "path_used":     v.get("path_used"),
        })
    return out
