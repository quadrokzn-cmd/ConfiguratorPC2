# /admin/price-uploads портала: ручная загрузка прайсов поставщиков
# через веб (этап 11.2).
#
# Эндпоинты:
#   GET  /admin/price-uploads                 — страница: 6 поставщиков,
#                                                форма загрузки, журнал
#                                                последних 30 загрузок.
#   POST /admin/price-uploads/run             — приём multipart-файла,
#                                                запись audit_log,
#                                                фоновый запуск orchestrator'а.
#   GET  /admin/price-uploads/{id}/details    — JSON-эндпоинт для модалки
#                                                «Подробности» (report_json).
#
# Все эндпоинты — require_admin.
#
# Архитектурное решение: импортируем app.services.price_loaders.orchestrator
# напрямую — это чистый Python в одном репозитории и одной БД, выносить в
# shared/ ради этого одной точки нет смысла. Подробности — в брифе 11.2 и
# в docs/architecture.md.
#
# Фоновое выполнение: FastAPI BackgroundTasks. Каждая фоновая задача
# открывает СВОЮ shared.db.SessionLocal — orchestrator не thread-safe
# по чужим сессиям. Для длинных загрузок (Netlab — до 2 минут) этого
# хватает в рамках одного процесса; если контейнер упадёт в момент
# загрузки, запись price_uploads останется в status='running' — и
# страница покажет её как «зависшую».

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.price_loaders import LOADERS
from portal.templating import templates
from shared.audit import extract_request_meta, write_audit
from shared.audit_actions import (
    ACTION_PRICE_UPLOAD_COMPLETE,
    ACTION_PRICE_UPLOAD_FAILED,
    ACTION_PRICE_UPLOAD_START,
    ACTION_PRICE_UPLOAD_VIEW,
)
from shared.auth import AuthUser, get_csrf_token, require_admin, verify_csrf
from shared.db import SessionLocal, get_db


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/admin/price-uploads")


# ---- Константы --------------------------------------------------------

# Максимальный размер загружаемого файла. DealerD.xlsx ≈ 10 МБ; ставим
# 100 МБ запас, чтобы не отвергать большие .xlsm/.zip.
_MAX_FILE_SIZE_BYTES = 100 * 1024 * 1024

# Допустимые расширения. Netlab принимает .zip (с DealerD.xlsx внутри).
_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".xlsx", ".xlsm", ".xls", ".csv", ".zip"})

# Сколько строк журнала показываем на странице.
_JOURNAL_LIMIT = 30

# Канонические имена 6 поставщиков, в том порядке, что мы хотим в UI.
# Ключ slug — то, что приходит из формы (соответствует LOADERS); name —
# что лежит в suppliers.name (точное соответствие, см. orchestrator и
# миграцию 019).
SUPPLIERS_ORDERED: list[tuple[str, str]] = [
    ("ocs",          "OCS"),
    ("merlion",      "Merlion"),
    ("treolan",      "Treolan"),
    ("netlab",       "Netlab"),
    ("resurs_media", "Ресурс Медиа"),
    ("green_place",  "Green Place"),
]


# ---- Время в МСК ------------------------------------------------------

def _to_msk_str(dt: datetime | None) -> str:
    """ДД.ММ.ГГГГ ЧЧ:ММ в МСК."""
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        dt_msk = dt.astimezone(ZoneInfo("Europe/Moscow"))
    except Exception:
        dt_msk = dt.astimezone(timezone(timedelta(hours=3)))
    return dt_msk.strftime("%d.%m.%Y %H:%M")


