# /databases/catalog-excel — выгрузка/загрузка каталога в Excel
# (Фазы 2 и 3 плана plans/2026-05-13-catalog-excel-export-import.md).
#
# Эндпоинты:
#   GET  /databases/catalog-excel/download/{pc|printers}
#        Скачивание xlsx-файла. Доступ — только admin (Фаза 2).
#   POST /databases/catalog-excel/upload/{pc|printers}
#        Загрузка xlsx-файла, импорт в БД, JSON-отчёт. Только admin (Фаза 3).
#
# UI-страница /databases/catalog-excel и sidebar — отдельная Фаза 4.
#
# Export-флоу: файл собирается в памяти через openpyxl
# (write_only=False, чтобы поддерживалось autofilter +
# column_dimensions), затем отдаётся как attachment с правильным
# MIME-типом.
#
# Import-флоу: файл сохраняется в `data/catalog_imports/<timestamp>_<name>.xlsx`,
# затем синхронно вызывается соответствующий import_components_pc /
# import_printers_mfu, который читает xlsx, валидирует и применяет
# UPDATE/INSERT в каталоговых таблицах. Возвращается JSON-отчёт со
# счётчиками и списком ошибок.

from __future__ import annotations

import logging
import re
import tempfile
from datetime import date, datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from portal.services.catalog.excel_export import (
    default_filename,
    export_components_pc,
    export_printers_mfu,
)
from portal.services.catalog.excel_import import (
    ImportReport,
    import_components_pc,
    import_printers_mfu,
)
from shared.audit import extract_request_meta, write_audit
from shared.audit_actions import (
    ACTION_CATALOG_EXCEL_EXPORT,
    ACTION_CATALOG_EXCEL_IMPORT,
)
from shared.auth import AuthUser, require_admin, verify_csrf


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/databases/catalog-excel")


_XLSX_MIME = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


# ---------------------------------------------------------------------------
# Export (Фаза 2)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Import (Фаза 3)
# ---------------------------------------------------------------------------


# Максимальный размер xlsx-файла. Полный каталог комплектующих +
# печатной техники сейчас ~5 МБ; 100 МБ — комфортный запас.
_MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024
_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".xlsx"})

# Корневая папка для сохранения загруженных файлов. Относительный путь
# от cwd процесса — она же используется в Dockerfile.portal (WORKDIR /app).
_UPLOAD_ROOT = Path("data/catalog_imports")


_FILENAME_SAFE_RE = re.compile(r"[^A-Za-zА-Яа-я0-9._-]+")


def _sanitize_filename(name: str) -> str:
    """Делает имя файла безопасным для файловой системы. Кириллицу не
    трогаем — она допустима в путях Windows/Linux, а админ удобнее
    узнаёт свой файл, если он остался читаемым."""
    name = name.strip().replace(" ", "_")
    name = _FILENAME_SAFE_RE.sub("", name)
    if not name:
        name = "catalog.xlsx"
    if not name.lower().endswith(".xlsx"):
        name = name + ".xlsx"
    return name


def _save_uploaded(
    uploaded: UploadFile,
    *,
    kind: str,
) -> tuple[Path, int]:
    """Сохраняет UploadFile на диск, контролирует размер.

    Возвращает (Path, size_bytes). Папка `data/catalog_imports/` создаётся
    при необходимости.
    """
    _UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    safe = _sanitize_filename(uploaded.filename or f"{kind}.xlsx")
    target = _UPLOAD_ROOT / f"{timestamp}_{safe}"

    size = 0
    with target.open("wb") as out:
        while True:
            chunk = uploaded.file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > _MAX_FILE_SIZE_BYTES:
                try:
                    out.close()
                    target.unlink(missing_ok=True)
                except OSError:
                    pass
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=(
                        f"Файл слишком большой. Максимум "
                        f"{_MAX_FILE_SIZE_BYTES // (1024 * 1024)} МБ."
                    ),
                )
            out.write(chunk)
    return target, size


@router.post("/upload/{kind}")
def catalog_excel_upload(
    kind: str,
    request: Request,
    uploaded_file: UploadFile = File(...),
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
):
    """Принимает xlsx-файл и запускает соответствующий импорт.

    kind:
      - `pc`       → import_components_pc (8 листов);
      - `printers` → import_printers_mfu (2 листа).

    Возвраты:
      - 200 JSON `{updated, inserted, skipped, errors_count, errors,
                   warnings, saved_path}` при успешном импорте;
      - 400 — неверный CSRF / неизвестный kind / пустой файл /
        неподдерживаемое расширение;
      - 413 — файл больше _MAX_FILE_SIZE_BYTES;
      - 500 — SQL-ошибка во время импорта (вся транзакция откатывается).
    """
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    kind = (kind or "").strip().lower()
    if kind not in ("pc", "printers"):
        raise HTTPException(
            status_code=400,
            detail=f"Неизвестный kind «{kind}». Допустимо: pc, printers.",
        )

    if not uploaded_file or not (uploaded_file.filename or "").strip():
        raise HTTPException(status_code=400, detail="Файл не выбран.")

    ext = Path(uploaded_file.filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Неподдерживаемое расширение «{ext}». "
                f"Допустимо: {', '.join(sorted(_ALLOWED_EXTENSIONS))}."
            ),
        )

    saved_path, size_bytes = _save_uploaded(uploaded_file, kind=kind)
    if size_bytes == 0:
        try:
            saved_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="Файл пустой.")

    # Запускаем импорт синхронно — он работает с локальной БД и должен
    # уложиться в несколько секунд даже на полном каталоге (~1.5к строк
    # ПК + ~3к строк печатной техники). Если позже потребуется async —
    # обернуть в BackgroundTasks по тому же шаблону, что admin_price_uploads.
    report: ImportReport
    try:
        if kind == "pc":
            report = import_components_pc(saved_path, user_id=user.id)
        else:
            report = import_printers_mfu(saved_path, user_id=user.id)
        report.saved_path = str(saved_path)
    except Exception as exc:
        logger.exception(
            "catalog_excel_upload: импорт упал kind=%s file=%s",
            kind, uploaded_file.filename,
        )
        # audit с error-меткой
        ip, ua = extract_request_meta(request)
        write_audit(
            action=ACTION_CATALOG_EXCEL_IMPORT,
            service="portal",
            user_id=user.id,
            user_login=user.login,
            target_type="catalog_excel",
            target_id=kind,
            payload={
                "target":      kind,
                "saved_path":  str(saved_path),
                "error":       f"{type(exc).__name__}: {exc}",
                "user_id":     user.id,
            },
            ip=ip,
            user_agent=ua,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Импорт упал: {type(exc).__name__}: {exc}",
        )

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_CATALOG_EXCEL_IMPORT,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="catalog_excel",
        target_id=kind,
        payload={
            "target":     kind,
            "updated":    report.updated,
            "inserted":   report.inserted,
            "skipped":    report.skipped,
            "errors":     report.error_count,
            "saved_path": str(saved_path),
            "user_id":    user.id,
        },
        ip=ip,
        user_agent=ua,
    )

    return JSONResponse({
        "updated":      report.updated,
        "inserted":     report.inserted,
        "skipped":      report.skipped,
        "errors_count": report.error_count,
        "errors":       [
            {"sheet": e.sheet, "row": e.row, "message": e.message}
            for e in report.errors
        ],
        "warnings":     list(report.warnings),
        "saved_path":   str(saved_path),
    })
