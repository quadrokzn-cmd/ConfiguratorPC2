# Фоновые периодические задачи приложения (этап 9А.2.3).
#
# Сейчас здесь только обновление курса ЦБ РФ — 5 раз в день в МСК.
# В будущем сюда же лягут авто-обновление прайсов поставщиков, чистка
# старых логов и т. п.
#
# Используем APScheduler в режиме BackgroundScheduler: пул потоков внутри
# процесса FastAPI. При reload-режиме uvicorn'а scheduler заведётся в
# каждом подпроцессе — это нормально, ЦБ-эндпоинт безопасен к повторам,
# а UPSERT в exchange_rates обработает дубликаты.

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import SessionLocal
# UI-4 (Путь B, 2026-05-11): app/services/export переехал в portal/services/configurator/export.
# app/scheduler.py пока остаётся в app/ (cron USD/RUB), его перенос — отдельный мини-этап перед UI-5.
from portal.services.configurator.export import exchange_rate

logger = logging.getLogger(__name__)


# Часы МСК, когда дёргаем ЦБ. ЦБ обновляет курс утром (~8:30) и затем
# периодически до вечера. 5 точек — компромисс между «всегда свежий
# курс» и «не дёргаем CBR на каждое чихание».
_CBR_CRON_TIMES = [
    ("08", "30"),
    ("13", "00"),
    ("16", "00"),
    ("17", "00"),
    ("18", "15"),
]
_CBR_TIMEZONE = "Europe/Moscow"
_LOG_DIR = Path("logs")
_LOG_FILE = _LOG_DIR / "scheduler.log"


_scheduler: BackgroundScheduler | None = None


def _setup_file_logger() -> None:
    """Дополнительно льём scheduler-сообщения в logs/scheduler.log,
    чтобы было куда смотреть при разборе «а почему сегодня курс старый»."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    # Прицепляем к нашему логгеру и к корневому apscheduler.
    for name in (__name__, "apscheduler"):
        log = logging.getLogger(name)
        if not any(
            isinstance(h, logging.FileHandler)
            and getattr(h, "baseFilename", "") == str(_LOG_FILE.resolve())
            for h in log.handlers
        ):
            log.addHandler(handler)


def _job_fetch_cbr() -> None:
    """Тело cron-задачи: ходит на ЦБ и пишет курс в БД."""
    started = datetime.now()
    db = SessionLocal()
    try:
        info = exchange_rate.fetch_and_store_cbr_rate(db)
        logger.info(
            "scheduler: курс ЦБ обновлён — %s ₽ (date=%s, source=%s)",
            info.rate, info.rate_date, info.source,
        )
    except Exception as exc:
        # Не падаем — следующий cron повторит попытку. Главное, что
        # старый курс в БД остаётся доступным.
        logger.warning(
            "scheduler: не удалось обновить курс ЦБ (%s): %s",
            type(exc).__name__, exc,
        )
    finally:
        db.close()
        elapsed = (datetime.now() - started).total_seconds()
        logger.info("scheduler: cbr-job выполнен за %.2fс", elapsed)


def init_scheduler() -> BackgroundScheduler:
    """Создаёт и запускает BackgroundScheduler с cron-задачами.

    Идемпотентно: повторный вызов вернёт уже запущенный экземпляр.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    _setup_file_logger()

    sched = BackgroundScheduler(
        timezone=_CBR_TIMEZONE,
        # При старте сервера НЕ догоняем пропущенные запуски (если
        # инстанс был выключен ночью — не надо запускать всё подряд),
        # достаточно ближайшего планового.
        job_defaults={
            "coalesce": True,
            "misfire_grace_time": 3600,
            "max_instances": 1,
        },
    )
    for hour, minute in _CBR_CRON_TIMES:
        sched.add_job(
            _job_fetch_cbr,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=_CBR_TIMEZONE),
            id=f"cbr_fetch_{hour}{minute}",
            name=f"CBR rate fetch {hour}:{minute} МСК",
            replace_existing=True,
        )

    sched.start()
    _scheduler = sched
    logger.info(
        "scheduler: запущен, %d задач(и) обновления курса ЦБ",
        len(_CBR_CRON_TIMES),
    )
    return sched


def shutdown_scheduler() -> None:
    """Останавливает scheduler без ожидания завершения текущих задач —
    при shutdown'е сервера нет смысла висеть лишний таймаут на сетевом
    запросе ЦБ."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        if _scheduler.running:
            _scheduler.shutdown(wait=False)
            logger.info("scheduler: остановлен")
    except Exception as exc:
        logger.warning("scheduler: ошибка при остановке: %s", exc)
    _scheduler = None


def ensure_initial_rate() -> None:
    """При старте сервера: если в exchange_rates пусто — синхронно дёргаем
    ЦБ один раз, чтобы UI сразу показывал данные.

    Если ЦБ недоступен и в БД пусто — логируем warning и идём дальше:
    приложение должно стартовать даже когда сеть недоступна.
    """
    db = SessionLocal()
    try:
        from sqlalchemy import text as _t
        row = db.execute(_t("SELECT 1 FROM exchange_rates LIMIT 1")).first()
        if row is not None:
            return
        info = exchange_rate.fetch_and_store_cbr_rate(db)
        logger.info(
            "scheduler: при старте подтянули первый курс — %s ₽ (%s)",
            info.rate, info.rate_date,
        )
    except Exception as exc:
        logger.warning(
            "scheduler: первичная инициализация курса ЦБ не удалась: %s",
            exc,
        )
    finally:
        db.close()
