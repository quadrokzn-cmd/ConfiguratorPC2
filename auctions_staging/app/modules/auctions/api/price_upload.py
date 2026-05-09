from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import text

from app.core.auth import require_user
from app.core.db import get_engine
from app.core.templates import templates
from app.modules.auctions.price_loaders import LOADERS, get_loader
from app.modules.auctions.price_loaders.orchestrator import load_price

logger = logging.getLogger(__name__)

router = APIRouter()


def _suppliers_list() -> list[dict]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT code, name FROM suppliers "
                "WHERE code = ANY(:codes) "
                "ORDER BY name"
            ),
            {"codes": list(LOADERS.keys())},
        ).all()
    return [{"code": r.code, "name": r.name} for r in rows]


def _recent_uploads(limit: int = 10) -> list[dict]:
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT pu.id, pu.filename, pu.uploaded_at, pu.uploaded_by, "
                "       pu.rows_total, pu.rows_matched, pu.rows_unmatched, "
                "       pu.status, pu.notes, s.name AS supplier_name, s.code AS supplier_code "
                "FROM price_uploads pu "
                "JOIN suppliers s ON s.id = pu.supplier_id "
                "ORDER BY pu.uploaded_at DESC "
                "LIMIT :lim"
            ),
            {"lim": limit},
        ).all()
    result = []
    for r in rows:
        result.append(
            {
                "id": r.id,
                "filename": r.filename,
                "uploaded_at": r.uploaded_at,
                "uploaded_by": r.uploaded_by,
                "rows_total": r.rows_total,
                "rows_matched": r.rows_matched,
                "rows_unmatched": r.rows_unmatched,
                "status": r.status,
                "notes": r.notes,
                "supplier_name": r.supplier_name,
                "supplier_code": r.supplier_code,
            }
        )
    return result


def _render_partial(
    request: Request,
    *,
    last_report: dict | None = None,
    error: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        "_upload_price_form.html",
        {
            "request": request,
            "suppliers": _suppliers_list(),
            "uploads": _recent_uploads(),
            "last_report": last_report,
            "error": error,
        },
    )


@router.get("/nomenclature/upload-price", response_class=HTMLResponse)
def upload_price_partial(
    request: Request,
    _user: str = Depends(require_user),
):
    return _render_partial(request)


@router.post("/nomenclature/upload-price", response_class=HTMLResponse)
def upload_price(
    request: Request,
    supplier_code: str = Form(...),
    file: UploadFile = File(...),
    username: str = Depends(require_user),
):
    try:
        loader = get_loader(supplier_code)
    except ValueError as exc:
        return _render_partial(request, error=str(exc))

    suffix = Path(file.filename or "upload.xlsx").suffix or ".xlsx"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(file.file.read())
        tmp.flush()
        tmp.close()

        try:
            report = load_price(
                tmp.name,
                loader=loader,
                uploaded_by=username,
            )
            report["filename"] = file.filename or os.path.basename(tmp.name)
        except NotImplementedError as exc:
            return _render_partial(
                request,
                error=f"Адаптер «{supplier_code}» ещё не реализован: {exc}",
            )
        except Exception as exc:
            logger.exception("Ошибка загрузки прайса %s", supplier_code)
            return _render_partial(request, error=f"Ошибка: {exc}")

        return _render_partial(request, last_report=report)
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