def _hours_since(dt: datetime | None) -> float | None:
    """Сколько часов прошло с момента dt. None — если dt None."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = datetime.now(tz=timezone.utc) - dt
    return delta.total_seconds() / 3600.0


def _freshness_badge(last_at: datetime | None) -> tuple[str, str]:
    """Возвращает (css_class, label) для бейджа свежести.

    Шкала из брифа 11.2:
      ≤24ч  → зелёный «Свежий»
      24-72ч → жёлтый «Устаревает»
      >72ч  → оранжевый «Старый»
      None  → серый «Не загружался»
    """
    hours = _hours_since(last_at)
    if hours is None:
        return ("badge-neutral", "Не загружался")
    if hours <= 24:
        return ("badge-success", "Свежий")
    if hours <= 72:
        return ("badge-warning", "Устаревает")
    return ("badge-danger", "Старый")


# ---- Чтение БД --------------------------------------------------------

def _suppliers_overview(db: Session) -> list[dict[str, Any]]:
    """Для таблицы наверху страницы: по каждому из 6 поставщиков —
    последняя загрузка (success/partial), бейдж свежести и счётчик цен."""
    names = [n for _slug, n in SUPPLIERS_ORDERED]
    last_by_name: dict[str, datetime | None] = {}
    prices_by_name: dict[str, int] = {}
    try:
        rows = db.execute(
            text(
                "SELECT s.name AS name, "
                "       MAX(pu.uploaded_at) FILTER ("
                "         WHERE pu.status IN ('success', 'partial')"
                "       ) AS last_at, "
                "       (SELECT COUNT(*) FROM supplier_prices sp "
                "         WHERE sp.supplier_id = s.id) AS prices_count "
                "FROM suppliers s "
                "LEFT JOIN price_uploads pu ON pu.supplier_id = s.id "
                "WHERE s.name = ANY(:names) "
                "GROUP BY s.id, s.name"
            ),
            {"names": names},
        ).all()
        for r in rows:
            last_by_name[r.name] = r.last_at
            prices_by_name[r.name] = int(r.prices_count or 0)
    except Exception as exc:
        # На пустой БД таблиц может не быть в каком-то edge-case-тесте.
        logger.warning("price_uploads overview failed: %s", exc)

    overview: list[dict[str, Any]] = []
    for slug, name in SUPPLIERS_ORDERED:
        last_at = last_by_name.get(name)
        badge_class, badge_label = _freshness_badge(last_at)
        overview.append({
            "slug":         slug,
            "name":         name,
            "last_at":      last_at,
            "last_at_msk":  _to_msk_str(last_at),
            "badge_class":  badge_class,
            "badge_label":  badge_label,
            "prices_count": prices_by_name.get(name, 0),
        })
    return overview


def _journal_rows(db: Session) -> list[dict[str, Any]]:
    """Последние N записей price_uploads с именем поставщика и счётчиками
    из notes/report_json. Если report_json пуст (старые записи до этапа
    11.2) — счётчики выводим из notes-строки."""
    sql = (
        "SELECT pu.id, pu.uploaded_at, pu.filename, pu.status, "
        "       pu.rows_total, pu.rows_matched, pu.rows_unmatched, "
        "       pu.notes, pu.report_json, "
        "       s.name AS supplier_name "
        "FROM price_uploads pu "
        "JOIN suppliers s ON s.id = pu.supplier_id "
        "ORDER BY pu.uploaded_at DESC, pu.id DESC "
        "LIMIT :lim"
    )
    rows: list[dict[str, Any]] = []
    try:
        result = db.execute(text(sql), {"lim": _JOURNAL_LIMIT}).all()
    except Exception as exc:
        logger.warning("price_uploads journal failed: %s", exc)
        return rows
    for r in result:
        report = r.report_json or {}
        if isinstance(report, str):
            import json as _json
            try:
                report = _json.loads(report)
            except Exception:
                report = {}
        added = report.get("added") if isinstance(report, dict) else None
        updated = report.get("updated") if isinstance(report, dict) else None
        skipped = report.get("skipped") if isinstance(report, dict) else None
        errors = report.get("errors") if isinstance(report, dict) else None
        # uploaded_by мы не пишем в price_uploads — берём из audit_log
        # по-дешёвому, через payload в reload-friendly виде. Здесь оставим
        # «—» (журнал и так есть в /admin/audit).
        rows.append({
            "id":             int(r.id),
            "uploaded_msk":   _to_msk_str(r.uploaded_at),
            "filename":       r.filename or "",
            "status":         r.status or "",
            "rows_total":     r.rows_total,
            "rows_matched":   r.rows_matched,
            "rows_unmatched": r.rows_unmatched,
            "notes":          r.notes or "",
            "supplier_name":  r.supplier_name,
            "counter_added":   added,
            "counter_updated": updated,
            "counter_skipped": skipped,
            "counter_errors":  errors,
        })
    return rows


# ---- GET /admin/price-uploads -----------------------------------------

@router.get("")
def price_uploads_index(
    request: Request,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Страница: 6 поставщиков, форма загрузки, журнал последних 30."""
    flash_error = request.session.pop("flash_error", None)
    flash_info = request.session.pop("flash_info", None)

    overview = _suppliers_overview(db)
    journal = _journal_rows(db)

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_PRICE_UPLOAD_VIEW,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        ip=ip,
        user_agent=ua,
    )

    return templates.TemplateResponse(
        request,
        "admin/price_uploads.html",
        {
            "user":              user,
            "csrf_token":        get_csrf_token(request),
            "suppliers":         overview,
            "journal":           journal,
            "max_size_mb":       _MAX_FILE_SIZE_BYTES // (1024 * 1024),
            "allowed_extensions": ", ".join(sorted(_ALLOWED_EXTENSIONS)),
            "error":             flash_error,
            "info":              flash_info,
        },
    )


