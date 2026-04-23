# Бизнес-логика веб-роутов: название проекта, сериализация FinalResponse,
# сохранение запроса в БД. Роуты тонкие — всё тяжёлое живёт здесь.

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.configurator.schema import result_to_dict
from app.services.nlu.schema import FinalResponse


# --- Название проекта ----------------------------------------------------

def format_project_name(raw_name: str | None, *, now: datetime | None = None) -> str:
    """Строит имя проекта для записи в БД.

    Правила (этап 5):
      - пусто → «Запрос от 23.04.2026 14:30»
      - не пусто → «<Имя> (23.04.2026 14:30)»
    """
    ts = (now or datetime.now()).strftime("%d.%m.%Y %H:%M")
    clean = (raw_name or "").strip()
    if not clean:
        return f"Запрос от {ts}"
    # Ограничение на длину, чтобы не упереться в VARCHAR(300).
    if len(clean) > 200:
        clean = clean[:200].rstrip()
    return f"{clean} ({ts})"


# --- Сериализация FinalResponse в JSONB ---------------------------------

def parsed_to_dict(resp: FinalResponse) -> dict | None:
    """ParsedRequest + список ResolvedMention в плоский dict.
    Если парсер не отработал (resp.parsed is None) — возвращаем None."""
    if resp.parsed is None:
        return None

    parsed = resp.parsed
    out: dict[str, Any] = {
        "is_empty":              parsed.is_empty,
        "purpose":               parsed.purpose,
        "budget_usd":            parsed.budget_usd,
        "cpu_manufacturer":      parsed.cpu_manufacturer,
        "overrides":             dict(parsed.overrides),
        "model_mentions":        [
            {"category": m.category, "query": m.query}
            for m in parsed.model_mentions
        ],
        "clarifying_questions":  list(parsed.clarifying_questions),
        "raw_summary":           parsed.raw_summary,
    }
    if resp.resolved:
        out["resolved"] = [
            {
                "query":         r.mention.query,
                "category":      r.mention.category,
                "found_id":      r.found_id,
                "found_model":   r.found_model,
                "found_sku":     r.found_sku,
                "is_substitute": r.is_substitute,
                "note":          r.note,
            }
            for r in resp.resolved
        ]
    return out


def build_request_to_dict(resp: FinalResponse) -> dict | None:
    """BuildRequest → dict (через asdict-и по вложенным dataclass-ам)."""
    br = resp.build_request
    if br is None:
        return None
    return {
        "budget_usd":  br.budget_usd,
        "cpu":         asdict(br.cpu),
        "ram":         asdict(br.ram),
        "gpu":         asdict(br.gpu),
        "storage":     asdict(br.storage),
        "motherboard": asdict(br.motherboard) if br.motherboard else None,
        "case":        asdict(br.case)        if br.case        else None,
        "psu":         asdict(br.psu)         if br.psu         else None,
        "cooler":      asdict(br.cooler)      if br.cooler      else None,
        "allow_transit": br.allow_transit,
    }


def build_result_to_dict(resp: FinalResponse) -> dict | None:
    """BuildResult → dict. Тонкая обёртка над result_to_dict из configurator."""
    if resp.build_result is None:
        return None
    return result_to_dict(resp.build_result)


# --- Сохранение запроса --------------------------------------------------

def _created_sync_query_cost(session: Session, run_started_at: datetime) -> tuple[float, float, float | None]:
    """Считает фактический расход по этому конкретному запросу.

    Берём все записи api_usage_log, созданные не раньше run_started_at —
    это вызовы, относящиеся к текущему process_query (парсер + комментатор).
    Возвращает (cost_usd, cost_rub, usd_rub_rate).
    """
    row = session.execute(
        text(
            "SELECT COALESCE(SUM(cost_usd), 0) AS u, "
            "       COALESCE(SUM(cost_rub), 0) AS r, "
            "       MAX(usd_rub_rate)          AS rate "
            "FROM api_usage_log "
            "WHERE started_at >= :since"
        ),
        {"since": run_started_at},
    ).first()
    if row is None:
        return 0.0, 0.0, None
    cost_usd = float(row.u) if isinstance(row.u, Decimal) else float(row.u or 0.0)
    cost_rub = float(row.r) if isinstance(row.r, Decimal) else float(row.r or 0.0)
    rate = float(row.rate) if isinstance(row.rate, Decimal) else (
        float(row.rate) if row.rate is not None else None
    )
    return cost_usd, cost_rub, rate


def create_project(
    session: Session,
    *,
    user_id: int,
    name: str,
) -> int:
    """Создаёт проект, возвращает id."""
    row = session.execute(
        text(
            "INSERT INTO projects (user_id, name) "
            "VALUES (:uid, :name) "
            "RETURNING id"
        ),
        {"uid": user_id, "name": name},
    ).first()
    session.commit()
    return int(row.id)


