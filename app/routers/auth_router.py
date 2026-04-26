# Роутер авторизации: /login (GET/POST), /logout (POST).

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    get_csrf_token,
    get_user_by_login,
    login_session,
    logout_session,
    verify_csrf,
    verify_password,
)
from app.database import get_db
from app.templating import templates


router = APIRouter()


@router.get("/login")
def login_form(request: Request):
    """Страница входа. Если уже залогинен — редирект на /."""
    if request.session.get("user_id"):
        return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "csrf_token": get_csrf_token(request),
            "error":      None,
        },
    )


@router.post("/login")
def login_submit(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    csrf_token: str = Form(""),
    db: Session = Depends(get_db),
):
    """Принимает форму логина, проверяет пароль, ставит сессию."""
    # 1. CSRF
    if not verify_csrf(request, csrf_token):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "csrf_token": get_csrf_token(request),
                "error":      "Сессия истекла. Попробуйте войти ещё раз.",
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
            },
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    user, _ph = found
    login_session(request, user)
    return RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form("")):
    """Закрывает сессию."""
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")
    logout_session(request)
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)
