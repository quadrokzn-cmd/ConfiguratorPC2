# Роутер экспорта проекта (этап 8).
#
# /project/{id}/export/excel    — xlsx-выгрузка (этап 8.1).
# /project/{id}/export/kp       — docx-КП с наценкой (этап 8.2).
# /project/{id}/emails/preview  — список черновиков писем поставщикам (этап 8.3).
# /project/{id}/emails/send     — массовая рассылка (этап 8.3).
#
# Оба эндпоинта экспорта запрашивают курс ЦБ РФ, загружают проект и
# спецификацию, собирают файл через соответствующий builder и отдают
# StreamingResponse с RFC 5987 Content-Disposition, чтобы браузер корректно
# скачивал файл с русским названием. Email-эндпоинты возвращают JSON.

from __future__ import annotations

import logging
from io import BytesIO
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.auth import AuthUser, require_login, verify_csrf
from app.config import settings
from app.database import get_db
from app.routers.project_router import _load_project_or_raise
from app.services.export import (
    email_composer,
    email_sender,
    excel_builder,
    exchange_rate,
    kp_builder,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


def _content_disposition(filename: str, *, ascii_fallback: str) -> str:
    """Content-Disposition по RFC 5987 — ASCII-фолбэк + UTF-8 параметр.

    Русское название в filename* URL-кодируется; для старых клиентов
    отдаём ASCII-транслит в filename=.
    """
    quoted = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quoted}"


