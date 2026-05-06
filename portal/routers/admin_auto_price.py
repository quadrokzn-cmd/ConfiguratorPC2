# /admin/auto-price-loads — UI автозагрузок прайсов (этап 12.3).
#
# Эндпоинты:
#   GET  /admin/auto-price-loads                — таблица 6 поставщиков,
#                                                  журнал последних 20 запусков.
#   POST /admin/auto-price-loads/<slug>/run     — ручной запуск runner'а.
#   POST /admin/auto-price-loads/<slug>/toggle  — enabled on/off.
#
# Доступ: require_admin (как и /admin/price-uploads). Внутри POST'ов
# дополнительный CSRF-check.

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.auto_price.base import (
    get_fetcher_class,
    list_registered_slugs,
)
from app.services.auto_price.runner import TooFrequentRunError, run_auto_load
from portal.templating import templates
from shared.audit import extract_request_meta, write_audit
from shared.audit_actions import (
    ACTION_AUTO_PRICE_RUN,
    ACTION_AUTO_PRICE_TOGGLE,
    ACTION_AUTO_PRICE_VIEW,
)
from shared.auth import AuthUser, get_csrf_token, require_admin, verify_csrf
from shared.db import get_db


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/admin/auto-price-loads")


# Канонические имена 6 поставщиков и человекочитаемые подписи каналов.
# Для slug, у которого зарегистрирован fetcher, channel_label берётся из
# карты ниже; иначе UI показывает «—» и блокирует кнопки.
SUPPLIERS_ORDERED: list[tuple[str, str]] = [
    ("treolan",      "Treolan"),
    ("ocs",          "OCS"),
    ("merlion",      "Merlion"),
    ("netlab",       "Netlab"),
    ("resurs_media", "Ресурс Медиа"),
    ("green_place",  "Green Place"),
]

# Канал на slug → label для UI. Заполняется по мере подключения новых
# fetcher'ов в подэтапах 12.1/12.2/12.4.
_CHANNEL_LABELS: dict[str, str] = {
    "treolan": "REST API",
    "ocs":     "IMAP",
    "merlion": "IMAP",
    "netlab":  "HTTP (прямая ссылка)",
}

# Сколько последних запусков показывать в журнале на странице.
_RUNS_LIMIT = 20

# Защита от частых ручных вызовов в UI должна совпадать с throttle
# в runner'е, чтобы кнопка корректно блокировалась до истечения окна.
from app.services.auto_price.runner import MANUAL_THROTTLE_SECONDS


# ---- Время в МСК ------------------------------------------------------

def _to_msk_str(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        dt_msk = dt.astimezone(ZoneInfo("Europe/Moscow"))
    except Exception:
        dt_msk = dt.astimezone(timezone(timedelta(hours=3)))
    return dt_msk.strftime("%d.%m.%Y %H:%M")


def _seconds_since(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(tz=timezone.utc) - dt).total_seconds()


# ---- Сборка данных для шаблона ----------------------------------------

def _suppliers_overview(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT supplier_slug, enabled, status, last_run_at, "
            "       last_success_at, last_error_at, last_error_message "
            "FROM auto_price_loads"
        ),
    ).all()
    by_slug = {r.supplier_slug: r for r in rows}

    registered = set(list_registered_slugs())

    overview: list[dict[str, Any]] = []
    for slug, name in SUPPLIERS_ORDERED:
        r = by_slug.get(slug)
        last_run_at = r.last_run_at if r else None
        secs = _seconds_since(last_run_at)
        too_frequent = (secs is not None and secs < MANUAL_THROTTLE_SECONDS)
        has_fetcher = slug in registered
        item = {
            "slug":                slug,
            "name":                name,
            "channel_label":       _CHANNEL_LABELS.get(slug, "—"),
            "has_fetcher":         has_fetcher,
            "enabled":             bool(r.enabled) if r else False,
            "status":              (r.status if r else "idle") or "idle",
            "last_run_at":         last_run_at,
            "last_run_msk":        _to_msk_str(last_run_at),
            "last_success_at":     r.last_success_at if r else None,
            "last_success_msk":    _to_msk_str(r.last_success_at if r else None),
            "last_error_at":       r.last_error_at if r else None,
            "last_error_msk":      _to_msk_str(r.last_error_at if r else None),
            "last_error_message":  ((r.last_error_message or "") if r else "")[:200],
            "run_disabled":        (not has_fetcher) or too_frequent,
            "run_disabled_reason": (
                "Канал не подключён" if not has_fetcher
                else (
                    f"Последний запуск был меньше {MANUAL_THROTTLE_SECONDS // 60} минут назад"
                    if too_frequent else ""
                )
            ),
        }
        overview.append(item)
    return overview


