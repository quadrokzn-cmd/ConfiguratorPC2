# /admin/run-ingest{,-blocking} — ручной запуск ingest-задачи модуля «Аукционы»
# (этап 8/9 слияния, 2026-05-08).
#
# Эндпоинты:
#   POST /admin/run-ingest          — запускает run_ingest_once в фоне.
#                                      Возвращает {"status": "started"} сразу.
#                                      Защита от двойного запуска — общий
#                                      threading.Lock с APScheduler-job'ом.
#   POST /admin/run-ingest-blocking — синхронный запуск с возвратом IngestStats.
#                                      Используется в smoke-тестах и для
#                                      разовых ручных проверок из админки.
#
# Доступ: require_permission('auctions_edit_settings'). Тонкое право, так
# как ingest — настроечная операция (читает settings.* и пишет в tenders).
# Без CSRF-защиты на уровне POST: эндпоинты вызываются скриптами/curl, а
# не из браузерных форм портала. UI-кнопка появится на Этапе 9 и тогда же
# навесится csrf_token.

from __future__ import annotations

import logging
import threading

from fastapi import APIRouter, Depends, HTTPException, status

from app.services.auctions.ingest.single_flight import ingest_lock
from shared.auth import AuthUser
from shared.permissions import require_permission


logger = logging.getLogger(__name__)


router = APIRouter()


def _run_ingest_async(user_login: str) -> None:
    """Тело фонового запуска для /admin/run-ingest. Single-flight через
    общий ingest_lock: если занято — тихо выходим (не запускаем второй
    параллельный прогон, его всё равно отбили бы внутри). Любая ошибка
    логируется, не пропагируется (фоновый поток без обработчика)."""
    if not ingest_lock.acquire(blocking=False):
        logger.warning(
            "admin/run-ingest: %s — предыдущий прогон ещё активен, пропуск.",
            user_login,
        )
        return
    try:
        from app.services.auctions.ingest.orchestrator import run_ingest_once
        from shared.db import engine
        try:
            stats = run_ingest_once(engine)
            logger.info(
                "admin/run-ingest: %s ok — parsed=%d inserted=%d updated=%d failed=%d",
                user_login, stats.cards_parsed, stats.inserted, stats.updated, stats.cards_failed,
            )
        except Exception as exc:
            logger.warning(
                "admin/run-ingest: %s упал (%s: %s)",
                user_login, type(exc).__name__, exc,
            )
    finally:
        ingest_lock.release()


@router.post("/admin/run-ingest")
def run_ingest_async(
    user: AuthUser = Depends(require_permission("auctions_edit_settings")),
):
    """Не блокирующий запуск: стартует ingest в daemon-потоке и сразу
    возвращает {"status": "started"|"busy"}. Если ingest уже занят —
    возвращает 409 Conflict (даблклик не должен молча дропнуться)."""
    if ingest_lock.locked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ingest аукционов уже выполняется. Дождитесь завершения и попробуйте снова.",
        )
    thread = threading.Thread(
        target=_run_ingest_async,
        args=(user.login,),
        name=f"admin-run-ingest-{user.login}",
        daemon=True,
    )
    thread.start()
    logger.info("admin/run-ingest: %s — фоновый запуск стартовал", user.login)
    return {"status": "started"}


@router.post("/admin/run-ingest-blocking")
def run_ingest_blocking(
    user: AuthUser = Depends(require_permission("auctions_edit_settings")),
):
    """Синхронный запуск: ждёт окончания ingest, возвращает IngestStats.as_dict().
    Используется smoke-тестами и для разовых проверок. Single-flight: если
    занято, отвечает 409 (вызывающий получит чёткую ошибку, а не зависнет)."""
    if not ingest_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ingest аукционов уже выполняется. Дождитесь завершения и попробуйте снова.",
        )
    try:
        from app.services.auctions.ingest.orchestrator import run_ingest_once
        from shared.db import engine
        try:
            stats = run_ingest_once(engine)
        except Exception as exc:
            logger.warning(
                "admin/run-ingest-blocking: %s упал (%s: %s)",
                user.login, type(exc).__name__, exc,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{type(exc).__name__}: {exc}",
            )
    finally:
        ingest_lock.release()

    logger.info(
        "admin/run-ingest-blocking: %s ok — parsed=%d inserted=%d updated=%d failed=%d",
        user.login, stats.cards_parsed, stats.inserted, stats.updated, stats.cards_failed,
    )
    return stats.as_dict()
