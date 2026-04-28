# /admin/users портала: список менеджеров, создание, активация/
# деактивация, редактирование permissions (этап 9Б.1).
#
# Перенос полностью из конфигуратора (app/routers/admin_router.py)
# с добавлениями:
#   - чекбоксы по MODULE_KEYS (в UI 9Б.1 показываем только
#     "configurator" — остальные ключи скрыты, но логика готова);
#   - permissions при создании по умолчанию {"configurator": True};
#   - отдельный POST /admin/users/{id}/permissions для перезаписи прав.

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from portal.templating import templates
from shared.auth import (
    AuthUser,
    get_csrf_token,
    hash_password,
    require_admin,
    verify_csrf,
)
from shared.db import get_db
from shared.permissions import MODULE_KEYS
from shared import user_repo


router = APIRouter(prefix="/admin")


# В 9Б.1 в UI чекбоксов отрисовываем только "configurator" — остальные
# модули ещё не имеют ни одного маршрута. Список MODULE_KEYS остаётся
# источником истины для permissions JSONB; UI расширим в 9Б.2.
_VISIBLE_MODULE_KEYS: list[str] = ["configurator"]


@router.get("/users")
def users_list(
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Список пользователей с формой создания и чекбоксами модулей."""
    users = user_repo.list_users(db)
    return templates.TemplateResponse(
        request,
        "admin/users.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "users":      users,
            "module_keys":         MODULE_KEYS,
            "visible_module_keys": _VISIBLE_MODULE_KEYS,
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
    """Создаёт нового менеджера. По умолчанию permissions = {configurator: true}."""
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    login_clean = (login or "").strip()
    name_clean = (name or "").strip()
    if not login_clean or not name_clean or not password:
        request.session["flash_error"] = "Заполните логин, имя и пароль."
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)

    if len(password) < 6:
        request.session["flash_error"] = "Пароль должен быть не короче 6 символов."
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)

    try:
        user_repo.create_manager(
            db,
            login=login_clean,
            password_hash=hash_password(password),
            name=name_clean,
            # Permissions по умолчанию задаются внутри create_manager —
            # {"configurator": True}. Передавать явно не нужно.
        )
    except ValueError as exc:
        if str(exc) == "login_taken":
            request.session["flash_error"] = f"Логин «{login_clean}» уже занят."
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
    if int(user_id) == int(user.id):
        request.session["flash_error"] = "Нельзя деактивировать собственную учётку."
        return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)
    new_state = user_repo.toggle_user_active(db, user_id)
    request.session["flash_info"] = (
        f"Пользователь переведён в состояние: {'активен' if new_state else 'отключён'}."
    )
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)


@router.post("/users/{user_id}/permissions")
async def users_update_permissions(
    user_id: int,
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Перезаписывает permissions: то, что пришло чекбоксами — true,
    остальные ключи (даже невидимые в UI) — false. Чтобы случайно не
    стереть «невидимые» права, передавай скрытые input'ы со значением
    "1" для тех модулей, которые надо сохранить (см. шаблон users.html).
    """
    form = await request.form()
    csrf_token = form.get("csrf_token", "")
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    perms: dict[str, bool] = {}
    for key in MODULE_KEYS:
        # Чекбокс с name="permissions[<key>]" приходит как "1" если
        # отмечен; иначе вообще не приходит. Скрытые поля для невидимых
        # модулей шаблон может прислать тоже — обработка одинаковая.
        raw = form.get(f"permissions[{key}]")
        perms[key] = bool(raw) and str(raw).lower() in ("1", "true", "on", "yes")

    ok = user_repo.update_permissions(db, user_id, perms)
    if not ok:
        request.session["flash_error"] = "Пользователь не найден."
    else:
        request.session["flash_info"] = "Права обновлены."
    return RedirectResponse(url="/admin/users", status_code=status.HTTP_302_FOUND)