def save_query(
    session: Session,
    *,
    project_id: int,
    user_id: int,
    raw_text: str,
    resp: FinalResponse | None,
    error_msg: str | None = None,
    run_started_at: datetime | None = None,
) -> int:
    """Сохраняет запрос в БД. Возвращает id новой записи в queries.

    Если resp=None (была ошибка до/во время process_query) —
    status='error', тексты — через error_msg.
    """
    import json

    if resp is None:
        status = "error"
        parsed_json = None
        breq_json = None
        bres_json = None
        formatted = None
        interpretation = None
        warnings = None
        cost_usd = 0.0
        cost_rub = 0.0
        usd_rub_rate = None
    else:
        status = resp.kind or "ok"
        parsed_json = parsed_to_dict(resp)
        breq_json = build_request_to_dict(resp)
        bres_json = build_result_to_dict(resp)
        formatted = resp.formatted_text
        interpretation = resp.interpretation
        warnings = list(resp.warnings)
        # Cost из api_usage_log (он точнее, чем resp.cost_usd: там только USD,
        # а нам нужны и рубли).
        if run_started_at is not None:
            cost_usd, cost_rub, usd_rub_rate = _created_sync_query_cost(
                session, run_started_at
            )
        else:
            cost_usd = float(resp.cost_usd or 0.0)
            cost_rub = 0.0
            usd_rub_rate = None

    row = session.execute(
        text(
            "INSERT INTO queries "
            "    (project_id, user_id, raw_text, "
            "     parsed_json, build_request_json, build_result_json, "
            "     formatted_text, interpretation, warnings_json, "
            "     status, error_msg, cost_usd, cost_rub, usd_rub_rate) "
            "VALUES "
            "    (:pid, :uid, :raw, "
            "     CAST(:parsed AS JSONB), CAST(:breq AS JSONB), CAST(:bres AS JSONB), "
            "     :fmt, :interp, CAST(:warns AS JSONB), "
            "     :status, :err, :cu, :cr, :rate) "
            "RETURNING id"
        ),
        {
            "pid":    project_id,
            "uid":    user_id,
            "raw":    raw_text,
            "parsed": json.dumps(parsed_json, ensure_ascii=False) if parsed_json is not None else None,
            "breq":   json.dumps(breq_json,   ensure_ascii=False) if breq_json   is not None else None,
            "bres":   json.dumps(bres_json,   ensure_ascii=False) if bres_json   is not None else None,
            "fmt":    formatted,
            "interp": interpretation,
            "warns":  json.dumps(warnings, ensure_ascii=False) if warnings is not None else None,
            "status": status,
            "err":    error_msg,
            "cu":     round(cost_usd, 6),
            "cr":     round(cost_rub, 2),
            "rate":   round(usd_rub_rate, 4) if usd_rub_rate is not None else None,
        },
    ).first()
    session.commit()
    return int(row.id)


# --- Чтение запросов -----------------------------------------------------

def get_query_for_user(
    session: Session,
    *,
    query_id: int,
    requester_user_id: int,
    requester_is_admin: bool,
) -> dict | None:
    """Возвращает dict с полями запроса, либо None если запись не найдена
    либо не принадлежит пользователю и он не админ.

    ВНИМАНИЕ: различать «не найдено» и «403» должен вызывающий код —
    мы возвращаем None в обоих случаях, но через параметр raise_forbidden
    можно будет поднять HTTPException (сделано в роуте через явные проверки).
    """
    row = session.execute(
        text(
            "SELECT q.id, q.project_id, q.user_id, q.raw_text, "
            "       q.parsed_json, q.build_request_json, q.build_result_json, "
            "       q.formatted_text, q.interpretation, q.warnings_json, "
            "       q.status, q.error_msg, q.cost_usd, q.cost_rub, "
            "       q.usd_rub_rate, q.created_at, "
            "       p.name AS project_name, u.login AS author_login, "
            "       u.name AS author_name "
            "FROM queries q "
            "JOIN projects p ON p.id = q.project_id "
            "JOIN users u ON u.id = q.user_id "
            "WHERE q.id = :qid"
        ),
        {"qid": query_id},
    ).first()
    if row is None:
        return None
    if not requester_is_admin and int(row.user_id) != int(requester_user_id):
        return {"_forbidden": True}
    return {
        "id":              int(row.id),
        "project_id":      int(row.project_id),
        "user_id":         int(row.user_id),
        "raw_text":        row.raw_text,
        "parsed":          row.parsed_json,
        "build_request":   row.build_request_json,
        "build_result":    row.build_result_json,
        "formatted_text":  row.formatted_text,
        "interpretation":  row.interpretation,
        "warnings":        row.warnings_json or [],
        "status":          row.status,
        "error_msg":       row.error_msg,
        "cost_usd":        float(row.cost_usd) if row.cost_usd is not None else 0.0,
        "cost_rub":        float(row.cost_rub) if row.cost_rub is not None else 0.0,
        "usd_rub_rate":    float(row.usd_rub_rate) if row.usd_rub_rate is not None else None,
        "created_at":      row.created_at,
        "project_name":    row.project_name,
        "author_login":    row.author_login,
        "author_name":     row.author_name,
    }


