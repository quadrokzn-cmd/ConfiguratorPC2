# Фоновые задачи портала (этап 9В.2).
#
# В отличие от scheduler'а конфигуратора (app/scheduler.py — там 5 cron-точек
# для курса ЦБ), здесь пока ровно одна задача: ежедневный бекап БД на
# Backblaze B2 в 03:00 МСК. Сюда же позже могут попасть очистка временных
# файлов портала и аналогичные периодические работы.
#
# Активация: APScheduler стартует только при APP_ENV=production либо если
# RUN_BACKUP_SCHEDULER=1. Это сделано чтобы:
#   - на pytest-сессии планировщик не дёргал внешние сервисы (B2);
#   - на локальной dev-машине (APP_ENV=development) случайно не залить
#     бекап в продовый бакет;
#   - на Railway (APP_ENV=production) бекапы работали без дополнительных
#     env-флагов.

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from portal.services import backup_service


logger = logging.getLogger(__name__)


_BACKUP_TIMEZONE = "Europe/Moscow"
_BACKUP_HOUR = 3
_BACKUP_MINUTE = 0

# Этап 9В.4: ретенция аудит-лога. По умолчанию 180 дней — можно
# переопределить через AUDIT_RETENTION_DAYS. Если в B2 лежат бекапы,
# старые записи останутся доступны через них (бекапы — наш долгосрочный
# источник истины).
_AUDIT_RETENTION_DEFAULT_DAYS = 180

_scheduler: BackgroundScheduler | None = None


def _job_daily_backup() -> None:
    """Тело cron-задачи: дёргает perform_backup() и логирует результат."""
    started = datetime.now(tz=timezone.utc)
    try:
        result = backup_service.perform_backup()
        logger.info(
            "scheduler/portal: daily_backup ok — %d bytes, tiers=%s, %.2fs",
            result["size_bytes"],
            ",".join(result["tiers"]),
            result["duration_sec"],
        )
    except Exception:
        # perform_backup уже логирует exception со stack trace; здесь
        # дублируем только короткое сообщение, чтобы не замусоривать
        # лог двумя одинаковыми трейсбеками.
        elapsed = (datetime.now(tz=timezone.utc) - started).total_seconds()
        logger.error(
            "scheduler/portal: daily_backup упал за %.2fс (см. трейсбек выше)",
            elapsed,
        )


def _audit_retention_days() -> int:
    """Считывает AUDIT_RETENTION_DAYS из env с дефолтом 180.
    Невалидные/отрицательные значения → дефолт. Минимум 1, чтобы случайно
    не выставить 0 и не зачистить весь лог."""
    raw = (os.environ.get("AUDIT_RETENTION_DAYS", "") or "").strip()
    if not raw:
        return _AUDIT_RETENTION_DEFAULT_DAYS
    try:
        v = int(raw)
    except ValueError:
        return _AUDIT_RETENTION_DEFAULT_DAYS
    if v < 1:
        return _AUDIT_RETENTION_DEFAULT_DAYS
    return v


def _job_audit_retention() -> None:
    """Удаляет записи audit_log старше AUDIT_RETENTION_DAYS дней.

    Защита от зачистки только что записанных строк: интервал считается
    в днях относительно NOW(). Если за неделю пропустили (контейнер был
    оффлайн), misfire_grace_time=3600 даст шанс на догон в течение часа;
    мимо этого окна — забываем и ждём следующего воскресенья.
    """
    days = _audit_retention_days()
    try:
        from shared.db import engine
        with engine.begin() as conn:
            from sqlalchemy import text as _t
            res = conn.execute(
                _t(
                    "DELETE FROM audit_log "
                    "WHERE created_at < NOW() - make_interval(days => :d)"
                ),
                {"d": days},
            )
            removed = res.rowcount or 0
        logger.info(
            "scheduler/portal: audit_retention ok — удалено %d строк (старше %d дней)",
            removed, days,
        )
    except Exception as exc:
        logger.warning(
            "scheduler/portal: audit_retention упал: %s: %s",
            type(exc).__name__, exc,
        )


def _is_enabled() -> bool:
    """Решает, нужно ли стартовать scheduler портала.

    Поднимаем при APP_ENV=production либо если задан явный флаг
    RUN_BACKUP_SCHEDULER=1. На pytest и локалке без флага — молча выключен.
    """
    if (os.environ.get("APP_ENV", "") or "").strip().lower() == "production":
        return True
    raw = (os.environ.get("RUN_BACKUP_SCHEDULER", "") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def init_scheduler() -> BackgroundScheduler | None:
    """Создаёт и запускает BackgroundScheduler с задачей daily_backup.

    Идемпотентно: повторный вызов вернёт уже запущенный экземпляр.
    Если scheduler не активирован настройками — возвращает None и пишет
    info-сообщение.
    """
    global _scheduler

    if not _is_enabled():
        logger.info(
            "scheduler/portal: отключён (APP_ENV != production и RUN_BACKUP_SCHEDULER != 1)."
        )
        return None

    if _scheduler is not None and _scheduler.running:
        return _scheduler

    sched = BackgroundScheduler(
        timezone=_BACKUP_TIMEZONE,
        job_defaults={
            "coalesce": True,
            "misfire_grace_time": 3600,  # если контейнер был оффлайн —
            # есть час, чтобы догнать пропущенный плановый запуск
            "max_instances": 1,          # защита от перекрытия при долгих дампах
        },
    )
    sched.add_job(
        _job_daily_backup,
        trigger=CronTrigger(
            hour=_BACKUP_HOUR,
            minute=_BACKUP_MINUTE,
            timezone=_BACKUP_TIMEZONE,
        ),
        id="daily_backup",
        name="daily DB backup → Backblaze B2 (03:00 МСК)",
        replace_existing=True,
    )

    # 9В.4: ретенция аудит-лога — каждое воскресенье 04:00 МСК (после
    # бекапа в 03:00, чтобы удалённые записи попали в воскресный weekly
    # снимок). Под тем же флагом RUN_BACKUP_SCHEDULER, что и бекапы:
    # на pytest и локалке без флага молча выключен.
    sched.add_job(
        _job_audit_retention,
        trigger=CronTrigger(
            day_of_week="sun",
            hour=4,
            minute=0,
            timezone=_BACKUP_TIMEZONE,
        ),
        id="audit_retention",
        name="audit_log retention (вс 04:00 МСК)",
        replace_existing=True,
    )

    sched.start()
    _scheduler = sched
    logger.info(
        "scheduler/portal: запущен (daily_backup %02d:%02d МСК, "
        "audit_retention вс 04:00 МСК, retention=%d дней).",
        _BACKUP_HOUR, _BACKUP_MINUTE, _audit_retention_days(),
    )
    return sched


def shutdown_scheduler() -> None:
    """Останавливает scheduler без ожидания завершения текущих задач —
    при shutdown процесса нет смысла висеть на pg_dump'е, который и так
    окажется в неконсистентном состоянии."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        if _scheduler.running:
            _scheduler.shutdown(wait=False)
            logger.info("scheduler/portal: остановлен")
    except Exception as exc:
        logger.warning("scheduler/portal: ошибка при остановке: %s", exc)
    _scheduler = None
