# Auth-роуты портала (этап 9Б.1): /login (GET/POST), /logout (POST/GET).
#
# Раньше login/logout были в конфигураторе (app/routers/auth_router.py).
# С появлением портала логин — единый вход в семейство сервисов
# КВАДРО-ТЕХ; конфигуратор сюда редиректит неавторизованных.
#
# Защита от open redirect: ?next=URL разрешается только в whitelist
# (settings.allowed_redirect_hosts). Список читается из ALLOWED_
# REDIRECT_HOSTS, на локалке: localhost:8080,localhost:8081.

from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from shared.config import settings
from portal.templating import templates
from shared.audit import extract_request_meta, write_audit
from shared.audit_actions import (
    ACTION_LOGIN_FAILED,
    ACTION_LOGIN_SUCCESS,
    ACTION_LOGOUT,
)
from shared.auth import (
    get_csrf_token,
    get_user_by_login,
    login_session,
    logout_session,
    verify_csrf,
    verify_password,
)
from shared.db import get_db


router = APIRouter()


# --- next-redirect whitelist --------------------------------------------

def _safe_next_url(next_raw: str | None) -> str:
    """Возвращает next_raw если он в whitelist'е разрешённых хостов,
    иначе '/' (главная портала). Защита от open redirect.

    Правила:
      - пусто или None → '/';
      - относительный путь (без netloc) → разрешён, т.к. остаётся в портале;
      - абсолютный URL → проверяем netloc по settings.allowed_redirect_hosts.
    """
    if not next_raw:
        return "/"
    parsed = urlparse(next_raw)
    if not parsed.netloc:
        # Относительный путь. Разрешаем, но требуем чтобы начинался с '/' —
        # иначе можно подсунуть '//evil.com/...' (это парсится как
        # protocol-relative URL, netloc уже не пустой, но defense in depth).
        if next_raw.startswith("/") and not next_raw.startswith("//"):
            return next_raw
        return "/"
    if parsed.netloc in settings.allowed_redirect_hosts:
        return next_raw
    return "/"


# --- /login -------------------------------------------------------------

@router.get("/login")
def login_form(request: Request, next: str = ""):
    """Страница входа. Если уже залогинен — отдаём редирект на ?next=
    (с whitelist) или /."""
    if request.session.get("user_id"):
        return RedirectResponse(
            url=_safe_next_url(next),
            status_code=status.HTTP_302_FOUND,
        )
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "csrf_token": get_csrf_token(request),
            "error":      None,
            "next":       next or "",
        },
    )


@router.post("/login")
def login_submit(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
    next: str = Form(""),
    db: Session = Depends(get_db),
):
    """Принимает форму логина, проверяет пароль, ставит сессию,
    редиректит на ?next= (если в whitelist) либо на /."""
    if not verify_csrf(request, csrf_token):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "csrf_token": get_csrf_token(request),
                "error":      "Сессия истекла. Попробуйте войти ещё раз.",
                "next":       next,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    login_clean = (login or "").strip()
    if not login_clean or not password:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "csrf_token": get_csrf_token(request),
                "error":      "Введите логин и пароль.",
                "next":       next,
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    ip, ua = extract_request_meta(request)

    found = get_user_by_login(db, login_clean)
    if found is None or not verify_password(password, found[1]):
        # 9В.4: фиксируем неудачную попытку. Если логин найден, но пароль
        # неверный — пишем user_id, чтобы видеть «кого пытались взломать».
        # Если логин не найден — user_id остаётся None, attempted_login
        # сохраняется в payload (полезно для разбора брутфорса).
        attempted_user_id = found[0].id if found is not None else None
        attempted_login = found[0].login if found is not None else login_clean
        write_audit(
            action=ACTION_LOGIN_FAILED,
            service="portal",
            user_id=attempted_user_id,
            user_login=attempted_login if found is not None else None,
            payload={"attempted_login": login_clean},
            ip=ip,
            user_agent=ua,
        )
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "csrf_token": get_csrf_token(request),
                "error":      "Неверный логин или пароль.",
                "next":       next,
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user, _ph = found
    login_session(request, user)
    write_audit(
        action=ACTION_LOGIN_SUCCESS,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        ip=ip,
        user_agent=ua,
    )
    return RedirectResponse(
        url=_safe_next_url(next),
        status_code=status.HTTP_302_FOUND,
    )


# --- /logout ------------------------------------------------------------

def _audit_logout(request: Request, db: Session) -> None:
    """Пишет ACTION_LOGOUT с user_id из сессии (если он там был).
    Вызывать ДО logout_session — иначе сессия уже пуста."""
    user_id_raw = request.session.get("user_id")
    if not user_id_raw:
        return
    try:
        from shared.auth import get_user_by_id
        user = get_user_by_id(db, int(user_id_raw))
    except Exception:
        user = None
    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_LOGOUT,
        service="portal",
        user_id=int(user_id_raw),
        user_login=user.login if user is not None else None,
        ip=ip,
        user_agent=ua,
    )


@router.get("/logout")
def logout_get(request: Request, db: Session = Depends(get_db)):
    """GET-вариант logout (по брифу 9Б.1). Без CSRF — это idempotent
    очистка сессии, и из конфигуратора иногда удобно дернуть просто
    через ссылку. POST-вариант ниже принимает CSRF — сохранён для
    обратной совместимости со старой кнопкой выхода в base.html
    конфигуратора."""
    _audit_logout(request, db)
    logout_session(request)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


@router.post("/logout")
def logout_post(
    request: Request,
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    """POST-вариант logout. Совместимость со старой формой выхода
    в конфигураторе, которая шлёт csrf_token из сессии."""
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")
    _audit_logout(request, db)
    logout_session(request)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
