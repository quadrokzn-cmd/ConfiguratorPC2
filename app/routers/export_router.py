# Роутер экспорта проекта (этап 8).
#
# /project/{id}/export/excel — xlsx-выгрузка (этап 8.1).
# /project/{id}/export/kp    — docx-КП с наценкой (этап 8.2).
#
# Оба эндпоинта запрашивают курс ЦБ РФ, загружают проект и спецификацию,
# собирают файл через соответствующий builder и отдают StreamingResponse
# с RFC 5987 Content-Disposition, чтобы браузер корректно скачивал файл
# с русским названием.

from __future__ import annotations

import logging
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import AuthUser, require_login
from app.database import get_db
from app.routers.project_router import _load_project_or_raise
from app.services.export import excel_builder, exchange_rate, kp_builder

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