# ---- POST /admin/price-uploads/run ------------------------------------

def _persist_upload_to_temp(uploaded: UploadFile) -> tuple[str, int]:
    """Сохраняет UploadFile в /tmp/price_uploads/<random>.<ext> и
    возвращает (путь, размер). Работает потоком, не загружая весь файл
    в память за раз — fastapi.UploadFile.read() умеет stream'ить."""
    base_dir = Path(tempfile.gettempdir()) / "kvadro_price_uploads"
    base_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(uploaded.filename or "").suffix.lower()
    fd, tmp_path = tempfile.mkstemp(prefix="upl_", suffix=ext, dir=str(base_dir))
    try:
        size = 0
        with os.fdopen(fd, "wb") as out:
            while True:
                chunk = uploaded.file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > _MAX_FILE_SIZE_BYTES:
                    out.close()
                    try:
                        os.unlink(tmp_path)
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
        return tmp_path, size
    except HTTPException:
        raise
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _run_loader_in_background(
    *,
    supplier_slug: str,
    tmp_path: str,
    original_filename: str,
    user_id: int,
    user_login: str,
    ip: str | None,
    user_agent: str | None,
) -> None:
    """Фоновая задача: запускает orchestrator.load_price, пишет аудит,
    удаляет временный файл. НИКОГДА не бросает исключение наверх — чтобы
    BackgroundTasks-воркер не залип в стеке.

    Импорт orchestrator'а здесь, а не на уровне модуля — так роутер
    подхватит свежий код в тестах с monkeypatch и не утянет тяжёлые
    зависимости при простом GET'е страницы.
    """
    started = time.monotonic()
    try:
        from app.services.price_loaders.orchestrator import load_price

        # Переименовать файл к "родному" имени для красивого filename в
        # price_uploads — копируем рядом с original-name, иначе все строки
        # журнала будут «upl_xxx.xlsx».
        renamed = str(Path(tmp_path).parent / Path(original_filename).name)
        try:
            shutil.move(tmp_path, renamed)
            run_path = renamed
        except Exception:
            run_path = tmp_path

        try:
            result = load_price(run_path, supplier_key=supplier_slug)
        finally:
            try:
                os.unlink(run_path)
            except OSError:
                pass

        duration = round(time.monotonic() - started, 3)
        logger.info(
            "price_upload: завершена supplier=%s file=%s status=%s "
            "added=%s updated=%s skipped=%s errors=%s за %ss",
            supplier_slug, original_filename, result.get("status"),
            result.get("added"), result.get("updated"),
            result.get("skipped"), result.get("errors"), duration,
        )
        write_audit(
            action=ACTION_PRICE_UPLOAD_COMPLETE,
            service="portal",
            user_id=user_id,
            user_login=user_login,
            target_type="price_upload",
            target_id=result.get("upload_id"),
            payload={
                "supplier_slug":  supplier_slug,
                "filename":       original_filename,
                "status":         result.get("status"),
                "added":          result.get("added"),
                "updated":        result.get("updated"),
                "skipped":        result.get("skipped"),
                "errors":         result.get("errors"),
                "duration_seconds": duration,
            },
            ip=ip,
            user_agent=user_agent,
        )
    except Exception as exc:
        # Любая ошибка orchestrator'а — пишем PRICE_UPLOAD_FAILED + tb,
        # отсылаем в Sentry и тихо выходим.
        tb = traceback.format_exc()
        logger.exception(
            "price_upload: фоновая задача упала supplier=%s file=%s",
            supplier_slug, original_filename,
        )
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass
        write_audit(
            action=ACTION_PRICE_UPLOAD_FAILED,
            service="portal",
            user_id=user_id,
            user_login=user_login,
            target_type="price_upload",
            payload={
                "supplier_slug":  supplier_slug,
                "filename":       original_filename,
                "error":          f"{type(exc).__name__}: {exc}",
                "traceback":      tb[:4000],  # обрезаем — в payload JSONB не нужно простыни
            },
            ip=ip,
            user_agent=user_agent,
        )
        # На всякий случай если файл ещё лежит — удаляем.
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