def list_user_queries(session: Session, user_id: int, limit: int = 200) -> list[dict]:
    """Последние запросы одного пользователя, новые сверху."""
    rows = session.execute(
        text(
            "SELECT q.id, q.raw_text, q.status, q.cost_rub, q.created_at, "
            "       p.name AS project_name "
            "FROM queries q "
            "JOIN projects p ON p.id = q.project_id "
            "WHERE q.user_id = :uid "
            "ORDER BY q.created_at DESC "
            "LIMIT :lim"
        ),
        {"uid": user_id, "lim": limit},
    ).all()
    return [_row_to_list_item(r) for r in rows]


def list_all_queries(session: Session, limit: int = 200) -> list[dict]:
    """Последние запросы всех пользователей (для админки)."""
    rows = session.execute(
        text(
            "SELECT q.id, q.raw_text, q.status, q.cost_rub, q.created_at, "
            "       p.name AS project_name, "
            "       u.login AS author_login, u.name AS author_name "
            "FROM queries q "
            "JOIN projects p ON p.id = q.project_id "
            "JOIN users u ON u.id = q.user_id "
            "ORDER BY q.created_at DESC "
            "LIMIT :lim"
        ),
        {"lim": limit},
    ).all()
    return [_row_to_list_item(r, with_author=True) for r in rows]


def _row_to_list_item(r, *, with_author: bool = False) -> dict:
    short = (r.raw_text or "").strip().replace("\n", " ")
    if len(short) > 120:
        short = short[:117] + "…"
    item = {
        "id":           int(r.id),
        "raw_text":     r.raw_text,
        "short_text":   short,
        "status":       r.status,
        "cost_rub":     float(r.cost_rub) if r.cost_rub is not None else 0.0,
        "created_at":   r.created_at,
        "project_name": r.project_name,
    }
    if with_author:
        item["author_login"] = r.author_login
        item["author_name"]  = r.author_name
    return item


# --- Пользователи (для /admin/users) ------------------------------------

def list_users(session: Session) -> list[dict]:
    rows = session.execute(
        text(
            "SELECT id, login, role, name, is_active, created_at "
            "FROM users ORDER BY created_at ASC"
        )
    ).all()
    return [
        {
            "id":         int(r.id),
            "login":      r.login,
            "role":       r.role,
            "name":       r.name,
            "is_active":  bool(r.is_active),
            "created_at": r.created_at,
        }
        for r in rows
    ]


def create_manager(
    session: Session,
    *,
    login: str,
    password_hash: str,
    name: str,
) -> int:
    """Создаёт менеджера. Возвращает id. При конфликте логина — поднимает
    ValueError('login_taken')."""
    exists = session.execute(
        text("SELECT 1 FROM users WHERE login = :login"),
        {"login": login},
    ).first()
    if exists:
        raise ValueError("login_taken")
    row = session.execute(
        text(
            "INSERT INTO users (login, password_hash, role, name) "
            "VALUES (:login, :ph, 'manager', :name) "
            "RETURNING id"
        ),
        {"login": login, "ph": password_hash, "name": name},
    ).first()
    session.commit()
    return int(row.id)


def toggle_user_active(session: Session, user_id: int) -> bool:
    """Переключает is_active у пользователя. Возвращает новое значение."""
    row = session.execute(
        text(
            "UPDATE users SET is_active = NOT is_active "
            "WHERE id = :id "
            "RETURNING is_active"
        ),
        {"id": user_id},
    ).first()
    session.commit()
    return bool(row.is_active) if row else False


# --- Статистика бюджета для /admin/budget -------------------------------

def get_budget_by_day(session: Session, days: int = 30) -> list[dict]:
    """Расход по дням за последние N дней (новые сверху)."""
    rows = session.execute(
        text(
            "SELECT started_at::date AS d, "
            "       SUM(cost_usd) AS u, "
            "       SUM(cost_rub) AS r, "
            "       COUNT(*)      AS n "
            "FROM api_usage_log "
            "WHERE started_at::date >= CURRENT_DATE - (:days)::int "
            "GROUP BY started_at::date "
            "ORDER BY started_at::date DESC"
        ),
        {"days": int(days)},
    ).all()
    return [
        {
            "date":       r.d,
            "cost_usd":   float(r.u) if r.u is not None else 0.0,
            "cost_rub":   float(r.r) if r.r is not None else 0.0,
            "calls":      int(r.n),
        }
        for r in rows
    ]


def get_month_total_rub(session: Session) -> float:
    """Сумма расходов за текущий календарный месяц."""
    row = session.execute(
        text(
            "SELECT COALESCE(SUM(cost_rub), 0) AS r "
            "FROM api_usage_log "
            "WHERE date_trunc('month', started_at) = date_trunc('month', CURRENT_DATE)"
        )
    ).first()
    if row is None:
        return 0.0
    return float(row.r) if isinstance(row.r, Decimal) else float(row.r or 0.0)
