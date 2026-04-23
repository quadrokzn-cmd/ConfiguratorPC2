# Точка входа FastAPI-приложения (этап 5).
#
# ВАЖНО: load_dotenv() ДОЛЖЕН быть первым исполняемым кодом модуля —
# до любых import из app.*, иначе app.config и app.database прочитают
# пустые/дефолтные значения до того, как .env попадёт в os.environ,
# и мы получим падения вида «password authentication failed for user 'user'»
# или «Не задан OPENAI_API_KEY».

from dotenv import load_dotenv

load_dotenv()

# ---- Дальше уже можно импортировать app.* ----

from fastapi import FastAPI, Request, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.auth import LoginRequiredRedirect
from app.config import settings
from app.routers import (
    admin_router,
    auth_router,
    main_router,
    mapping_router,
    project_router,
)


app = FastAPI(title="КВАДРО-ТЕХ: сервис-конфигуратор ПК")


# Сессии — подписанные cookie. SESSION_SECRET_KEY задаётся в .env.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    session_cookie="kt_session",
    # 30 дней; менеджер вряд ли хочет логиниться каждую неделю.
    max_age=60 * 60 * 24 * 30,
    same_site="lax",
    https_only=False,   # локально https нет; в проде будет True
)


@app.exception_handler(LoginRequiredRedirect)
def _redirect_to_login(request: Request, exc: LoginRequiredRedirect):
    """Неавторизованный заход на защищённый роут → /login."""
    return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)


# Статические файлы (JS конфигуратора спецификации, картинки и т. п.).
app.mount("/static", StaticFiles(directory="static"), name="static")

# Роутеры
app.include_router(auth_router.router)
# /admin/mapping раньше /admin, иначе более общий роутер съест префикс.
app.include_router(mapping_router.router)
app.include_router(admin_router.router)     # /admin/* — подключаем раньше /
app.include_router(project_router.router)   # /projects, /project/*
app.include_router(main_router.router)


@app.get("/healthz")
def healthz():
    """Проверка живости. Не требует авторизации."""
    return {"status": "ok"}
