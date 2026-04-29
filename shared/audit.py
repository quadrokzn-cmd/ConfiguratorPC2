# Аудит-лог действий пользователей (Этап 9В.4).
#
# Один helper write_audit, общий для портала и конфигуратора. Пишет
# строку в таблицу audit_log в отдельной транзакции (свой engine.begin),
# чтобы:
#   - падение записи аудита НЕ ломало пользовательский запрос;
#   - откат основной транзакции (если она ещё не коммитнута) не уносил
#     с собой запись аудита.
#
# Принцип: аудит важен, но НЕ критичен для UX. При любой ошибке логируем
# WARNING (не ERROR — Sentry собирает ERROR, шуметь там не хотим) и
# спокойно продолжаем.
#
# Парный модуль shared/audit_actions.py содержит каталог констант
# action — единый источник правды, чтобы не плодить опечатки в строках.
#
# Тестовый режим: AUDIT_DISABLED=1 → write_audit ничего не пишет.
# Это нужно фикстурам, которые работают без БД (юнит-тесты NLU и пр.),
# чтобы интеграция с роутами не валилась на отсутствии connect()'а.

from __future__ import annotations

import ipaddress
import json
import logging
import os
from typing import Any

from fastapi import Request
from sqlalchemy import text


logger = logging.getLogger(__name__)


# Длина user-agent, после которой обрезаем — длинные UA-строки бывают,
# но смысла хранить их полностью в аудит-логе нет (раздувает БД).
_USER_AGENT_MAX_LEN = 500


def _is_disabled() -> bool:
    raw = (os.environ.get("AUDIT_DISABLED", "") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _normalize_ip(value: str | None) -> str | None:
    """Возвращает IP в нормализованной форме, либо None если входная
    строка не парсится. Защита от мусора в X-Forwarded-For ('testclient'
    у starlette TestClient, '<unknown>' у некоторых прокси и т.п.) —
    Postgres INET строгий и упадёт на любом не-IP значении."""
    if not value:
        return None
    s = value.strip()
    if not s:
        return None
    try:
        return str(ipaddress.ip_address(s))
    except (ValueError, TypeError):
        return None


def _serialize_payload(payload: dict | None) -> str:
    """Готовит JSON-строку для INSERT.

    Исключаем None-значения и компактуем — таблица должна оставаться
    обозримой через psql и веб-UI. Если payload содержит несериализуемые
    объекты (datetime, Decimal и т.п.), default=str превращает их в
    строки, чтобы не уронить запись."""
    if not payload:
        return "{}"
    cleaned = {k: v for k, v in payload.items() if v is not None}
    return json.dumps(cleaned, ensure_ascii=False, default=str)


def write_audit(
    *,
    action: str,
    service: str,
    user_id: int | None = None,
    user_login: str | None = None,
    target_type: str | None = None,
    target_id: str | int | None = None,
    payload: dict | None = None,
    ip: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Пишет одну запись в audit_log. НИКОГДА не бросает исключение наружу.

    Args:
        action:      'auth.login.success', 'project.create' и т.п.
                     Используй константы из shared.audit_actions.
        service:     'portal' / 'configurator' — какой сервис записал.
        user_id:     id актора (None если действие анонимное, например
                     неудачный логин незарегистрированного логина).
        user_login:  логин актора (денормализуем — чтобы при удалении
                     пользователя имя в логе сохранилось).
        target_type: 'user' / 'project' / 'component' / ...
        target_id:   id цели; int конвертируется в str.
        payload:     произвольный JSON-словарь с контекстом действия.
                     Не клади сюда пароли и большие тела запросов.
        ip:          IP клиента. Получай через extract_request_meta().
        user_agent:  user-agent клиента. Обрезается до 500 символов.
    """
    if _is_disabled():
        return

    # Конвертация target_id в текст, чтобы не упираться в типы первичных
    # ключей разных таблиц.
    target_id_str: str | None = None
    if target_id is not None:
        target_id_str = str(target_id)

    ua_clean: str | None = None
    if user_agent:
        ua_clean = user_agent[:_USER_AGENT_MAX_LEN]

    ip_clean = _normalize_ip(ip)

    payload_json = _serialize_payload(payload)

    # Импортируем engine лениво — на момент инициализации модуля shared.db
    # уже импортирован в обоих сервисах, но мы хотим, чтобы AUDIT_DISABLED
    # позволял избежать любого касания engine'а.
    try:
        from shared.db import engine
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO audit_log "
                    "  (user_id, user_login, action, target_type, target_id, "
                    "   payload, ip, user_agent, service) "
                    "VALUES "
                    "  (:user_id, :user_login, :action, :target_type, :target_id, "
                    "   CAST(:payload AS JSONB), CAST(:ip AS INET), :user_agent, :service)"
                ),
                {
                    "user_id":     user_id,
                    "user_login":  user_login,
                    "action":      action,
                    "target_type": target_type,
                    "target_id":   target_id_str,
                    "payload":     payload_json,
                    "ip":          ip_clean,
                    "user_agent":  ua_clean,
                    "service":     service,
                },
            )
    except Exception as exc:
        # Сознательно WARNING, не ERROR: Sentry повесил event_level=ERROR
        # для LoggingIntegration — аудит не должен забивать квоту, если
        # БД недоступна по сетевой причине. На отсутствие таблицы это
        # тоже сработает (например, тест без миграций — мы тихо молчим).
        logger.warning(
            "audit: не удалось записать action=%s service=%s: %s: %s",
            action, service, type(exc).__name__, exc,
        )


def extract_request_meta(request: Request) -> tuple[str | None, str | None]:
    """Возвращает (ip, user_agent) с учётом Railway-прокси.

    IP:
      1. X-Forwarded-For — Railway прокидывает реальный клиентский IP
         в этом заголовке. Берём первый элемент списка (ближайший к
         клиенту; остальные — цепочка прокси).
      2. Если XFF пуст или отсутствует — request.client.host.
      3. Если ничего нет (например, ASGI без client) — None.

    User-Agent: стандартный заголовок, обрезается до 500 символов
    при записи (см. write_audit)."""
    ip: str | None = None
    xff = request.headers.get("x-forwarded-for") or ""
    if xff:
        # X-Forwarded-For: client, proxy1, proxy2 — первый и есть клиент.
        first = xff.split(",", 1)[0].strip()
        if first:
            ip = first
    if ip is None:
        client = getattr(request, "client", None)
        if client is not None:
            host = getattr(client, "host", None)
            if host:
                ip = host

    ua = request.headers.get("user-agent") or None
    return ip, ua
