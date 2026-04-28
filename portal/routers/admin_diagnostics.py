# /admin/sentry-* портала: диагностические endpoint'ы для Sentry (этап 9В.3).
#
# Эндпоинты:
#   GET /admin/sentry-test     — намеренно бросает RuntimeError, чтобы
#                                проверить что 5xx прилетел в Sentry.
#   GET /admin/sentry-message  — шлёт capture_message("info") без 500-ки.
#
# Оба — require_admin. Полный rationale и инструкция в docs/monitoring.md.

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

import sentry_sdk

from shared.auth import AuthUser, require_admin


logger = logging.getLogger(__name__)


router = APIRouter()


@router.get("/admin/sentry-test")
def sentry_test(user: AuthUser = Depends(require_admin)):
    """Бросает исключение наружу — FastAPI вернёт 500, Sentry поймает.

    Полезно для проверки боевой связки портала с Sentry-проектом сразу
    после деплоя. До 9В.3 не существовало способа убедиться что DSN
    в Railway правильный, кроме ожидания первой реальной 5xx.
    """
    logger.info("sentry-test: admin %s триггерит тестовое исключение", user.login)
    raise RuntimeError("Sentry test exception from /admin/sentry-test")


@router.get("/admin/sentry-message")
def sentry_message(user: AuthUser = Depends(require_admin)):
    """Шлёт явное info-сообщение в Sentry без 500-ки.

    Удобно когда хочется проверить что канал работает, но не хочется
    видеть 500-ку в логах Railway.
    """
    sentry_sdk.capture_message("Sentry test message", level="info")
    logger.info("sentry-message: admin %s отправил тестовое сообщение", user.login)
    return {"status": "sent"}
