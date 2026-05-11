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
from apscheduler.triggers.interval import IntervalTrigger

from portal.services import backup_service


logger = logging.getLogger(__name__)


_BACKUP_TIMEZONE = "Europe/Moscow"
_BACKUP_HOUR = 3
_BACKUP_MINUTE = 0

# Этап 12.2: единое утреннее расписание авто-загрузки прайсов.
# До 12.2 расписание было разнесено по дню: 04:00 МСК — REST (Treolan),
# 14:30 МСК — IMAP (OCS, Merlion). Новый план: всё прогоняем утром, до
# начала рабочего дня, чтобы менеджер сразу видел свежие цены. Каждому
# поставщику — свой 10-минутный слот, чтобы не ловить параллельные
# orchestrator-вставки в одну и ту же таблицу supplier_prices и не
# забивать сеть/IMAP одновременными подключениями.
#
# Слоты:
#   07:00  treolan       (REST API)
#   07:10  ocs           (IMAP)
#   07:20  merlion       (IMAP)
#   07:30  netlab        (HTTP)
#   07:40  resurs_media  (12.4 — пока без fetcher'а, no-op при OFF)
#   07:50  green_place   (12.4 — пока без fetcher'а, no-op при OFF)
#
# Каждый job сам читает auto_price_loads.enabled для своего slug и
# вызывает run_auto_load(slug, 'scheduled') только при enabled=TRUE.
# Это позволяет UI-тумблер мгновенно отключать поставщика без
# перерегистрации cron-задач.
_AUTO_PRICE_SCHEDULE: list[tuple[str, int, int]] = [
    ("treolan",      7,  0),
    ("ocs",          7, 10),
    ("merlion",      7, 20),
    ("netlab",       7, 30),
    ("resurs_media", 7, 40),
    ("green_place",  7, 50),
]

# Этап 8/9 слияния (2026-05-08): ингест аукционных карточек с zakupki.gov.ru.
# Запускается каждые 2 часа (24/7) — окно подачи заявок на zakupki короткое
# (часто 7-14 дней), пропустить лот = упустить шанс. Реальная нагрузка
# минимальная: 2 KTRU-зонтика, ~150 карточек активны одновременно. Throttle
# и UA-rotation в ZakupkiClient защищают от бана.
#
# Тумблер: settings.auctions_ingest_enabled. Если != 'true' (нечувствительно
# к регистру) — плановый прогон тихо пропускается. Добавлен миграцией 034.
#
# Single-flight: общий threading.Lock в app.services.auctions.ingest.single_flight,
# который шарится с endpoint'ами /admin/run-ingest{,-blocking}. Если cron-тик
# и ручной запуск пытаются стартовать одновременно — второй проигрывает и
# пишет лог. max_instances=1 в APScheduler — вторая линия защиты (внутри
# scheduler'а тики не накапливаются).
_AUCTIONS_INGEST_INTERVAL_HOURS = 2

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


def _is_supplier_enabled(slug: str) -> bool:
    """True, если auto_price_loads.enabled = TRUE для этого slug.
    На ошибке БД — False с предупреждением в лог (если БД недоступна,
    лучше пропустить, чем бросить исключение и завалить APScheduler).
    """
    try:
        from sqlalchemy import text as _t
        from shared.db import engine
        with engine.begin() as conn:
            row = conn.execute(
                _t(
                    "SELECT enabled FROM auto_price_loads "
                    "WHERE supplier_slug = :slug"
                ),
                {"slug": slug},
            ).first()
        if row is None:
            return False
        return bool(row.enabled)
    except Exception as exc:
        logger.warning(
            "scheduler/portal: не удалось прочитать enabled для %s (%s: %s)",
            slug, type(exc).__name__, exc,
        )
        return False


def _make_auto_price_job(slug: str):
    """Фабрика тела cron-задачи для конкретного slug.

    Тело:
      1. Проверяет auto_price_loads.enabled — если FALSE, тихо выходит
         (тумблер выключен — никаких записей в журнал).
      2. Иначе вызывает run_auto_load(slug, triggered_by='scheduled').
         Если fetcher не зарегистрирован (resurs_media, green_place до
         12.4), run_auto_load бросит ValueError — runner сам запишет
         его как error в auto_price_load_runs. Это допустимое
         поведение: тумблер OFF — задача не срабатывает; включил, но
         канала ещё нет — увидит ошибку в журнале.
      3. Любая другая ошибка ловится и пишется в WARN, чтобы не
         завалить scheduler-loop.
    """
    def _job() -> None:
        if not _is_supplier_enabled(slug):
            logger.info(
                "scheduler/portal: auto_price_loads.%s — тумблер OFF, пропуск.",
                slug,
            )
            return
        try:
            from portal.services.configurator.auto_price.runner import run_auto_load
        except Exception as exc:
            logger.warning(
                "scheduler/portal: %s — не удалось импортировать runner (%s: %s)",
                slug, type(exc).__name__, exc,
            )
            return
        try:
            run_auto_load(slug, triggered_by="scheduled")
            logger.info("scheduler/portal: auto_price_loads.%s — ok", slug)
        except Exception as exc:
            # run_auto_load уже залогировал и (при настоящих ошибках)
            # отправил в Sentry. Здесь — просто двигаемся дальше.
            logger.warning(
                "scheduler/portal: auto_price_loads.%s — %s: %s",
                slug, type(exc).__name__, exc,
            )

    _job.__name__ = f"_job_auto_price_loads_{slug}"
    return _job


