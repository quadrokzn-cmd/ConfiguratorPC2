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

import logging
from urllib.parse import quote

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.auth import LoginRequiredRedirect, build_session_cookie_kwargs, get_user_by_id
from app.config import settings
from app.database import SessionLocal
from app.routers import (
    admin_router,
    export_router,
    main_router,
    mapping_router,
    project_router,
)
from app.scheduler import (
    ensure_initial_rate,
    init_scheduler,
    shutdown_scheduler,
)
from shared.permissions import has_permission


logger = logging.getLogger(__name__)

app = FastAPI(title="КВАДРО-ТЕХ: сервис-конфигуратор ПК")


@app.on_event("startup")
def _startup_scheduler() -> None:
    """Запускаем фоновые cron-задачи (обновление курса ЦБ).

    Старт обёрнут в флаг RUN_SCHEDULER (этап 10.1). На Railway-инстансах,
    где scheduler не нужен (например, будущие реплики), переменную
    оставляем пустой — задачи не дублируются.

    Если init упал (например, неправильный таймзонный конфиг) — логируем,
    но сервер всё равно стартует. Курс будет браться из БД, а scheduler'у
    можно перезапустить вручную.
    """
    if not settings.run_scheduler:
        logger.info("Планировщик отключён в этом инстансе (RUN_SCHEDULER!=1).")
        return
    try:
        ensure_initial_rate()
        init_scheduler()
        logger.info("Планировщик запущен.")
    except Exception as exc:
        logger.warning("Не удалось инициализировать scheduler: %s", exc)


@app.on_event("shutdown")
def _shutdown_scheduler() -> None:
    shutdown_scheduler()


# Permission middleware (этап 9Б.4). Второй уровень защиты после login:
# у залогиненного пользователя должна быть permissions["configurator"]=true,
# иначе конфигуратор НЕ ОТКРЫВАЕТСЯ — редирект на ${PORTAL_URL}/?denied=configurator.
# Без этого менеджер без прав мог зайти прямо по URL config.quadro.tatar/
# и обойти UI-фильтр на портале.
#
# Порядок регистрации middleware важен: starlette применяет user_middleware
# в обратном порядке (последний add_middleware — outermost). Чтобы внутри
# permission-middleware был доступен request.session, SessionMiddleware
# должен быть outermost — поэтому его add_middleware идёт ПОСЛЕ нашего.
_PERM_BYPASS_PATH_PREFIXES = ("/static", "/healthz", "/logout")


@app.middleware("http")
async def _enforce_configurator_permission(request: Request, call_next):
    """Блокирует доступ к конфигуратору пользователям без права 'configurator'.

    Логика:
      - служебные пути (/static, /healthz, /logout) и preflight OPTIONS —
        пропускаются без проверки;
      - не залогиненные тоже пропускаются: дальше отработает обычный
        require_login → LoginRequiredRedirect → 302 на портал/login;
      - залогиненный без права получает 403 JSON, если запрос ждёт JSON,
        иначе 302 на ${PORTAL_URL}/?denied=configurator (портал покажет баннер).

    admin всегда проходит — has_permission(admin, ..., ...) → True (см. shared/permissions.py).
    """
    path = request.url.path
    if request.method == "OPTIONS" or any(
        path.startswith(p) for p in _PERM_BYPASS_PATH_PREFIXES
    ):
        return await call_next(request)

    user_id = request.session.get("user_id")
    if not user_id:
        return await call_next(request)

    db = SessionLocal()
    try:
        user = get_user_by_id(db, int(user_id))
    finally:
        db.close()

    if user is None:
        # Сессия указывает на удалённого/деактивированного пользователя —
        # current_user-зависимость её почистит и редиректнет на login.
        return await call_next(request)

    if has_permission(user.role, user.permissions or {}, "configurator"):
        return await call_next(request)

    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={"detail": "Нет доступа к модулю «Конфигуратор ПК»."},
        )
    target = f"{settings.portal_url}/?denied=configurator"
    return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)


# Сессии — подписанные cookie. Кука и секрет общие с порталом
# (build_session_cookie_kwargs живёт в shared/auth.py), чтобы login на
# app.quadro.tatar пускал сразу и сюда.
app.add_middleware(SessionMiddleware, **build_session_cookie_kwargs(settings))


@app.exception_handler(LoginRequiredRedirect)
def _redirect_to_login(request: Request, exc: LoginRequiredRedirect):
    """Этап 9Б.1: неавторизованный заход на защищённый роут конфигуратора
    → 302 на ${PORTAL_URL}/login?next=<encoded full URL>.

    Form логина теперь живёт в портале (portal/routers/auth.py).
    `next` — полный URL текущего запроса (с query-string), чтобы после
    логина пользователь попал ровно туда, куда шёл. Защита от open
    redirect — на стороне портала: ALLOWED_REDIRECT_HOSTS whitelist."""
    next_url = quote(str(request.url), safe="")
    target = f"{settings.portal_url}/login?next={next_url}"
    return RedirectResponse(url=target, status_code=status.HTTP_302_FOUND)


# Статические файлы (JS конфигуратора спецификации, картинки и т. п.).
app.mount("/static", StaticFiles(directory="static"), name="static")

# Роутеры. /login и /logout удалены — переехали в портал.
# /admin/users тоже переехал; в admin_router.py остался только редирект
# на portal_url для совместимости со старыми ссылками.
# /admin/mapping раньше /admin, иначе более общий роутер съест префикс.
app.include_router(mapping_router.router)
app.include_router(admin_router.router)     # /admin/* — подключаем раньше /
app.include_router(project_router.router)   # /projects, /project/*
app.include_router(export_router.router)    # /project/*/export/*
app.include_router(main_router.router)


@app.get("/healthz")
def healthz():
    """Liveness-проверка для Railway. Без авторизации.

    Делает один лёгкий SELECT 1 в БД. Если БД отвечает — 200/{db: ok}.
    Если падает — 503/{db: error} + текст ошибки в логе. Сам HTTP-сервер
    при этом продолжает отдавать 503, так что Railway не убьёт инстанс
    из-за единичной сетевой ошибки.
    """
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
