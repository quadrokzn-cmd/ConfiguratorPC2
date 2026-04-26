# Точка входа FastAPI-приложения «Портал» (этап 9Б.1).
#
# Запускается отдельным процессом на отдельном порту (локально 8081),
# на Railway — отдельным сервисом (Dockerfile.portal). С конфигуратором
# делит:
#   - PostgreSQL (одна БД, одна таблица users);
#   - кодовую базу shared/ (auth, db, permissions, user_repo);
#   - подписанные сессионные cookie (одинаковые secret_key и имя cookie
#     "kt_session"; на production cookie выставляется на .quadro.tatar
#     через APP_COOKIE_DOMAIN).
#
# Шаблоны портала минимальные — это «архитектурный скелет». Дизайн
# делается в подэтапе 9Б.2.

from dotenv import load_dotenv

load_dotenv()

# ---- Дальше уже можно импортировать app.* и shared.* ----

import logging
from urllib.parse import quote

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from portal.routers import admin_users, auth, home
from shared.auth import LoginRequiredRedirect, build_session_cookie_kwargs
from shared.db import SessionLocal


logger = logging.getLogger(__name__)

app = FastAPI(title="КВАДРО-ТЕХ: портал")


# Сессии — те же подписанные cookie, что и в конфигураторе.
app.add_middleware(SessionMiddleware, **build_session_cookie_kwargs(settings))


@app.exception_handler(LoginRequiredRedirect)
def _redirect_to_login(request: Request, exc: LoginRequiredRedirect):
    """Неавторизованный заход на защищённую страницу портала → /login
    с next=<полный URL текущего запроса>. После успешного логина
    портал отправит обратно (если хост в whitelist)."""
    next_url = quote(str(request.url), safe="")
    return RedirectResponse(
        url=f"/login?next={next_url}",
        status_code=status.HTTP_302_FOUND,
    )


# Статика общая с конфигуратором — шаблоны портала ссылаются на
# /static/dist/main.css. Файлы physically лежат в /app/static
# (Dockerfile.portal копирует static/ внутрь образа).
app.mount("/static", StaticFiles(directory="static"), name="static")


# Роутеры. Порядок: сначала auth (/login, /logout) — на нём нет require_login.
# Потом /admin — нужен только админам. Потом / — last resort.
app.include_router(auth.router)
app.include_router(admin_users.router)
app.include_router(home.router)


@app.get("/healthz")
def healthz():
    """Liveness-проверка для Railway. Без авторизации, тот же ответ
    что у конфигуратора — {"status":"ok","db":"ok"} либо 503."""
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "db": "ok"}
    except Exception as exc:
        logger.warning("healthz: проверка БД не прошла: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "error", "db": "error"},
        )
    finally:
        db.close()
