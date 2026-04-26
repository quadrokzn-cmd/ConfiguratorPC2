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

from app.config import settings
from portal.templating import templates
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

    found = get_user_by_login(db, login_clean)
    if found is None or not verify_password(password, found[1]):
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
    return RedirectResponse(
        url=_safe_next_url(next),
        status_code=status.HTTP_302_FOUND,
    )


# --- /logout ------------------------------------------------------------

@router.get("/logout")
def logout_get(request: Request):
    """GET-вариант logout (по брифу 9Б.1). Без CSRF — это idempotent
    очистка сессии, и из конфигуратора иногда удобно дернуть просто
    через ссылку. POST-вариант ниже принимает CSRF — сохранён для
    обратной совместимости со старой кнопкой выхода в base.html
    конфигуратора."""
    logout_session(request)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


@router.post("/logout")
def logout_post(request: Request, csrf_token: str = Form("")):
    """POST-вариант logout. Совместимость со старой формой выхода
    в конфигураторе, которая шлёт csrf_token из сессии."""
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")
    logout_session(request)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
