# Точка входа FastAPI-приложения (этап 5).
#
# ВАЖНО: load_dotenv() ДОЛЖЕН быть первым исполняемым кодом модуля —
# до любых import из app.*, иначе app.config и app.database прочитают
# пустые/дефолтные значения до того, как .env попадёт в os.environ,
# и мы получим падения вида «password authentication failed for user 'user'»
# или «Не задан OPENAI_API_KEY».

from dotenv import load_dotenv

load_dotenv()

# ---- Sentry init ДОЛЖЕН быть до импорта роутеров (этап 9В.3) ----
# Чтобы FastApiIntegration перехватил исключения с самого старта,
# init_sentry зовём сразу после load_dotenv() и до создания приложения.
# Без DSN init вернёт False и Sentry просто выключен — локально это норма.
from shared.sentry_init import init_sentry

init_sentry("configurator")

# ---- Дальше уже можно импортировать app.* ----

import logging
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.auth import LoginRequiredRedirect, build_session_cookie_kwargs
from app.config import settings
from app.database import SessionLocal
from app.routers import admin_router
from app.scheduler import (
    ensure_initial_rate,
    init_scheduler,
    shutdown_scheduler,
)


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


# UI-4 (Путь B, 2026-05-11): Permission middleware
# _enforce_configurator_permission удалена — конфигуратор переехал в
# /configurator/* портала, и право permissions['configurator']
# проверяется теперь точечно через Depends require_configurator_access
# в portal/dependencies/configurator_access.py. Этот процесс
# (config.quadro.tatar) после UI-4 обслуживает только 301-редиректы
# на portal + /healthz + /admin/* (legacy-dashboard и редиректы).
#
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

# UI-2 (Путь B, 2026-05-11): /admin/{suppliers,components,mapping}
# переехали в портал (portal/routers/databases). Здесь оставлены три
# точечных catch-all GET-редиректа на новые URL, чтобы старые закладки
# у менеджеров продолжали работать. Сами роуты в admin_router.py и
# mapping_router.py удалены (mapping_router.py больше нет, обработчики
# suppliers/components вырезаны из admin_router.py).
#
# Использован {rest:path} с дефолтом "" — это и корень /admin/suppliers,
# и любой sub-route (/admin/suppliers/15/edit) одним обработчиком.
# Только GET — POST-формы редактирования в конфигураторе больше не
# отдаются (страница списка/детали — это GET; они и редиректнутся,
# дальше менеджер уже на портале сабмитит).

def _redirect_to_portal_databases(section: str, rest: str = "") -> RedirectResponse:
    """Собирает 301-редирект на portal_url/databases/<section>[/<rest>].

    Хост (settings.portal_url) подменяется в зависимости от окружения
    (prod / pre-prod / локально), хардкода нет."""
    suffix = f"/{rest}" if rest else ""
    return RedirectResponse(
        url=f"{settings.portal_url}/databases/{section}{suffix}",
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )


@app.get("/admin/suppliers")
def _redirect_admin_suppliers_root():
    return _redirect_to_portal_databases("suppliers")


@app.get("/admin/suppliers/{rest:path}")
def _redirect_admin_suppliers_sub(rest: str):
    return _redirect_to_portal_databases("suppliers", rest)


@app.get("/admin/components")
def _redirect_admin_components_root():
    return _redirect_to_portal_databases("components")


@app.get("/admin/components/{rest:path}")
def _redirect_admin_components_sub(rest: str):
    return _redirect_to_portal_databases("components", rest)


@app.get("/admin/mapping")
def _redirect_admin_mapping_root():
    return _redirect_to_portal_databases("mapping")


@app.get("/admin/mapping/{rest:path}")
def _redirect_admin_mapping_sub(rest: str):
    return _redirect_to_portal_databases("mapping", rest)


# Роутеры. /login и /logout удалены — переехали в портал.
# /admin/users тоже переехал; в admin_router.py остался редирект на portal_url
# для совместимости со старыми ссылками. /admin/suppliers, /admin/components,
# /admin/mapping переехали в портал на UI-2 — выше стоят 301-редиректы.
# UI-4 (Путь B, 2026-05-11): main_router, project_router, export_router
# переехали в portal/routers/configurator/. На config.quadro.tatar
# остаётся только admin_router (/admin dashboard, /admin/budget,
# /admin/queries, /admin/users → 302 на portal/settings/users) — он
# полностью уйдёт в UI-5 вместе со всем app/.
app.include_router(admin_router.router)     # /admin/* — подключаем раньше catch-all


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


# UI-4 (Путь B, 2026-05-11): catch-all 301-редиректы на портал.
# Конфигуратор переехал в portal/routers/configurator/* — здесь
# остаются только админ-страницы (admin_router выше) и публичные
# редиректы. Любой остальной URL уезжает на portal/configurator/*.
#
# ВАЖНО: catch-all регистрируется ПОСЛЕ admin_router.router и
# @app.get("/healthz") — иначе он перехватит /admin/* и /healthz.
# FastAPI matches routes в порядке добавления; если route совпал —
# дальше не идёт.

@app.get("/")
def _redirect_root_to_portal_configurator():
    """Корень config.quadro.tatar → portal/configurator/ (главная NLU-формы)."""
    return RedirectResponse(
        url=f"{settings.portal_url}/configurator/",
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )


@app.get("/{rest:path}")
def _redirect_to_portal_configurator(rest: str):
    """Catch-all 301: всё, что не /admin/* и не /healthz и не /static/*,
    отправляется на portal/configurator/<rest>. Старые закладки на
    config.quadro.tatar/projects, /history, /query продолжат работать.

    Только GET — POST-формы (если они существовали на старых страницах)
    дадут 404, но самих страниц с такими action в DOM больше нет (все
    шаблоны переехали и используют новые /configurator/* URL).

    /admin/* не редиректится: admin_router выше отдаёт /admin (dashboard),
    /admin/users (302 на portal/settings/users), /admin/budget, /admin/queries.
    Точечные 301 для /admin/{suppliers,components,mapping} зарегистрированы
    отдельно (UI-2). Любой другой /admin/* — 404 (так и было до UI-4):
    мы не хотим из этого процесса редиректить административные пути,
    которые живут только в портале (/admin/auto-price-loads, /admin/diagnostics
    и т.д. — это portal-роуты, на app никогда не существовали).
    """
    if rest.startswith("admin/") or rest == "admin":
        raise HTTPException(status_code=404)
    target = f"{settings.portal_url}/configurator/{rest}"
    return RedirectResponse(
        url=target,
        status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )
