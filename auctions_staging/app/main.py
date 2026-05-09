from __future__ import annotations

import os

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from loguru import logger

from app.core.auth import require_user
from app.core.db import get_engine
from app.core.templates import templates
from app.modules.auctions.api.price_upload import router as price_upload_router
from app.modules.auctions.catalog.routes import router as nomenclature_router
from app.modules.auctions.ingest.orchestrator import run_ingest_once
from app.modules.auctions.ingest.repository import log_db_versions
from app.modules.auctions.ingest.scheduler import (
    shutdown_scheduler,
    start_scheduler,
    trigger_ingest_now,
)

app = FastAPI(title="QuadroTech")
app.include_router(price_upload_router)
app.include_router(nomenclature_router)


@app.on_event("startup")
def _on_startup() -> None:
    if os.getenv("INGEST_SCHEDULER_DISABLED", "").lower() in ("1", "true", "yes"):
        logger.info("INGEST_SCHEDULER_DISABLED=1 — APScheduler не стартует")
        return
    log_db_versions(get_engine())
    immediate = os.getenv("INGEST_RUN_IMMEDIATE", "true").lower() in ("1", "true", "yes")
    start_scheduler(run_immediate=immediate)


@app.on_event("shutdown")
def _on_shutdown() -> None:
    shutdown_scheduler()


@app.get("/healthz", response_class=PlainTextResponse)
def healthz(_: str = Depends(require_user)) -> str:
    return "OK"


@app.get("/")
def root(_: str = Depends(require_user)) -> RedirectResponse:
    return RedirectResponse(url="/auctions", status_code=302)


@app.get("/auctions")
def auctions_page(request: Request, username: str = Depends(require_user)):
    return templates.TemplateResponse(
        "auctions.html",
        {"request": request, "username": username, "active": "auctions"},
    )


@app.get("/settings")
def settings_page(request: Request, username: str = Depends(require_user)):
    return templates.TemplateResponse(
        "settings.html",
        {"request": request, "username": username, "active": "settings"},
    )


@app.post("/admin/run-ingest")
def admin_run_ingest(username: str = Depends(require_user)) -> JSONResponse:
    logger.info("/admin/run-ingest triggered by user={}", username)
    trigger_ingest_now()
    return JSONResponse({"status": "completed", "triggered_by": username})


@app.post("/admin/run-ingest-blocking")
def admin_run_ingest_blocking(username: str = Depends(require_user)) -> JSONResponse:
    logger.info("/admin/run-ingest-blocking triggered by user={}", username)
    stats = run_ingest_once(get_engine())
    return JSONResponse({"status": "completed", "triggered_by": username, "stats": stats.as_dict()})
