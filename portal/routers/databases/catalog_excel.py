# /databases/catalog-excel — выгрузка/загрузка каталога в Excel
# (Фаза 2 плана plans/2026-05-13-catalog-excel-export-import.md).
#
# Текущая фаза:
#   GET /databases/catalog-excel/download/{pc|printers}
#       Скачивание xlsx-файла. Доступ — только admin.
#
# UI-страница /databases/catalog-excel и sidebar — отдельная Фаза 4.
# Фаза 3 (import) добавит POST /databases/catalog-excel/upload/{...}.
#
# Файл собирается в памяти через openpyxl (write_only=False, чтобы
# поддерживалось autofilter + column_dimensions), затем отдаётся как
# attachment с правильным MIME-типом.

from __future__ import annotations

import logging
import tempfile
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from portal.services.catalog.excel_export import (
    default_filename,
    export_components_pc,
    export_printers_mfu,
)
from shared.audit import extract_request_meta, write_audit
from shared.audit_actions import ACTION_CATALOG_EXCEL_EXPORT
from shared.auth import AuthUser, require_admin


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/databases/catalog-excel")


_XLSX_MIME = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _cleanup_file(path: Path) -> None:
    """BackgroundTask: удаляет временный xlsx-файл после отдачи клиенту.
    Если файла нет (был перемещён или удалён вручную) — молча выходим."""
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:
        logger.warning(
            "catalog_excel: не удалось удалить временный файл %s: %s",
            path, exc,
        )


@router.get("/download/{target}")
def download_catalog_excel(
    target: str,
    request: Request,
    user: AuthUser = Depends(require_admin),
):
    """Скачивание xlsx с каталогом.

    target: 'pc' → «Комплектующие_ПК_YYYY-MM-DD.xlsx» (8 листов),
            'printers' → «Печатная_техника_YYYY-MM-DD.xlsx» (2 листа).

    Файл создаётся в tempfile (на Windows / Railway это TEMP-папка),
    отдаётся клиенту как attachment, и удаляется BackgroundTask'ом
    после того, как Starlette отправил последний байт.
    """
    if target not in ("pc", "printers"):
        raise HTTPException(
            status_code=404,
            detail="Неизвестный target. Допустимо 'pc' или 'printers'.",
        )

    filename = default_filename(target, today=date.today())
    # tempfile.mkstemp — создаёт уникальный файл, чтобы параллельные
    # скачивания не конфликтовали.
    fd, raw_path = tempfile.mkstemp(suffix=".xlsx", prefix="catalog_excel_")
    # Закрываем дескриптор — openpyxl откроет файл сам.
    import os
    os.close(fd)
    tmp_path = Path(raw_path)

    try:
        if target == "pc":
            report = export_components_pc(tmp_path)
        else:
            report = export_printers_mfu(tmp_path)
    except Exception:
        # При ошибке убираем за собой временный файл и пробрасываем 500.
        _cleanup_file(tmp_path)
        raise

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_CATALOG_EXCEL_EXPORT,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="catalog_excel",
        target_id=target,
        payload={
            "target":       target,
            "rows_count":   report.total_rows,
            "sheet_counts": report.sheet_counts,
            "rate_used":    str(report.rate_used),
            "rate_fallback": report.rate_is_fallback,
        },
        ip=ip,
        user_agent=ua,
    )

    return FileResponse(
        path=str(tmp_path),
        filename=filename,
        media_type=_XLSX_MIME,
        background=BackgroundTask(_cleanup_file, tmp_path),
    )
