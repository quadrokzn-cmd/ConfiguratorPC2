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

# ---- Sentry init ДОЛЖЕН быть до импорта роутеров (этап 9В.3) ----
# Подробности — в комментарии к app/main.py.
from shared.sentry_init import init_sentry

init_sentry("portal")

# ---- Дальше уже можно импортировать app.* и shared.* ----

import logging
from urllib.parse import quote

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from portal.dependencies.configurator_access import ConfiguratorAccessDenied
from portal.routers import (
    admin_auctions,
    admin_auto_price,
    admin_diagnostics,
    admin_price_uploads,
    auctions,
    auth,
    home,
    nomenclature,
)
from portal.routers.configurator import (
    export as configurator_export,
    main as configurator_main,
    projects as configurator_projects,
)
from portal.routers.databases import (
    components as databases_components,
    mapping as databases_mapping,
    suppliers as databases_suppliers,
)
from portal.routers.settings import (
    audit_log as settings_audit_log,
    backups as settings_backups,
    users as settings_users,
)
from portal.scheduler import (
    ensure_initial_rate,
    init_scheduler,
    shutdown_scheduler,
)
from portal.scheduler import _is_enabled as _scheduler_enabled
from shared.auth import LoginRequiredRedirect, build_session_cookie_kwargs
from shared.db import SessionLocal


logger = logging.getLogger(__name__)

app = FastAPI(title="КВАДРО-ТЕХ: портал")


@app.on_event("startup")
def _startup_scheduler() -> None:
    """9В.2 + UI-4.5: фоновые задачи портала.

    9В.2 — ежедневный бекап БД на Backblaze B2 (03:00 МСК) + автозагрузка
    прайсов + аукционный ingest. UI-4.5 — cron обновления курса USD/RUB
    (перенесён из app/scheduler.py) и первичный init курса при пустой
    таблице exchange_rates.

    Активация — внутри scheduler'а: APP_ENV=production или
    RUN_BACKUP_SCHEDULER=1. На pytest эти env'ы по умолчанию пустые, и
    `_is_enabled()` возвращает False — никаких сетевых походов на ЦБ/B2
    из тестовых TestClient'ов.

    Если init упал — логируем и идём дальше: портал должен стартовать
    даже когда планировщик заглох.
    """
    try:
        init_scheduler()
    except Exception as exc:
        logger.warning("Не удалось инициализировать scheduler портала: %s", exc)

    # UI-4.5: если в exchange_rates пусто (новый контейнер / свежая БД) —
    # синхронно дёргаем ЦБ один раз, чтобы UI не висел с прочерком до
    # 08:30. Гейтится тем же _is_enabled() — на pytest не срабатывает.
    if _scheduler_enabled():
        try:
            ensure_initial_rate()
        except Exception as exc:
            logger.warning(
                "ensure_initial_rate упал (%s: %s)",
                type(exc).__name__, exc,
            )


@app.on_event("shutdown")
def _shutdown_scheduler() -> None:
    shutdown_scheduler()


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


# UI-4 (Путь B, 2026-05-11): scoped-проверка доступа к модулю
# «Конфигуратор ПК». Раньше это была глобальная middleware
# _enforce_configurator_permission в app/main.py — теперь Depends
# require_configurator_access на трёх роутерах /configurator/*.
# Если у залогиненного пользователя нет permissions['configurator'],
# Depends поднимает ConfiguratorAccessDenied; здесь превращаем его в
# 302 на главную портала с query ?denied=configurator — на главной
# рендерится баннер «нет доступа к модулю».
@app.exception_handler(ConfiguratorAccessDenied)
def _redirect_configurator_denied(request: Request, exc: ConfiguratorAccessDenied):
    return RedirectResponse(
        url="/?denied=configurator",
        status_code=status.HTTP_302_FOUND,
    )


# Статика общая с конфигуратором — шаблоны портала ссылаются на
# /static/dist/main.css. Файлы physically лежат в /app/static
# (Dockerfile.portal копирует static/ внутрь образа).
app.mount("/static", StaticFiles(directory="static"), name="static")


# Роутеры. Порядок: сначала auth (/login, /logout) — на нём нет require_login.
# Потом /admin — нужен только админам. Потом / — last resort.
app.include_router(auth.router)
# UI-3 (Путь B, 2026-05-11): «Настройки» — пользователи, бэкапы, журнал
# действий. Префикс /settings/* (старые /admin/{users,backups,audit}
# отдают 301 на новые URL — см. блок ниже).
app.include_router(settings_users.router)
app.include_router(settings_backups.router)
app.include_router(settings_audit_log.router)
app.include_router(admin_price_uploads.router)
app.include_router(admin_auto_price.router)
app.include_router(admin_auctions.router)
app.include_router(admin_diagnostics.router)
app.include_router(auctions.router)
app.include_router(nomenclature.router)
# UI-2 (Путь B, 2026-05-11): «Базы данных» — поставщики, комплектующие
# для ПК и очередь маппинга, переехавшие из конфигуратора.
app.include_router(databases_suppliers.router)
app.include_router(databases_components.router)
app.include_router(databases_mapping.router)
# UI-4 (Путь B, 2026-05-11): «Конфигуратор ПК» — NLU-форма, проекты и
# экспорт КП. Префикс /configurator/. Раньше жили в app/routers/*
# на config.quadro.tatar. Со старого хоста стоит catch-all 301 в
# app/main.py.
app.include_router(configurator_main.router)
app.include_router(configurator_projects.router)
app.include_router(configurator_export.router)


# UI-3 (Путь B, 2026-05-11): 301-редиректы со старых URL раздела
# «Настройки» на новые /settings/*. Старые URL жили в portal под
# префиксом /admin (admin_users / admin_backups / admin_audit), теперь
# переехали. Используем три точечных catch-all'а (по разделу) + по
# корневому handler'у на каждый, чтобы НЕ задеть соседей:
# /admin/price-uploads, /admin/auto-price-loads, /admin/auctions,
# /admin/diagnostics остаются на месте до отдельных этапов.

def _settings_redirect(new_path: str) -> RedirectResponse:
    return RedirectResponse(
        url=new_path, status_code=status.HTTP_301_MOVED_PERMANENTLY,
    )


@app.get("/admin/users")
def _redirect_admin_users_root():
    return _settings_redirect("/settings/users")


@app.get("/admin/users/{rest:path}")
def _redirect_admin_users_sub(rest: str):
    suffix = f"/{rest}" if rest else ""
    return _settings_redirect(f"/settings/users{suffix}")


@app.get("/admin/backups")
def _redirect_admin_backups_root():
    return _settings_redirect("/settings/backups")


@app.get("/admin/backups/{rest:path}")
def _redirect_admin_backups_sub(rest: str):
    suffix = f"/{rest}" if rest else ""
    return _settings_redirect(f"/settings/backups{suffix}")


@app.get("/admin/audit")
def _redirect_admin_audit_root():
    return _settings_redirect("/settings/audit-log")


@app.get("/admin/audit/{rest:path}")
def _redirect_admin_audit_sub(rest: str):
    suffix = f"/{rest}" if rest else ""
    return _settings_redirect(f"/settings/audit-log{suffix}")


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