def _is_auctions_ingest_enabled() -> bool:
    """True, если settings.auctions_ingest_enabled установлен в 'true'
    (нечувствительно к регистру). Любое другое значение, отсутствие
    ключа или ошибка БД → False (безопаснее пропустить, чем уронить
    APScheduler-loop)."""
    try:
        from sqlalchemy import text as _t
        from shared.db import engine
        with engine.begin() as conn:
            row = conn.execute(
                _t("SELECT value FROM settings WHERE key = 'auctions_ingest_enabled'"),
            ).first()
        if row is None:
            return False
        return (row.value or "").strip().lower() == "true"
    except Exception as exc:
        logger.warning(
            "scheduler/portal: не удалось прочитать auctions_ingest_enabled (%s: %s)",
            type(exc).__name__, exc,
        )
        return False


def _job_auctions_ingest() -> None:
    """Тело cron-задачи `auctions_ingest`.

    1. Тумблер `settings.auctions_ingest_enabled`. != 'true' → тихий
       выход (как в auto_price-job'ах: тумблер OFF — никаких записей).
    2. Single-flight через `app.services.auctions.ingest.single_flight.
       ingest_lock`. Если занято (например, /admin/run-ingest-blocking
       сейчас крутится) — пропускаем тик с warning-логом.
    3. Импорт run_ingest_once отложенный — чтобы portal стартовал даже
       при сломанных импортах модуля аукционов (на pytest без
       BeautifulSoup/lxml — теоретически).
    4. Любая ошибка ловится и пишется в WARN, не валит scheduler-loop.
    """
    if not _is_auctions_ingest_enabled():
        logger.info(
            "scheduler/portal: auctions_ingest — тумблер settings.auctions_ingest_enabled OFF, пропуск.",
        )
        return

    try:
        from app.services.auctions.ingest.single_flight import ingest_lock
    except Exception as exc:
        logger.warning(
            "scheduler/portal: auctions_ingest — не удалось импортировать single_flight (%s: %s)",
            type(exc).__name__, exc,
        )
        return

    if not ingest_lock.acquire(blocking=False):
        logger.warning(
            "scheduler/portal: auctions_ingest — предыдущий прогон ещё активен, пропуск.",
        )
        return

    try:
        from app.services.auctions.ingest.orchestrator import run_ingest_once
        from shared.db import engine
        try:
            stats = run_ingest_once(engine)
            logger.info(
                "scheduler/portal: auctions_ingest ok — parsed=%d inserted=%d updated=%d failed=%d",
                stats.cards_parsed, stats.inserted, stats.updated, stats.cards_failed,
            )
        except Exception as exc:
            logger.warning(
                "scheduler/portal: auctions_ingest упал (%s: %s)",
                type(exc).__name__, exc,
            )
    finally:
        ingest_lock.release()


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

    # 12.2: единое утреннее расписание авто-загрузки. По одному cron-job
    # на каждого поставщика, с интервалом 10 минут — чтобы в утренний
    # час прайсы пришли в нужном порядке (REST → IMAP → HTTP), а если
    # один поставщик встал — остальные не пострадали. Под тем же флагом
    # RUN_BACKUP_SCHEDULER — на pytest и dev-машине задача не
    # регистрируется, чтобы тесты не лезли к API/IMAP/HTTP.
    for slug, hour, minute in _AUTO_PRICE_SCHEDULE:
        sched.add_job(
            _make_auto_price_job(slug),
            trigger=CronTrigger(
                hour=hour, minute=minute, timezone=_BACKUP_TIMEZONE,
            ),
            id=f"auto_price_loads_{slug}",
            name=f"auto price loads {slug} ({hour:02d}:{minute:02d} МСК)",
            replace_existing=True,
        )

    # Этап 8/9 слияния: ingest аукционных карточек с zakupki, каждые 2 часа.
    # Тумблер — settings.auctions_ingest_enabled (через миграцию 034).
    sched.add_job(
        _job_auctions_ingest,
        trigger=IntervalTrigger(
            hours=_AUCTIONS_INGEST_INTERVAL_HOURS,
            timezone=_BACKUP_TIMEZONE,
        ),
        id="auctions_ingest",
        name=f"ingest аукционов с zakupki (каждые {_AUCTIONS_INGEST_INTERVAL_HOURS}ч)",
        replace_existing=True,
        misfire_grace_time=600,
    )

    sched.start()
    _scheduler = sched
    logger.info(
        "scheduler/portal: запущен (daily_backup %02d:%02d МСК, "
        "audit_retention вс 04:00 МСК, retention=%d дней, "
        "auto_price_loads %s, auctions_ingest каждые %dч).",
        _BACKUP_HOUR, _BACKUP_MINUTE, _audit_retention_days(),
        ", ".join(
            f"{slug}={h:02d}:{m:02d}" for slug, h, m in _AUTO_PRICE_SCHEDULE
        ),
        _AUCTIONS_INGEST_INTERVAL_HOURS,
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
