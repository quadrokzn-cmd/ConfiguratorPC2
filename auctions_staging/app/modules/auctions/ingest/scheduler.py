from __future__ import annotations

import threading
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from app.core.db import get_engine
from app.modules.auctions.ingest.orchestrator import run_ingest_once

INGEST_JOB_ID = "auctions_ingest"
INGEST_INTERVAL_HOURS = 2

_scheduler: Optional[BackgroundScheduler] = None
_run_lock = threading.Lock()


def _ingest_tick() -> None:
    """Single-flighted tick — overlapping ticks are skipped, not queued."""
    if not _run_lock.acquire(blocking=False):
        logger.warning("ingest tick skipped: previous run still in progress")
        return
    try:
        engine = get_engine()
        run_ingest_once(engine)
    except Exception as exc:
        logger.exception("ingest tick failed: {}", exc)
    finally:
        _run_lock.release()


def start_scheduler(run_immediate: bool = True) -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    scheduler = BackgroundScheduler(timezone="Europe/Moscow")
    scheduler.add_job(
        _ingest_tick,
        trigger=IntervalTrigger(hours=INGEST_INTERVAL_HOURS),
        id=INGEST_JOB_ID,
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "APScheduler started; jobs: {}",
        [j.id for j in scheduler.get_jobs()],
    )
    _scheduler = scheduler

    if run_immediate:
        threading.Thread(target=_ingest_tick, name="ingest-immediate", daemon=True).start()

    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("APScheduler stopped")


def trigger_ingest_now() -> None:
    """Synchronous run intended for the /admin/run-ingest endpoint."""
    _ingest_tick()
