# Роутер проектов и спецификации (этап 6.2).
#
# /projects                  — список проектов пользователя
# /project/{id}              — детальная страница проекта
# /project/{id}/new_query    — форма и создание новой конфигурации в проекте
# /project/{id}/select       — добавить вариант в спецификацию (AJAX)
# /project/{id}/deselect     — убрать вариант (AJAX)
# /project/{id}/update_quantity — изменить количество (AJAX)
# /project/{id}/rename       — переименовать проект
# /project/{id}/delete       — удалить проект
# /project/{id}/query/{qid}/delete — удалить конфигурацию из проекта

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import AuthUser, get_csrf_token, require_login, verify_csrf
from app.database import get_db
from app.routers.main_router import (
    _CATEGORY_LABELS,
    _CATEGORY_ORDER,
    _prepare_variants,
)
from app.services import budget_guard, spec_recalc, spec_service, web_service
from app.services.nlu import process_query
from app.services.web_result_view import enrich_variants_with_specs
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------
# Вспомогалки: доступ и CSRF для AJAX
# ---------------------------------------------------------------------

def _load_project_or_raise(
    db: Session,
    *,
    project_id: int,
    user: AuthUser,
) -> dict:
    """Проверяет доступ и возвращает dict проекта. 404/403 — как исключения."""
    project = spec_service.get_project_or_none(
        db,
        project_id=project_id,
        requester_user_id=user.id,
        requester_is_admin=user.is_admin,
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Проект не найден.")
    if project.get("_forbidden"):
        raise HTTPException(status_code=403, detail="Чужой проект.")
    return project


def _verify_csrf_ajax(request: Request) -> None:
    """Проверяет CSRF-токен в заголовке X-CSRF-Token для AJAX-запросов."""
    token = request.headers.get("x-csrf-token", "")
    if not verify_csrf(request, token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")


def _spec_payload(db: Session, project_id: int) -> dict:
    items = spec_service.list_spec_items(db, project_id=project_id)
    totals = spec_service.spec_totals(items)
    return {
        "ok":    True,
        "items": [
            {
                "id":                   it["id"],
                "query_id":             it["query_id"],
                "variant_manufacturer": it["variant_manufacturer"],
                "quantity":             it["quantity"],
                "position":             it["position"],
                "display_name":         it["display_name"],
                "unit_usd":             it["unit_usd"],
                "unit_rub":             it["unit_rub"],
                "total_usd":            it["total_usd"],
                "total_rub":            it["total_rub"],
                "recalculated_at":      it.get("recalculated_at").isoformat()
                                        if it.get("recalculated_at") else None,
            }
            for it in items
        ],
        "total_usd": totals["total_usd"],
        "total_rub": totals["total_rub"],
    }


# ---------------------------------------------------------------------
# Список проектов и создание нового
# ---------------------------------------------------------------------

@router.get("/projects")
def projects_list(
    request: Request,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Список проектов. Менеджер — свои; админ — все."""
    items = spec_service.list_projects(
        db,
        user_id=user.id,
        is_admin=user.is_admin,
    )
    return templates.TemplateResponse(
        request,
        "projects_list.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "items":      items,
        },
    )


@router.post("/projects")
def projects_create(
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Создаёт пустой проект и редиректит в него."""
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")
    name = web_service.format_project_name(None)
    pid = spec_service.create_empty_project(db, user_id=user.id, name=name)
    return RedirectResponse(url=f"/project/{pid}", status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------
# Страница проекта
# ---------------------------------------------------------------------

@router.get("/project/{project_id}")
def project_detail(
    project_id: int,
    request: Request,
    highlight: int | None = None,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Детальная страница проекта: список конфигураций + спецификация."""
    project = _load_project_or_raise(db, project_id=project_id, user=user)

    queries = spec_service.list_queries_of_project(db, project_id=project_id)
    spec_items = spec_service.list_spec_items(db, project_id=project_id)
    totals = spec_service.spec_totals(spec_items)
    selected = spec_service.selected_set(spec_items)

    # Собираем «плоский» список всех вариантов всех конфигураций —
    # один пакетный enrich по всей странице.
    all_variants: list[dict] = []
    for q in queries:
        q["variants"] = _prepare_variants(q.get("build_result"))
        q["refusal_reason"] = (q.get("build_result") or {}).get("refusal_reason")
        all_variants.extend(q["variants"])
    if all_variants:
        enrich_variants_with_specs(all_variants, db)

    return templates.TemplateResponse(
        request,
        "project_detail.html",
        {
            "user":            user,
            "csrf_token":      get_csrf_token(request),
            "project":         project,
            "queries":         queries,
            "spec_items":      spec_items,
            "spec_totals":     totals,
            "selected":        selected,
            "highlight":       highlight,
            "category_order":  _CATEGORY_ORDER,
            "category_label":  _CATEGORY_LABELS,
        },
    )


# ---------------------------------------------------------------------
# Переименование и удаление
# ---------------------------------------------------------------------

@router.post("/project/{project_id}/rename")
def project_rename(
    project_id: int,
    request: Request,
    name: str = Form(...),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    _load_project_or_raise(db, project_id=project_id, user=user)
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")
    clean = (name or "").strip()
    if not clean:
        raise HTTPException(status_code=400, detail="Имя проекта не может быть пустым.")
    if len(clean) > 300:
        clean = clean[:300]
    spec_service.rename_project(db, project_id=project_id, name=clean)
    return RedirectResponse(url=f"/project/{project_id}", status_code=status.HTTP_302_FOUND)


@router.post("/project/{project_id}/delete")
def project_delete(
    project_id: int,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    _load_project_or_raise(db, project_id=project_id, user=user)
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")
    spec_service.delete_project(db, project_id=project_id)
    return RedirectResponse(url="/projects", status_code=status.HTTP_302_FOUND)


@router.post("/project/{project_id}/query/{query_id}/delete")
def project_query_delete(
    project_id: int,
    query_id: int,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Удаляет конфигурацию из проекта. Спец-позиции с этим query_id
    уйдут каскадом (ON DELETE CASCADE)."""
    _load_project_or_raise(db, project_id=project_id, user=user)
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    # Проверяем, что query принадлежит этому проекту (защита от
    # подмены query_id в URL).
    from sqlalchemy import text as _t
    row = db.execute(
        _t("SELECT project_id FROM queries WHERE id = :qid"),
        {"qid": query_id},
    ).first()
    if row is None or int(row.project_id) != int(project_id):
        raise HTTPException(status_code=404, detail="Конфигурация не найдена.")

    spec_service.delete_query(db, query_id=query_id)
    # После удаления запроса позиции спецификации могут опустеть —
    # пронумеруем оставшиеся.
    spec_service._renumber_positions(db, project_id)
    db.commit()
    return RedirectResponse(url=f"/project/{project_id}", status_code=status.HTTP_302_FOUND)


# ---------------------------------------------------------------------
# Новая конфигурация в существующий проект
# ---------------------------------------------------------------------

@router.get("/project/{project_id}/new_query")
def project_new_query_form(
    project_id: int,
    request: Request,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    project = _load_project_or_raise(db, project_id=project_id, user=user)
    return templates.TemplateResponse(
        request,
        "project_new_query.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "project":    project,
            "error":      request.session.pop("flash_error", None),
        },
    )


@router.post("/project/{project_id}/new_query")
def project_new_query_submit(
    project_id: int,
    request: Request,
    raw_text: str = Form(...),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Создаёт query в рамках существующего проекта. Логика
    параллельна main_router.query_submit, но проект уже есть."""
    _load_project_or_raise(db, project_id=project_id, user=user)
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    raw_clean = (raw_text or "").strip()
    if not raw_clean:
        request.session["flash_error"] = "Введите текст запроса."
        return RedirectResponse(
            url=f"/project/{project_id}/new_query",
            status_code=status.HTTP_302_FOUND,
        )

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

    started_at = datetime.now()
    resp = None
    err_msg = None
    try:
        resp = process_query(raw_clean)
    except Exception as exc:
        logger.exception("process_query упал: %s", exc)
        class_name = type(exc).__name__
        if class_name == "RateLimitError":
            err_msg = "Сервис временно перегружен (OpenAI rate-limit). Попробуйте через минуту."
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
    budget_guard.upsert_daily_log(db)
    return RedirectResponse(
        url=f"/project/{project_id}?highlight={qid}",
        status_code=status.HTTP_302_FOUND,
    )


# ---------------------------------------------------------------------
# AJAX: select / deselect / update_quantity
# ---------------------------------------------------------------------

@router.post("/project/{project_id}/select")
def project_select(
    project_id: int,
    request: Request,
    payload: dict = Body(...),
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    _verify_csrf_ajax(request)
    _load_project_or_raise(db, project_id=project_id, user=user)

    query_id = payload.get("query_id")
    manufacturer = payload.get("variant_manufacturer")
    quantity = payload.get("quantity", 1)
    if not isinstance(query_id, int) or not isinstance(manufacturer, str):
        raise HTTPException(status_code=400, detail="Неверные параметры.")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Количество должно быть числом.")

    try:
        spec_service.select_variant(
            db,
            project_id=project_id,
            query_id=query_id,
            manufacturer=manufacturer,
            quantity=quantity,
        )
    except spec_service.SpecError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return JSONResponse(_spec_payload(db, project_id))


@router.post("/project/{project_id}/deselect")
def project_deselect(
    project_id: int,
    request: Request,
    payload: dict = Body(...),
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    _verify_csrf_ajax(request)
    _load_project_or_raise(db, project_id=project_id, user=user)

    query_id = payload.get("query_id")
    manufacturer = payload.get("variant_manufacturer")
    if not isinstance(query_id, int) or not isinstance(manufacturer, str):
        raise HTTPException(status_code=400, detail="Неверные параметры.")

    spec_service.deselect_variant(
        db,
        project_id=project_id,
        query_id=query_id,
        manufacturer=manufacturer,
    )
    return JSONResponse(_spec_payload(db, project_id))


@router.post("/project/{project_id}/update_quantity")
def project_update_quantity(
    project_id: int,
    request: Request,
    payload: dict = Body(...),
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    _verify_csrf_ajax(request)
    _load_project_or_raise(db, project_id=project_id, user=user)

    query_id = payload.get("query_id")
    manufacturer = payload.get("variant_manufacturer")
    quantity = payload.get("quantity")
    if not isinstance(query_id, int) or not isinstance(manufacturer, str):
        raise HTTPException(status_code=400, detail="Неверные параметры.")
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Количество должно быть числом.")

    try:
        spec_service.update_quantity(
            db,
            project_id=project_id,
            query_id=query_id,
            manufacturer=manufacturer,
            quantity=quantity,
        )
    except spec_service.SpecError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return JSONResponse(_spec_payload(db, project_id))


# ---------------------------------------------------------------------
# Этап 9А.2.3: reoptimize спецификации (полный пересбор) + rollback
# ---------------------------------------------------------------------

def _reoptimize_full_response(db: Session, project_id: int, result) -> JSONResponse:
    payload = _spec_payload(db, project_id)
    payload["recalc"] = {
        "items":         [spec_recalc.delta_to_dict(d) for d in result.items],
        "changed_count": result.changed_count,
        "total_count":   result.total_count,
    }
    return JSONResponse(payload)


@router.post("/project/{project_id}/spec/reoptimize")
def project_spec_reoptimize(
    project_id: int,
    request: Request,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Полный пересбор всех позиций: builder.build_config с теми же входами."""
    _verify_csrf_ajax(request)
    _load_project_or_raise(db, project_id=project_id, user=user)
    result = spec_recalc.reoptimize_specification(db, project_id=project_id)
    return _reoptimize_full_response(db, project_id, result)


@router.post("/project/{project_id}/spec/recalc")
def project_spec_recalc(
    project_id: int,
    request: Request,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Алиас старой кнопки «Пересчитать цены» — теперь делает reoptimize.
    Сохранён, чтобы старые закладки и тесты этапа 9А.2.1 продолжали работать."""
    _verify_csrf_ajax(request)
    _load_project_or_raise(db, project_id=project_id, user=user)
    result = spec_recalc.reoptimize_specification(db, project_id=project_id)
    return _reoptimize_full_response(db, project_id, result)


@router.post("/project/{project_id}/spec/{item_id}/reoptimize")
def project_spec_item_reoptimize(
    project_id: int,
    item_id: int,
    request: Request,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Точечный reoptimize одной позиции."""
    _verify_csrf_ajax(request)
    _load_project_or_raise(db, project_id=project_id, user=user)

    from sqlalchemy import text as _t
    row = db.execute(
        _t("SELECT project_id FROM specification_items WHERE id = :id"),
        {"id": item_id},
    ).first()
    if row is None or int(row.project_id) != int(project_id):
        raise HTTPException(status_code=404, detail="Позиция спецификации не найдена.")

    delta = spec_recalc.reoptimize_specification_item(db, item_id=item_id)
    if delta is None:
        raise HTTPException(status_code=404, detail="Позиция спецификации не найдена.")

    payload = _spec_payload(db, project_id)
    payload["recalc_item"] = spec_recalc.delta_to_dict(delta)
    return JSONResponse(payload)


@router.post("/project/{project_id}/spec/{item_id}/recalc")
def project_spec_item_recalc(
    project_id: int,
    item_id: int,
    request: Request,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Алиас точечного пересчёта (этап 9А.2.1) — делает reoptimize."""
    return project_spec_item_reoptimize(
        project_id=project_id, item_id=item_id, request=request,
        user=user, db=db,
    )


@router.post("/project/{project_id}/spec/rollback")
def project_spec_rollback(
    project_id: int,
    request: Request,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Откат последнего reoptimize по всем позициям проекта."""
    _verify_csrf_ajax(request)
    _load_project_or_raise(db, project_id=project_id, user=user)
    rolled = spec_recalc.rollback_specification(db, project_id=project_id)
    payload = _spec_payload(db, project_id)
    payload["rolled_back"] = rolled
    return JSONResponse(payload)


@router.post("/project/{project_id}/spec/{item_id}/rollback")
def project_spec_item_rollback(
    project_id: int,
    item_id: int,
    request: Request,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Откат точечного reoptimize одной позиции."""
    _verify_csrf_ajax(request)
    _load_project_or_raise(db, project_id=project_id, user=user)

    from sqlalchemy import text as _t
    row = db.execute(
        _t("SELECT project_id FROM specification_items WHERE id = :id"),
        {"id": item_id},
    ).first()
    if row is None or int(row.project_id) != int(project_id):
        raise HTTPException(status_code=404, detail="Позиция спецификации не найдена.")

    ok = spec_recalc.rollback_specification_item(db, item_id=item_id)
    payload = _spec_payload(db, project_id)
    payload["rolled_back"] = 1 if ok else 0
    return JSONResponse(payload)


# Задел на этап 7 — перестановка позиций вручную (drag & drop).
@router.post("/project/{project_id}/reorder")
def project_reorder(
    project_id: int,
    request: Request,
    payload: dict = Body(...),
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    _verify_csrf_ajax(request)
    _load_project_or_raise(db, project_id=project_id, user=user)

    order = payload.get("order") or []
    if not isinstance(order, list):
        raise HTTPException(status_code=400, detail="order должен быть массивом id.")

    from sqlalchemy import text as _t
    for pos, item_id in enumerate(order, start=1):
        db.execute(
            _t(
                "UPDATE specification_items SET position = :p "
                "WHERE id = :id AND project_id = :pid"
            ),
            {"p": pos, "id": int(item_id), "pid": project_id},
        )
    db.commit()
    return JSONResponse(_spec_payload(db, project_id))
