# Инициализация Sentry SDK для портала и конфигуратора (этап 9В.3).
#
# Один и тот же helper подключается из app/main.py и portal/main.py.
# Каждый сервис передаёт свой service_name ("configurator" / "portal");
# DSN читается из соответствующей env-переменной (SENTRY_DSN_CONFIGURATOR
# или SENTRY_DSN_PORTAL), при их отсутствии — из общей SENTRY_DSN.
#
# Если DSN не задан, init_sentry возвращает False и Sentry просто
# выключен — в логах остаётся одна строка "Sentry disabled (no SENTRY_DSN)".
# Это сделано специально: в локальной разработке и в тестах Sentry не
# нужен, и мы не хотим ронять процесс из-за отсутствия мониторинга.
#
# Контракт:
#   init_sentry("portal")        → bool (True если DSN был и SDK поднят)
#   init_sentry("configurator")  → bool
#
# Контроль PII (см. ШАГ 1 этапа): send_default_pii=False, IP/email
# в события не уходят. Привязка пользователя — отдельно через
# sentry_sdk.set_user в shared/auth.py (только id + login).
#
# Фильтрация (before_send):
#   - HTTPException 4xx (validation, 401, 403, 404) — выкидываются;
#   - asyncio.CancelledError (отменённые coroutines на shutdown'е) — выкидываются;
#   - всё остальное (5xx, RuntimeError и пр.) — улетает в Sentry.

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any


logger = logging.getLogger(__name__)


# Регэксп публичной части DSN: всё между "://" и "@" — это публичный
# ключ проекта. В логах его маскируем, чтобы не светить лишнего
# (DSN сам по себе не секрет, но и не нужен в открытом логе).
_DSN_PUBLIC_KEY_RE = re.compile(r"://([^@]+)@")


def mask_dsn(dsn: str) -> str:
    """Маскирует публичный ключ в DSN для безопасного логирования.

    https://abcdef@o123.ingest.sentry.io/456 → https://****@o123.ingest.sentry.io/456
    Если строка не похожа на DSN — возвращаем её как есть (не падаем).
    """
    if not dsn:
        return ""
    return _DSN_PUBLIC_KEY_RE.sub("://****@", dsn)


def _resolve_dsn(service_name: str) -> str:
    """Per-service DSN с fallback'ом на общий SENTRY_DSN.

    Так каждый сервис в Railway может получить отдельный Sentry-проект
    через SENTRY_DSN_PORTAL/SENTRY_DSN_CONFIGURATOR; локально хватит одной
    переменной SENTRY_DSN, если она вообще нужна (обычно нет).
    """
    per_service = os.getenv(f"SENTRY_DSN_{service_name.upper()}", "").strip()
    if per_service:
        return per_service
    return os.getenv("SENTRY_DSN", "").strip()


def _make_before_send(service_name: str):
    """Сборка before_send-хука с замыканием на service_name (для логов)."""

    def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
        exc_info = hint.get("exc_info") if hint else None
        if exc_info:
            exc_type, exc_value, _ = exc_info

            # Отменённые coroutines на shutdown'е и при таймаутах — это
            # не баг, а нормальное поведение asyncio. Sentry от такого
            # шумит до неприличия.
            if exc_type is asyncio.CancelledError or isinstance(exc_value, asyncio.CancelledError):
                return None

            # HTTPException с 4xx — это пользовательская ошибка
            # (валидация, 401, 403, 404), не баг. Дропаем; 5xx
            # пропускаем (они — наша проблема).
            try:
                from fastapi import HTTPException
                from starlette.exceptions import HTTPException as StarletteHTTPException
            except Exception:
                HTTPException = None  # type: ignore[assignment]
                StarletteHTTPException = None  # type: ignore[assignment]

            if (
                (HTTPException is not None and isinstance(exc_value, HTTPException))
                or (StarletteHTTPException is not None and isinstance(exc_value, StarletteHTTPException))
            ):
                status_code = getattr(exc_value, "status_code", 500)
                if 400 <= int(status_code) < 500:
                    return None

        return event

    # Метку оставляем для отладки, чтобы в репле было понятно для какого
    # сервиса собирался хук (полезно когда два сервиса крутятся в одном
    # Python-процессе — например, в pytest-сессии).
    before_send._service_name = service_name  # type: ignore[attr-defined]
    return before_send


def _make_traces_sampler():
    """Транзакции на /healthz — почти не сэмплируем.

    Healthcheck бьёт раз в 30 секунд; если включить traces_sample_rate
    глобально, бесплатная квота Developer-плана выгорит за сутки.
    """

    def traces_sampler(sampling_context: dict[str, Any]) -> float:
        request = sampling_context.get("asgi_scope") or {}
        path = ""
        if isinstance(request, dict):
            path = request.get("path") or ""
        if path == "/healthz":
            return 0.01
        return 0.1

    return traces_sampler


def init_sentry(service_name: str) -> bool:
    """Инициализирует Sentry SDK для указанного сервиса.

    Args:
        service_name: "portal" или "configurator". Используется как
            server_name события и для выбора per-service DSN.

    Returns:
        True — DSN найден и SDK инициализирован.
        False — DSN пуст, Sentry отключён. В логах остаётся одна
            INFO-строка "Sentry disabled (no SENTRY_DSN)" — это
            безопасный режим для локальной разработки и тестов.
    """
    if service_name not in ("portal", "configurator"):
        raise ValueError(
            f"init_sentry: service_name должен быть 'portal' или 'configurator', "
            f"получено {service_name!r}"
        )

    dsn = _resolve_dsn(service_name)
    if not dsn:
        logger.info("Sentry disabled (no SENTRY_DSN) for %s", service_name)
        return False

    # environment: production / staging / development. Берём APP_ENV
    # как и остальной код проекта — единый источник правды.
    environment = os.getenv("APP_ENV", "development").strip() or "development"
    release = os.getenv("SENTRY_RELEASE", "").strip() or None

    # Импортируем sentry_sdk лениво. Если кто-то локально без SENTRY_DSN
    # запустит сервис на машине, где даже sentry-sdk не установлен,
    # импорт shared.sentry_init не должен падать на верхнем уровне.
    import sentry_sdk
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.logging import LoggingIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        server_name=service_name,
        # 10% транзакций — баланс между видимостью и квотой Developer-плана
        # (5k events / 10k transactions в месяц). Healthcheck отдельно
        # понижен до 1% через traces_sampler.
        traces_sample_rate=0.1,
        traces_sampler=_make_traces_sampler(),
        # Мы не хотим, чтобы IP/cookie/headers пользователя автоматически
        # летели в Sentry. Привязываем только id+login через set_user.
        send_default_pii=False,
        attach_stacktrace=False,
        before_send=_make_before_send(service_name),
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
            # Уровень event'а — только ERROR и выше; breadcrumb'ы — INFO.
            # WARNING-логи захламляют Sentry без пользы.
            LoggingIntegration(level=logging.INFO, event_level=logging.ERROR),
        ],
    )

    # Тег service для фильтрации в UI Sentry: даже если оба сервиса
    # настроены на один проект (например, в dev), события можно будет
    # фильтровать по tag service:portal / service:configurator.
    sentry_sdk.set_tag("service", service_name)

    logger.info(
        "Sentry initialized for %s (DSN: %s, env=%s)",
        service_name,
        mask_dsn(dsn),
        environment,
    )
    return True