def _runs_journal(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT id, supplier_slug, started_at, finished_at, "
            "       status, error_message, price_upload_id, triggered_by "
            "FROM auto_price_load_runs "
            "ORDER BY started_at DESC, id DESC "
            "LIMIT :lim"
        ),
        {"lim": _RUNS_LIMIT},
    ).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "id":                int(r.id),
            "supplier_slug":     r.supplier_slug,
            "started_msk":       _to_msk_str(r.started_at),
            "finished_msk":      _to_msk_str(r.finished_at),
            "status":            r.status,
            "error_message":     (r.error_message or "")[:200],
            "price_upload_id":   r.price_upload_id,
            "triggered_by":      r.triggered_by,
        })
    return out


# ---- GET /admin/auto-price-loads --------------------------------------

@router.get("")
def auto_price_index(
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    flash_error = request.session.pop("flash_error", None)
    flash_info = request.session.pop("flash_info", None)

    suppliers = _suppliers_overview(db)
    runs = _runs_journal(db)

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUTO_PRICE_VIEW,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        ip=ip,
        user_agent=ua,
    )

    return templates.TemplateResponse(
        request,
        "admin/auto_price_loads.html",
        {
            "user":                  user,
            "csrf_token":            get_csrf_token(request),
            "suppliers":             suppliers,
            "runs":                  runs,
            "throttle_minutes":      MANUAL_THROTTLE_SECONDS // 60,
            "error":                 flash_error,
            "info":                  flash_info,
        },
    )


# ---- POST /admin/auto-price-loads/<slug>/run --------------------------

@router.post("/{slug}/run")
def auto_price_run(
    slug: str,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    if get_fetcher_class(slug) is None:
        raise HTTPException(
            status_code=400,
            detail=f"Канал автозагрузки для «{slug}» не подключён.",
        )

    ip, ua = extract_request_meta(request)
    try:
        result = run_auto_load(slug, triggered_by="manual")
    except TooFrequentRunError as exc:
        request.session["flash_error"] = str(exc)
        write_audit(
            action=ACTION_AUTO_PRICE_RUN,
            service="portal",
            user_id=user.id,
            user_login=user.login,
            target_type="auto_price_load",
            payload={"slug": slug, "result": "too_frequent", "error": str(exc)},
            ip=ip, user_agent=ua,
        )
        return RedirectResponse(
            url="/admin/auto-price-loads",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )
    except ValueError as exc:
        # Нет fetcher'а — уже отбили выше, но на всякий.
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        # run_auto_load уже залогировал и записал ошибку в БД/Sentry.
        request.session["flash_error"] = (
            f"Загрузка прайса {slug} упала: {type(exc).__name__}: {exc}"
        )
        write_audit(
            action=ACTION_AUTO_PRICE_RUN,
            service="portal",
            user_id=user.id,
            user_login=user.login,
            target_type="auto_price_load",
            payload={"slug": slug, "result": "error", "error": f"{type(exc).__name__}: {exc}"},
            ip=ip, user_agent=ua,
        )
        # 500, чтобы тесты могли проверить сценарий
        return RedirectResponse(
            url="/admin/auto-price-loads",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    request.session["flash_info"] = (
        f"Загрузка для «{slug}» запущена. price_upload_id="
        f"{result.get('price_upload_id')}"
    )
    write_audit(
        action=ACTION_AUTO_PRICE_RUN,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="auto_price_load",
        payload={
            "slug": slug,
            "result": "success",
            "price_upload_id": result.get("price_upload_id"),
            "run_id": result.get("run_id"),
        },
        ip=ip, user_agent=ua,
    )
    return RedirectResponse(
        url="/admin/auto-price-loads",
        status_code=status.HTTP_302_FOUND,
    )


# ---- POST /admin/auto-price-loads/<slug>/toggle -----------------------

@router.post("/{slug}/toggle")
def auto_price_toggle(
    slug: str,
    request: Request,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    row = db.execute(
        text(
            "SELECT id, enabled FROM auto_price_loads WHERE supplier_slug = :slug"
        ),
        {"slug": slug},
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Поставщик {slug} не найден.")

    new_value = not bool(row.enabled)

    # Запрет включать поставщика, у которого нет fetcher'а — иначе
    # APScheduler в 04:00 ничего полезного не сделает, только записал бы
    # ошибку в журнал. На UI кнопка тоже скрыта/неактивна для таких slug,
    # но проверка здесь — защита от прямого POST'а.
    if new_value and get_fetcher_class(slug) is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Нельзя включить автозагрузку для «{slug}»: канал ещё "
                "не подключён (см. подэтапы 12.1/12.2/12.4)."
            ),
        )

    db.execute(
        text(
            "UPDATE auto_price_loads "
            "   SET enabled = :v, updated_at = NOW() "
            " WHERE supplier_slug = :slug"
        ),
        {"v": new_value, "slug": slug},
    )
    db.commit()

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUTO_PRICE_TOGGLE,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="auto_price_load",
        payload={"slug": slug, "enabled": new_value},
        ip=ip, user_agent=ua,
    )

    request.session["flash_info"] = (
        f"Автозагрузка для «{slug}» {'включена' if new_value else 'выключена'}."
    )
    return RedirectResponse(
        url="/admin/auto-price-loads",
        status_code=status.HTTP_302_FOUND,
    )
