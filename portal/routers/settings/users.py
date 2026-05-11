# /settings/users портала: список менеджеров, создание, активация/
# деактивация, редактирование permissions.
#
# Этап UI-3 (Путь B, 2026-05-11): переехал из portal/routers/admin_users.py
# (старый префикс /admin/users) — логика без изменений, только URL.
# Перенос выполнен в рамках оформления раздела «Настройки».

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from portal.templating import templates
from shared.audit import extract_request_meta, write_audit
from shared.audit_actions import (
    ACTION_USER_CREATE,
    ACTION_USER_DELETE_PERMANENT,
    ACTION_USER_PERM_CHANGE,
    ACTION_USER_ROLE_CHANGE,
    ACTION_USER_TOGGLE_ACTIVE,
)
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


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/settings")


# Видимые в UI чекбоксы: configurator (с этапа 9Б.1) + 3 ключа аукционов
# (этап 9a слияния QT↔C-PC2). Остальные ключи (`kp_form`, `mail_agent`,
# `dashboard`) пока без маршрутов — добавятся вместе с UI этих модулей.
_VISIBLE_MODULE_KEYS: list[str] = [
    "configurator",
    "auctions",
    "auctions_edit_status",
    "auctions_edit_settings",
]

_VALID_ROLES: frozenset[str] = frozenset({"admin", "manager"})


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
        "settings/users.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "users":      users,
            "module_keys":         MODULE_KEYS,
            "visible_module_keys": _VISIBLE_MODULE_KEYS,
            # admin_count нужен шаблону, чтобы скрыть кнопку «Удалить
            # навсегда» для последнего админа (UI-защита; серверная всё
            # равно стоит в users_delete_permanent).
            "admin_count": user_repo.count_admins(db),
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
    role: str = Form("manager"),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Создаёт нового пользователя. По умолчанию role=manager и
    permissions={configurator: true}; для admin permissions={} (admin
    видит всё)."""
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    login_clean = (login or "").strip()
    name_clean = (name or "").strip()
    if not login_clean or not name_clean or not password:
        request.session["flash_error"] = "Заполните логин, имя и пароль."
        return RedirectResponse(url="/settings/users", status_code=status.HTTP_302_FOUND)

    if len(password) < 6:
        request.session["flash_error"] = "Пароль должен быть не короче 6 символов."
        return RedirectResponse(url="/settings/users", status_code=status.HTTP_302_FOUND)

    role_clean = (role or "manager").strip().lower()
    if role_clean not in _VALID_ROLES:
        request.session["flash_error"] = "Недопустимая роль."
        return RedirectResponse(url="/settings/users", status_code=status.HTTP_302_FOUND)

    try:
        new_user_id = user_repo.create_manager(
            db,
            login=login_clean,
            password_hash=hash_password(password),
            name=name_clean,
            role=role_clean,
        )
    except ValueError as exc:
        if str(exc) == "login_taken":
            request.session["flash_error"] = f"Логин «{login_clean}» уже занят."
        else:
            request.session["flash_error"] = f"Ошибка создания: {exc}"
        return RedirectResponse(url="/settings/users", status_code=status.HTTP_302_FOUND)

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_USER_CREATE,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="user",
        target_id=new_user_id,
        payload={"login": login_clean, "role": role_clean, "name": name_clean},
        ip=ip,
        user_agent=ua,
    )

    role_label = "администратор" if role_clean == "admin" else "менеджер"
    request.session["flash_info"] = (
        f"Пользователь «{login_clean}» создан ({role_label})."
    )
    return RedirectResponse(url="/settings/users", status_code=status.HTTP_302_FOUND)


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
        return RedirectResponse(url="/settings/users", status_code=status.HTTP_302_FOUND)
    new_state = user_repo.toggle_user_active(db, user_id)
    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_USER_TOGGLE_ACTIVE,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="user",
        target_id=user_id,
        payload={"is_active": bool(new_state)},
        ip=ip,
        user_agent=ua,
    )
    request.session["flash_info"] = (
        f"Пользователь переведён в состояние: {'активен' if new_state else 'отключён'}."
    )
    return RedirectResponse(url="/settings/users", status_code=status.HTTP_302_FOUND)


@router.post("/users/{user_id}/role")
def users_set_role(
    user_id: int,
    request: Request,
    role: str = Form(...),
    csrf_token: str = Form(""),
    confirm_self_demotion: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Меняет роль пользователя (admin/manager). Защиты:
       - role должен быть из {admin, manager}, иначе 422;
       - target должен существовать, иначе 404;
       - role==current → no-op (302 на /settings/users без записи в БД);
       - нельзя понизить последнего админа (400);
       - самопонижение admin→manager требует confirm_self_demotion='true' (400 без флага).
    Все 400/404/422 идут в session.flash_error и редиректят на список —
    кроме CSRF (400 plain) и невалидного role (422), которые ловит
    автоматическая интеграция теста."""
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    role_clean = (role or "").strip().lower()
    if role_clean not in _VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Недопустимая роль. Допустимо: admin, manager.",
        )

    current_role = user_repo.get_role(db, user_id)
    if current_role is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден.")

    # No-op: ничего не меняем, просто отвечаем успехом.
    if current_role == role_clean:
        return RedirectResponse(url="/settings/users", status_code=status.HTTP_302_FOUND)

    is_demotion = current_role == "admin" and role_clean == "manager"

    # Защита от «остаться без админов».
    if is_demotion and user_repo.count_admins(db) <= 1:
        raise HTTPException(
            status_code=400,
            detail=(
                "Нельзя понизить последнего администратора. "
                "Сначала повысьте другого пользователя до администратора."
            ),
        )

    # Самопонижение требует подтверждения.
    if is_demotion and int(user_id) == int(user.id):
        confirm = (confirm_self_demotion or "").strip().lower()
        if confirm not in ("1", "true", "on", "yes"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Самопонижение требует подтверждения "
                    "(confirm_self_demotion=true)."
                ),
            )

    user_repo.set_role(db, user_id, role_clean)
    logger.info(
        "role change: actor_id=%s actor_login=%s -> target_id=%s "
        "from %s to %s",
        user.id, user.login, user_id, current_role, role_clean,
    )
    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_USER_ROLE_CHANGE,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="user",
        target_id=user_id,
        payload={"from": current_role, "to": role_clean},
        ip=ip,
        user_agent=ua,
    )

    role_label = "администратор" if role_clean == "admin" else "менеджер"
    request.session["flash_info"] = f"Роль обновлена: {role_label}."
    return RedirectResponse(url="/settings/users", status_code=status.HTTP_302_FOUND)