@router.post("/run")
def price_uploads_run(
    request: Request,
    background_tasks: BackgroundTasks,
    csrf_token: str = Form(""),
    supplier_slug: str = Form(...),
    uploaded_file: UploadFile = File(...),
    user: AuthUser = Depends(require_admin),
):
    """Принимает файл, валидирует, сохраняет во временный, ставит фоновую
    задачу orchestrator.load_price и редиректит обратно с flash-подсказкой.

    Возвраты:
      - 400 — неверный CSRF / неизвестный supplier_slug / файл пуст /
        неподдерживаемое расширение.
      - 413 — файл больше _MAX_FILE_SIZE_BYTES.
      - 302 — успех, в session кладём flash_info.
    """
    if not verify_csrf(request, csrf_token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")

    slug = (supplier_slug or "").strip().lower()
    if slug not in LOADERS:
        raise HTTPException(
            status_code=400,
            detail=f"Неизвестный поставщик «{supplier_slug}».",
        )

    if not uploaded_file or not (uploaded_file.filename or "").strip():
        raise HTTPException(status_code=400, detail="Файл не выбран.")

    ext = Path(uploaded_file.filename).suffix.lower()
    if ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Неподдерживаемое расширение «{ext}». "
                f"Разрешено: {', '.join(sorted(_ALLOWED_EXTENSIONS))}."
            ),
        )

    tmp_path, size_bytes = _persist_upload_to_temp(uploaded_file)
    if size_bytes == 0:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="Файл пустой.")

    ip, ua = extract_request_meta(request)
    write_audit(
        action=ACTION_PRICE_UPLOAD_START,
        service="portal",
        user_id=user.id,
        user_login=user.login,
        target_type="price_upload",
        payload={
            "supplier_slug": slug,
            "filename":      uploaded_file.filename,
            "size_bytes":    size_bytes,
        },
        ip=ip,
        user_agent=ua,
    )

    logger.info(
        "price_upload: ручной запуск supplier=%s file=%s size=%dB user=%s",
        slug, uploaded_file.filename, size_bytes, user.login,
    )

    background_tasks.add_task(
        _run_loader_in_background,
        supplier_slug=slug,
        tmp_path=tmp_path,
        original_filename=uploaded_file.filename,
        user_id=user.id,
        user_login=user.login,
        ip=ip,
        user_agent=ua,
    )

    request.session["flash_info"] = (
        "Загрузка началась в фоне. Обновите страницу через 1–2 минуты — "
        "результат появится в журнале ниже."
    )
    return RedirectResponse(
        url="/admin/price-uploads",
        status_code=status.HTTP_302_FOUND,
    )


# ---- GET /admin/price-uploads/{id}/details -----------------------------

@router.get("/{upload_id}/details")
def price_upload_details(
    upload_id: int,
    user: AuthUser = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """JSON-эндпоинт для модалки «Подробности». Возвращает report_json
    (или {} если поле пустое — старые записи до этапа 11.2)."""
    row = db.execute(
        text(
            "SELECT pu.id, pu.uploaded_at, pu.filename, pu.status, "
            "       pu.rows_total, pu.rows_matched, pu.rows_unmatched, "
            "       pu.notes, pu.report_json, "
            "       s.name AS supplier_name "
            "FROM price_uploads pu "
            "JOIN suppliers s ON s.id = pu.supplier_id "
            "WHERE pu.id = :id"
        ),
        {"id": upload_id},
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Загрузка не найдена.")

    report = row.report_json or {}
    if isinstance(report, str):
        import json as _json
        try:
            report = _json.loads(report)
        except Exception:
            report = {}

    body = {
        "id":             int(row.id),
        "uploaded_at":    row.uploaded_at.isoformat() if row.uploaded_at else None,
        "supplier":       row.supplier_name,
        "filename":       row.filename,
        "status":         row.status,
        "rows_total":     row.rows_total,
        "rows_matched":   row.rows_matched,
        "rows_unmatched": row.rows_unmatched,
        "notes":          row.notes,
        "report":         report,
    }
    return JSONResponse(content=body)