@router.get("/project/{project_id}/export/excel")
def export_excel(
    project_id: int,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Excel-выгрузка проекта в формате шаблона «КВАДРО-ТЕХ».

    Возвращает 200 с файлом .xlsx или 403/404 если проект чужой/не найден.
    Если курс ЦБ не получен (нет сети И нет кэша) — 503.
    """
    project = _load_project_or_raise(db, project_id=project_id, user=user)

    try:
        rate, rate_date, source = exchange_rate.get_usd_rate()
    except RuntimeError as exc:
        logger.error("Excel-экспорт: не удалось получить курс ЦБ: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Не удалось получить курс ЦБ РФ и нет локального кэша.",
        )
    logger.info(
        "Excel-экспорт проекта %s: курс %s от %s (source=%s)",
        project_id, rate, rate_date, source,
    )

    xlsx_bytes = excel_builder.build_project_xlsx(
        project_id=project_id,
        db=db,
        rate=rate,
        rate_date=rate_date,
    )

    created = project["created_at"]
    # Русские буквы и пробелы в имени допустимы — их кодирует
    # _content_disposition через filename*=UTF-8''… Иначе хелпер
    # бы сам сложил пробелы в %20.
    safe_name = (project["name"] or "project").replace("/", "_").replace("\\", "_")
    filename = f"{safe_name}_{created.strftime('%Y-%m-%d_%H%M')}.xlsx"

    return StreamingResponse(
        BytesIO(xlsx_bytes),
        media_type=_XLSX_MEDIA_TYPE,
        headers={
            "Content-Disposition": _content_disposition(
                filename, ascii_fallback="export.xlsx",
            ),
        },
    )


@router.get("/project/{project_id}/export/kp")
def export_kp(
    project_id: int,
    markup: int = 15,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Сформировать коммерческое предложение (docx) по проекту.

    markup — целое число процентов (15 = +15%) к закупочной цене в рублях;
    результат: docx-файл по шаблону kp_template.docx с заменой даты,
    заполнением таблицы конфигураций и строки ИТОГО.

    Коды ответа: 200 с docx / 400 при неверном markup / 403 или 404 при
    отсутствии доступа / 503 если курс ЦБ недоступен и нет кэша.
    """
    project = _load_project_or_raise(db, project_id=project_id, user=user)

    try:
        data = kp_builder.build_kp_docx(
            project_id=project_id, markup_percent=markup, db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        logger.error("KP-экспорт: не удалось получить курс ЦБ: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Не удалось получить курс ЦБ РФ и нет локального кэша.",
        )

    created = project["created_at"]
    safe_name = (project["name"] or "project").replace("/", "_").replace("\\", "_")
    filename = f"{safe_name}_{created.strftime('%Y-%m-%d_%H%M')}.docx"

    return StreamingResponse(
        BytesIO(data),
        media_type=_DOCX_MEDIA_TYPE,
        headers={
            "Content-Disposition": _content_disposition(
                filename, ascii_fallback="export.docx",
            ),
        },
    )


# =====================================================================
# Email-запросы поставщикам (этап 8.3)
# =====================================================================


class EmailSendItem(BaseModel):
    """Один элемент POST /emails/send — что отправить конкретному поставщику."""
    model_config = ConfigDict(extra="forbid")
    supplier_id: int
    to_email:    str
    subject:     str = Field(min_length=1)
    body_html:   str = Field(min_length=1)


class EmailSendPayload(BaseModel):
    """Тело POST-запроса /emails/send — список писем к отправке."""
    model_config = ConfigDict(extra="forbid")
    items: List[EmailSendItem] = Field(default_factory=list)


def _verify_csrf_ajax(request: Request) -> None:
    """Проверяет CSRF-токен в заголовке X-CSRF-Token.

    На /emails/send клиент должен его отдавать — как и для остальных AJAX
    (select/deselect/update_quantity в project_router).
    """
    token = request.headers.get("x-csrf-token", "")
    if not verify_csrf(request, token):
        raise HTTPException(status_code=400, detail="Неверный CSRF-токен.")


@router.get("/project/{project_id}/emails/preview")
def preview_emails(
    project_id: int,
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Черновики писем поставщикам по проекту.

    Каждый элемент содержит готовые subject/body_html, имя и id поставщика,
    его email (может быть null — тогда can_send=False и UI блокирует кнопку).
    """
    # Проверка доступа: 404/403 обработаются до выхода в build_supplier_emails.
    _load_project_or_raise(db, project_id=project_id, user=user)

    try:
        drafts = email_composer.build_supplier_emails(
            project_id=project_id, db=db,
        )
    except RuntimeError as exc:
        # build_supplier_emails тянет курс ЦБ — если ни сети, ни кэша нет,
        # exchange_rate кидает RuntimeError. Для UI это 503.
        logger.error("Email-preview: нет курса ЦБ: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Не удалось получить курс ЦБ РФ и нет локального кэша.",
        )

    payload = [
        {
            "supplier_id":   d.supplier_id,
            "supplier_name": d.supplier_name,
            "to_email":      d.to_email,
            "subject":       d.subject,
            "body_html":     d.body_html,
            "items_count":   d.items_count,
            "can_send":      d.to_email is not None and bool(d.to_email.strip()),
        }
        for d in drafts
    ]
    return JSONResponse({"ok": True, "items": payload})


@router.post("/project/{project_id}/emails/send")
def send_emails(
    project_id: int,
    request: Request,
    payload: EmailSendPayload = Body(...),
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Рассылает письма выбранным поставщикам.

    Правила валидации:
      - CSRF обязательный (X-CSRF-Token).
      - Каждый item.supplier_id должен быть среди «победителей» для этого
        проекта (иначе менеджер может прислать в UI что угодно).
      - Отправка НЕ прерывается на первой ошибке: каждый адресат
        получает своё письмо, ошибка конкретного не мешает соседям.

    Каждая попытка логируется в sent_emails (status='sent'|'failed') —
    независимо от результата. Возвращается JSON со статусом по каждому.
    """
    _verify_csrf_ajax(request)
    _load_project_or_raise(db, project_id=project_id, user=user)

    try:
        drafts = email_composer.build_supplier_emails(
            project_id=project_id, db=db,
        )
    except RuntimeError as exc:
        logger.error("Email-send: нет курса ЦБ: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Не удалось получить курс ЦБ РФ и нет локального кэша.",
        )
    allowed: dict[int, email_composer.SupplierEmailDraft] = {
        d.supplier_id: d for d in drafts
    }

    results: list[dict] = []
    for item in payload.items:
        draft = allowed.get(item.supplier_id)
        if draft is None:
            # Поставщика нет среди победителей — это либо подделка запроса,
            # либо состояние проекта изменилось между preview и send.
            # Не тратим SMTP-квоту, сразу пишем failed в лог.
            error_msg = (
                "Поставщик не относится к этому проекту — возможно, "
                "спецификация изменилась. Обновите страницу."
            )
            _record_send(
                db,
                project_id=project_id,
                supplier_id=item.supplier_id,
                user_id=user.id,
                to_email=item.to_email,
                subject=item.subject,
                body_html=item.body_html,
                status="failed",
                error_message=error_msg,
            )
            results.append({
                "supplier_id":  item.supplier_id,
                "status":       "failed",
                "error_message": error_msg,
            })
            continue

        bcc = settings.smtp_bcc or None
        try:
            email_sender.send_email(
                to_email=item.to_email,
                subject=item.subject,
                body_html=item.body_html,
                bcc=bcc,
            )
        except email_sender.EmailSendError as exc:
            error_msg = str(exc)
            _record_send(
                db,
                project_id=project_id,
                supplier_id=item.supplier_id,
                user_id=user.id,
                to_email=item.to_email,
                subject=item.subject,
                body_html=item.body_html,
                status="failed",
                error_message=error_msg,
            )
            results.append({
                "supplier_id":  item.supplier_id,
                "status":       "failed",
                "error_message": error_msg,
            })
            continue

        _record_send(
            db,
            project_id=project_id,
            supplier_id=item.supplier_id,
            user_id=user.id,
            to_email=item.to_email,
            subject=item.subject,
            body_html=item.body_html,
            status="sent",
            error_message=None,
        )
        results.append({
            "supplier_id": item.supplier_id,
            "status":      "sent",
        })

    return JSONResponse({"ok": True, "results": results})


def _record_send(
    db: Session,
    *,
    project_id: int,
    supplier_id: int,
    user_id: int,
    to_email: str,
    subject: str,
    body_html: str,
    status: str,
    error_message: Optional[str],
) -> None:
    """Логирует попытку отправки в sent_emails.

    Commit делается здесь — каждая отправка самостоятельна, а откат одного
    письма не должен терять лог остальных в том же запросе.
    """
    db.execute(
        text(
            "INSERT INTO sent_emails "
            "  (project_id, supplier_id, sent_by_user_id, to_email, "
            "   subject, body_html, status, error_message) "
            "VALUES "
            "  (:pid, :sid, :uid, :to, :subj, :body, :st, :err)"
        ),
        {
            "pid":  project_id, "sid": supplier_id, "uid": user_id,
            "to":   to_email,
            "subj": subject, "body": body_html,
            "st":   status, "err": error_message,
        },
    )
    db.commit()
