# /admin/audit портала: журнал действий пользователей (Этап 9В.4).
#
# GET  /admin/audit         — таблица с фильтрами и пагинацией.
# GET  /admin/audit/export  — CSV-экспорт текущей выборки.
#
# Доступ: require_admin. Партнёр по бизнесу — тоже admin, поэтому ему
# журнал виден полностью.
#
# ВАЖНО: сам факт открытия страницы пишется в audit_log как audit.view —
# это «самонаблюдение»: видно, кто и когда смотрел чей лог.

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from portal.templating import templates
from shared.audit import extract_request_meta, write_audit
from shared.audit_actions import ACTION_AUDIT_VIEW
from shared.auth import AuthUser, get_csrf_token, require_admin
from shared.db import get_db


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/admin/audit")


_PAGE_SIZE = 50

# Whitelist значений service для фильтра — защита от инжекта в SQL и от
# случайных опечаток в URL.
_VALID_SERVICES: frozenset[str] = frozenset({"portal", "configurator"})


# --- Время в МСК -------------------------------------------------------

def _to_msk_str(dt: datetime | None) -> str:
    """ДД.ММ.ГГГГ ЧЧ:ММ:СС в МСК. Для UI таблицы и CSV."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        dt_msk = dt.astimezone(ZoneInfo("Europe/Moscow"))
    except Exception:
        dt_msk = dt.astimezone(timezone(timedelta(hours=3)))
    return dt_msk.strftime("%d.%m.%Y %H:%M:%S")


def _parse_date(s: str | None) -> datetime | None:
    """YYYY-MM-DD → datetime в МСК. Невалидные значения → None."""
    if not s:
        return None
    s_clean = s.strip()
    if not s_clean:
        return None
    try:
        return datetime.strptime(s_clean, "%Y-%m-%d")
    except ValueError:
        return None


# --- Сборка SQL-фильтров -----------------------------------------------

def _build_filters(
    *,
    user_id: int | None,
    action: str,
    target_type: str,
    service: str,
    date_from: datetime | None,
    date_to: datetime | None,
) -> tuple[str, dict[str, Any]]:
    """Возвращает (WHERE-фрагмент или '', параметры)."""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if user_id is not None:
        clauses.append("user_id = :user_id")
        params["user_id"] = user_id

    action_clean = (action or "").strip()
    if action_clean:
        # Поддержка префикс-фильтра 'user.*' и точного совпадения.
        if action_clean.endswith(".*"):
            clauses.append("action LIKE :action_prefix")
            params["action_prefix"] = action_clean[:-2] + ".%"
        else:
            clauses.append("action = :action")
            params["action"] = action_clean

    target_type_clean = (target_type or "").strip()
    if target_type_clean:
        clauses.append("target_type = :target_type")
        params["target_type"] = target_type_clean

    service_clean = (service or "").strip().lower()
    if service_clean and service_clean in _VALID_SERVICES:
        clauses.append("service = :service")
        params["service"] = service_clean

    # Диапазон по МСК. В БД created_at TIMESTAMPTZ; чтобы фильтр был
    # понятен пользователю, конвертируем границы из МСК в UTC.
    msk_offset = timedelta(hours=3)
    if date_from is not None:
        # Включительно с 00:00 МСК указанной даты.
        utc_from = (date_from - msk_offset).replace(tzinfo=timezone.utc)
        clauses.append("created_at >= :date_from")
        params["date_from"] = utc_from
    if date_to is not None:
        # Включительно по 23:59:59 МСК указанной даты.
        end_msk = date_to + timedelta(days=1) - timedelta(seconds=1)
        utc_to = (end_msk - msk_offset).replace(tzinfo=timezone.utc)
        clauses.append("created_at <= :date_to")
        params["date_to"] = utc_to

    where = ""
    if clauses:
        where = "WHERE " + " AND ".join(clauses)
    return where, params


def _select_entries(
    db: Session,
    *,
    where: str,
    params: dict[str, Any],
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, created_at, user_id, user_login, action, target_type, "
        "       target_id, payload, ip, user_agent, service "
        f"FROM audit_log {where} "
        "ORDER BY created_at DESC, id DESC "
        "LIMIT :limit OFFSET :offset"
    )
    rows = db.execute(
        text(sql),
        {**params, "limit": limit, "offset": offset},
    ).all()
    out: list[dict[str, Any]] = []
    for r in rows:
        # ip — sqlalchemy отдаёт его как str для INET-колонки.
        payload = r.payload or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {}
        out.append({
            "id":            int(r.id),
            "created_at":    r.created_at,
            "created_msk":   _to_msk_str(r.created_at),
            "user_id":       r.user_id,
            "user_login":    r.user_login or "",
            "action":        r.action,
            "target_type":   r.target_type or "",
            "target_id":     r.target_id or "",
            "payload":       payload,
            "payload_pretty": json.dumps(payload, ensure_ascii=False, indent=2)
                              if payload else "",
            "ip":            r.ip or "",
            "user_agent":    r.user_agent or "",
            "service":       r.service or "",
        })
    return out


def _count_entries(db: Session, *, where: str, params: dict[str, Any]) -> int:
    sql = f"SELECT COUNT(*) AS n FROM audit_log {where}"
    row = db.execute(text(sql), params).first()
    return int(row.n) if row else 0


def _list_users(db: Session) -> list[dict[str, Any]]:
    """Список (id, login) для селектора фильтра — в т.ч. удалённые
    (если в логе встречается их user_id, который сейчас NULL — берём из
    user_login). Чтобы не плодить лишний SQL, отдаём только живых
    пользователей: для удалённых фильтр всё равно ставится по user_login."""
    rows = db.execute(
        text(
            "SELECT id, login FROM users "
            "ORDER BY login ASC"
        )
    ).all()
    return [{"id": int(r.id), "login": r.login} for r in rows]


def _list_actions(db: Session) -> list[str]:
    """Уникальные action в логе — для подсказок селектора фильтра.
    Если лог пуст, возвращается пустой список (UI это переживёт)."""
    rows = db.execute(
        text("SELECT DISTINCT action FROM audit_log ORDER BY action ASC")
    ).all()
    return [r.action for r in rows]


# --- GET /admin/audit ---------------------------------------------------

@router.get("")
def audit_index(
    request: Request,
    user_id: int | None = Query(None),
    action: str = Query(""),
    target_type: str = Query(""),
    service: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    page: int = Query(1, ge=1),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Журнал действий: таблица + фильтры + пагинация."""
    df_dt = _parse_date(date_from)
    dt_dt = _parse_date(date_to)

    where, params = _build_filters(
        user_id=user_id,
        action=action,
        target_type=target_type,
        service=service,
        date_from=df_dt,
        date_to=dt_dt,
    )

    total = _count_entries(db, where=where, params=params)
    page_clean = max(1, int(page or 1))
    offset = (page_clean - 1) * _PAGE_SIZE
    entries = _select_entries(
        db, where=where, params=params,
        limit=_PAGE_SIZE, offset=offset,
    )

    pages_total = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

    # Самонаблюдение: фиксируем сам факт просмотра. Без payload пишем
    # текущие активные фильтры — полезно для разбора «кто что искал».
    active_filters: dict[str, Any] = {}
    if user_id is not None:
        active_filters["user_id"] = user_id
    if (action or "").strip():
        active_filters["action"] = action.strip()
    if (target_type or "").strip():
        active_filters["target_type"] = target_type.strip()
    if (service or "").strip():
        active_filters["service"] = service.strip()
    if df_dt is not None:
        active_filters["date_from"] = date_from
    if dt_dt is not None:
        active_filters["date_to"] = date_to
    if page_clean > 1:
        active_filters["page"] = page_clean

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_AUDIT_VIEW,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        payload={"filters": active_filters} if active_filters else None,
        ip=ip,
        user_agent=ua,
    )

    return templates.TemplateResponse(
        request,
        "admin/audit.html",
        {
            "user":          user,
            "csrf_token":    get_csrf_token(request),
            "entries":       entries,
            "users_list":    _list_users(db),
            "actions_list":  _list_actions(db),
            "filter": {
                "user_id":     user_id,
                "action":      action,
                "target_type": target_type,
                "service":     service,
                "date_from":   date_from,
                "date_to":     date_to,
            },
            "page":         page_clean,
            "pages_total":  pages_total,
            "total":        total,
            "page_size":    _PAGE_SIZE,
        },
    )