@router.post("/users/{user_id}/delete-permanent")
def users_delete_permanent(
    user_id: int,
    request: Request,
    csrf_token: str = Form(""),
    confirm_login: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Физически удаляет пользователя из БД (этап 9В.4.2). Защиты:
       - CSRF;
       - 404 если target не найден;
       - 400 если confirm_login не совпал с login (защита от случайного клика);
       - 400 если target.is_active=True (надо сначала отключить);
       - 400 на удаление самого себя (даже если каким-то образом current_user
         отключён — дублирующая защита);
       - 400 если target — последний admin (даже отключённый);
       - 400 если у target есть sent_emails (sent_by_user_id NOT NULL без
         ON DELETE — каскадно удалить лог писем поставщикам = потерять
         историю переписки, см. миграцию 011).
    После DELETE аудит-лог сохраняет user_id=NULL и user_login=<login>
    благодаря ON DELETE SET NULL миграции 018."""
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    target = user_repo.get_user_brief(db, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден.")

    if (confirm_login or "").strip() != target["login"]:
        raise HTTPException(
            status_code=400,
            detail="Подтверждение не совпало с login пользователя.",
        )

    # Порядок защит подобран так, чтобы каждую можно было независимо
    # достичь в HTTP-тесте:
    #   * last admin срабатывает раньше self, иначе тест «один админ
    #     пытается удалить сам себя» застрял бы на self-check и не
    #     дошёл бы до проверки last admin;
    #   * self раньше is_active — current_user всегда активен (иначе
    #     require_admin не пропустил бы), и без этого порядка self-check
    #     был бы недостижим в тесте.
    if target["role"] == "admin" and user_repo.count_admins(db) <= 1:
        raise HTTPException(
            status_code=400,
            detail="Нельзя удалить последнего администратора.",
        )

    if int(user.id) == int(target["id"]):
        raise HTTPException(
            status_code=400,
            detail="Нельзя удалить свой собственный аккаунт.",
        )

    if target["is_active"]:
        raise HTTPException(
            status_code=400,
            detail=(
                "Сначала отключите пользователя через «Отключить», "
                "потом удалите навсегда."
            ),
        )

    if user_repo.count_sent_emails_by_user(db, target["id"]) > 0:
        raise HTTPException(
            status_code=400,
            detail=(
                "Нельзя удалить пользователя, от имени которого отправлялись "
                "письма поставщикам — это сломает историю переписки. "
                "Оставьте учётку отключённой."
            ),
        )

    deleted_login = target["login"]
    deleted_role = target["role"]
    user_repo.delete_user_permanent(db, target["id"])

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_USER_DELETE_PERMANENT,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="user",
        target_id=target["id"],
        payload={
            "deleted_login": deleted_login,
            "deleted_role":  deleted_role,
            "was_active":    False,
        },
        ip=ip,
        user_agent=ua,
    )

    request.session["flash_info"] = (
        f"Пользователь «{deleted_login}» удалён навсегда."
    )
    return RedirectResponse(url="/settings/users", status_code=status.HTTP_302_FOUND)


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
        ip, ua = extract_request_meta(request)
        write_audit(
            action=ACTION_USER_PERM_CHANGE,
            service="portal",
            user_id=user.id,
            user_login=user.login,
            target_type="user",
            target_id=user_id,
            payload={"permissions": perms},
            ip=ip,
            user_agent=ua,
        )
        request.session["flash_info"] = "Права обновлены."
    return RedirectResponse(url="/settings/users", status_code=status.HTTP_302_FOUND)
