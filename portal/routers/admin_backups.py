# /admin/backups портала: управление бекапами БД (этап 9В.2).
#
# Эндпоинты:
#   GET  /admin/backups               — список объектов в B2 по трём
#                                       уровням (daily/weekly/monthly).
#   POST /admin/backups/create        — ручной запуск perform_backup в фоне.
#   GET  /admin/backups/download/...  — скачивание конкретного дампа.
#
# Все эндпоинты — require_admin.

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Form,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import RedirectResponse, StreamingResponse

from portal.services import backup_service
from portal.templating import templates
from shared.audit import extract_request_meta, write_audit
from shared.audit_actions import (
    ACTION_BACKUP_DOWNLOAD,
    ACTION_BACKUP_MANUAL,
)
from shared.auth import AuthUser, get_csrf_token, require_admin, verify_csrf


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/admin/backups")


_MSK_OFFSET_HOURS = 3  # упрощённая проекция в МСК для отображения списка


def _to_msk_str(dt: datetime | None) -> str:
    """Безопасное форматирование даты в МСК для UI."""
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Используем zoneinfo если доступен — иначе сдвиг UTC+3.
    try:
        from zoneinfo import ZoneInfo
        dt_msk = dt.astimezone(ZoneInfo("Europe/Moscow"))
    except Exception:
        from datetime import timedelta
        dt_msk = dt.astimezone(timezone(timedelta(hours=_MSK_OFFSET_HOURS)))
    return dt_msk.strftime("%d.%m.%Y %H:%M")


def _human_size(size_bytes: int) -> str:
    """Преобразует размер в KB/MB/GB."""
    n = float(size_bytes or 0)
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{size_bytes} Б"


def _filename_of(key: str) -> str:
    """daily/kvadro_tech_2026-04-28T03-00-00.dump → kvadro_tech_2026-...dump"""
    return key.split("/", 1)[-1]


def _filter_by_tier(items: Iterable[dict], tier: str) -> list[dict]:
    rows: list[dict] = []
    for it in items:
        if it["tier"] != tier:
            continue
        rows.append({
            "key":         it["key"],
            "filename":    _filename_of(it["key"]),
            "size_human":  _human_size(it["size_bytes"]),
            "created_msk": _to_msk_str(it["last_modified"]),
            "tier":        tier,
        })
    return rows


@router.get("")
def backups_list(
    request: Request,
    user: AuthUser = Depends(require_admin),
):
    """Страница со списком бекапов и кнопкой ручного запуска."""
    error: str | None = request.session.pop("flash_error", None)
    info: str | None = request.session.pop("flash_info", None)

    daily_rows: list[dict] = []
    weekly_rows: list[dict] = []
    monthly_rows: list[dict] = []
    list_error: str | None = None

    try:
        items = backup_service.list_backups()
        daily_rows = _filter_by_tier(items, "daily")
        weekly_rows = _filter_by_tier(items, "weekly")
        monthly_rows = _filter_by_tier(items, "monthly")
    except Exception as exc:
        # Если B2 не настроен или недоступен — страница всё равно
        # должна открываться, чтобы админ видел сообщение и мог
        # нажать «Создать бекап» (что покажет ту же ошибку явно).
        logger.warning("backups: list_backups упал: %s", type(exc).__name__)
        list_error = (
            "Не удалось получить список бекапов из Backblaze B2: "
            f"{type(exc).__name__}. Проверьте B2_* переменные окружения."
        )

    return templates.TemplateResponse(
        request,
        "admin/backups.html",
        {
            "user":         user,
            "csrf_token":   get_csrf_token(request),
            "daily_rows":   daily_rows,
            "weekly_rows":  weekly_rows,
            "monthly_rows": monthly_rows,
            "list_error":   list_error,
            "error":        error,
            "info":         info,
        },
    )


def _run_backup_safely() -> None:
    """Обёртка для BackgroundTasks: ловит исключения, чтобы фоновый
    воркер не унёс с собой uncaught-стек в логи Railway. perform_backup
    сам логирует exception со stack trace."""
    try:
        backup_service.perform_backup()
    except Exception:
        # Уже залогировано внутри perform_backup; здесь молчим.
        pass


@router.post("/create")
def backups_create(
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(""),
    user: AuthUser = Depends(require_admin),
):
    """Ручной запуск бекапа. Чтобы не держать запрос на 30+ секундах
    pg_dump'а, запускаем perform_backup в BackgroundTasks и сразу
    возвращаем 302 на /admin/backups с флешем."""
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    background_tasks.add_task(_run_backup_safely)
    request.session["flash_info"] = (
        "Бекап создаётся в фоне. Обновите страницу через минуту — он появится в списке."
    )
    logger.info("backup: ручной запуск (admin_id=%s, login=%s)", user.id, user.login)
    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_BACKUP_MANUAL,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        ip=ip,
        user_agent=ua,
    )
    return RedirectResponse(url="/admin/backups", status_code=status.HTTP_302_FOUND)


@router.get("/download/{tier}/{filename}")
def backups_download(
    tier: str,
    filename: str,
    request: Request,
    user: AuthUser = Depends(require_admin),
):
    """Скачивание бекапа из B2. Защита от path traversal: tier — whitelist,
    filename — regex ^kvadro_tech_[\\d\\-T]+\\.dump$."""
    if not backup_service.is_valid_tier(tier):
        raise HTTPException(status_code=404, detail="Неизвестный уровень бекапа.")
    if not backup_service.is_valid_backup_filename(filename):
        raise HTTPException(status_code=400, detail="Недопустимое имя файла бекапа.")

    key = f"{tier}/{filename}"
    try:
        client, cfg = backup_service._make_b2_client()
        obj = client.get_object(Bucket=cfg.bucket, Key=key)
    except Exception as exc:
        logger.warning("backup: download failed key=%s: %s", key, type(exc).__name__)
        raise HTTPException(status_code=404, detail="Бекап не найден.") from exc

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_BACKUP_DOWNLOAD,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="backup",
        target_id=key,
        payload={"tier": tier, "filename": filename},
        ip=ip,
        user_agent=ua,
    )

    body = obj.get("Body")
    size = obj.get("ContentLength")

    def _iter_body():
        if body is None:
            return
        try:
            for chunk in body.iter_chunks(chunk_size=64 * 1024):
                yield chunk
        finally:
            try:
                body.close()
            except Exception:
                pass

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
    }
    if size is not None:
        headers["Content-Length"] = str(size)

    return StreamingResponse(
        _iter_body(),
        media_type="application/octet-stream",
        headers=headers,
    )