# --- GET /admin/audit/export -------------------------------------------

@router.get("/export")
def audit_export_csv(
    user_id: int | None = Query(None),
    action: str = Query(""),
    target_type: str = Query(""),
    service: str = Query(""),
    date_from: str = Query(""),
    date_to: str = Query(""),
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Экспортирует текущую выборку фильтров в CSV. Без пагинации —
    отдаём StreamingResponse, чтобы не держать весь файл в памяти при
    большом аудит-логе. Для compliance-отчётов критично иметь полный
    выгруз, а не первые 50 строк."""
    df_dt = _parse_date(date_from)
    dt_dt = _parse_date(date_to)
    where, params = _build_filters(
        user_id=user_id,
        action=action,
        target_type=target_type,
        service=service,
        date_from=df_dt,
        date_to=dt_dt,
    )

    sql = (
        "SELECT created_at, user_login, user_id, action, target_type, "
        "       target_id, service, ip, user_agent, payload "
        f"FROM audit_log {where} "
        "ORDER BY created_at DESC, id DESC"
    )

    def _stream():
        # csv.writer пишет в текстовый буфер; на каждой N строке мы
        # сбрасываем его как chunk. Это balance — не строчно (CSV-overhead),
        # и не файлом целиком (память).
        buf = io.StringIO()
        writer = csv.writer(buf, dialect="excel", lineterminator="\n")
        writer.writerow([
            "created_at_msk", "user_login", "user_id", "action",
            "target_type", "target_id", "service", "ip", "user_agent",
            "payload",
        ])
        # BOM, чтобы Excel корректно распознавал UTF-8.
        yield "﻿" + buf.getvalue()
        buf.seek(0); buf.truncate(0)

        result = db.execute(text(sql), params)
        rows_in_chunk = 0
        for r in result:
            payload = r.payload or {}
            if isinstance(payload, dict):
                payload_str = json.dumps(payload, ensure_ascii=False)
            else:
                payload_str = str(payload)
            writer.writerow([
                _to_msk_str(r.created_at),
                r.user_login or "",
                r.user_id if r.user_id is not None else "",
                r.action,
                r.target_type or "",
                r.target_id or "",
                r.service or "",
                r.ip or "",
                r.user_agent or "",
                payload_str,
            ])
            rows_in_chunk += 1
            if rows_in_chunk >= 200:
                yield buf.getvalue()
                buf.seek(0); buf.truncate(0)
                rows_in_chunk = 0
        if rows_in_chunk > 0:
            yield buf.getvalue()

    today = datetime.now(tz=timezone.utc).astimezone(
        timezone(timedelta(hours=3))
    ).strftime("%Y-%m-%d")
    filename = f"audit_log_{today}.csv"

    return StreamingResponse(
        _stream(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
