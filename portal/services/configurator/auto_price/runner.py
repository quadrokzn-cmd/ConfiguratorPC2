# Оркестратор автозагрузки прайса (этап 12.3).
#
# run_auto_load(slug, triggered_by) — единственная точка запуска.
# Используется и UI («Запустить сейчас»), и APScheduler-задачей в 04:00 МСК.
#
# Логика:
#   1. Найти fetcher по slug. Если не зарегистрирован — ValueError.
#   2. Если triggered_by='manual' и last_run_at < 5 минут назад —
#      бросить TooFrequentRunError. Защита от случайного даблклика
#      на «Запустить сейчас», который мог бы загнать поставщика в
#      rate-limit (Treolan API — особенно). Для scheduled этой защиты
#      нет: она и не нужна, у нас раз в сутки.
#   3. INSERT в auto_price_load_runs (status='running', started_at=NOW).
#   4. UPDATE auto_price_loads SET status='running', last_run_at=NOW.
#      ↑ если таких строк ещё нет (на свежей БД до seed) — INSERT.
#   5. Вызвать fetcher.fetch_and_save() → price_upload_id.
#      success: status='success', last_success_at=NOW,
#               last_price_upload_id=<id>, last_error_message=NULL.
#      error:   status='error', last_error_at=NOW,
#               last_error_message=<truncate 2000>;
#               Sentry.capture_exception; перебросить наверх.
#   6. Параллельно — обновить строку в auto_price_load_runs.
#   7. Вернуть dict со сводкой для UI.

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from portal.services.configurator.auto_price.base import get_fetcher_class
from portal.services.configurator.auto_price.fetchers.base_imap import NoNewDataException
from shared.db import SessionLocal


logger = logging.getLogger(__name__)


# Защита от частых ручных запусков. 5 минут — достаточно, чтобы успеть
# увидеть в журнале результат предыдущего; больше не нужно, иначе админ
# не сможет повторить после исправления креденшелов.
MANUAL_THROTTLE_SECONDS = 5 * 60


class TooFrequentRunError(RuntimeError):
    """Ручной запуск отбит throttle'ом MANUAL_THROTTLE_SECONDS."""


# ---------------------------------------------------------------------
# Низкоуровневые операции с auto_price_loads / auto_price_load_runs
# ---------------------------------------------------------------------

def _get_or_create_state(session, slug: str) -> dict[str, Any]:
    """Возвращает строку auto_price_loads (как dict). Если её нет —
    создаёт с enabled=FALSE и возвращает свежую."""
    row = session.execute(
        text(
            "SELECT id, supplier_slug, enabled, status, last_run_at, "
            "       last_success_at, last_error_at, last_error_message, "
            "       last_price_upload_id "
            "FROM auto_price_loads WHERE supplier_slug = :slug"
        ),
        {"slug": slug},
    ).first()
    if row is None:
        session.execute(
            text(
                "INSERT INTO auto_price_loads (supplier_slug, enabled) "
                "VALUES (:slug, FALSE) "
                "ON CONFLICT (supplier_slug) DO NOTHING"
            ),
            {"slug": slug},
        )
        session.commit()
        row = session.execute(
            text(
                "SELECT id, supplier_slug, enabled, status, last_run_at, "
                "       last_success_at, last_error_at, last_error_message, "
                "       last_price_upload_id "
                "FROM auto_price_loads WHERE supplier_slug = :slug"
            ),
            {"slug": slug},
        ).first()
    return {
        "id":                   int(row.id),
        "supplier_slug":        row.supplier_slug,
        "enabled":              bool(row.enabled),
        "status":               row.status,
        "last_run_at":          row.last_run_at,
        "last_success_at":      row.last_success_at,
        "last_error_at":        row.last_error_at,
        "last_error_message":   row.last_error_message,
        "last_price_upload_id": row.last_price_upload_id,
    }


def _set_running(session, slug: str) -> None:
    """status=running, last_run_at=NOW, updated_at=NOW."""
    session.execute(
        text(
            "UPDATE auto_price_loads "
            "   SET status = 'running', last_run_at = NOW(), updated_at = NOW() "
            " WHERE supplier_slug = :slug"
        ),
        {"slug": slug},
    )


def _set_success(session, slug: str, price_upload_id: int | None) -> None:
    session.execute(
        text(
            "UPDATE auto_price_loads "
            "   SET status = 'success', "
            "       last_success_at = NOW(), "
            "       last_error_message = NULL, "
            "       last_price_upload_id = :pu_id, "
            "       updated_at = NOW() "
            " WHERE supplier_slug = :slug"
        ),
        {"slug": slug, "pu_id": price_upload_id},
    )


def _set_error(session, slug: str, error_message: str) -> None:
    truncated = (error_message or "")[:2000]
    session.execute(
        text(
            "UPDATE auto_price_loads "
            "   SET status = 'error', "
            "       last_error_at = NOW(), "
            "       last_error_message = :msg, "
            "       updated_at = NOW() "
            " WHERE supplier_slug = :slug"
        ),
        {"slug": slug, "msg": truncated},
    )


def _start_run(session, slug: str, triggered_by: str) -> int:
    row = session.execute(
        text(
            "INSERT INTO auto_price_load_runs "
            "    (supplier_slug, started_at, status, triggered_by) "
            "VALUES (:slug, NOW(), 'running', :tb) "
            "RETURNING id"
        ),
        {"slug": slug, "tb": triggered_by},
    ).first()
    return int(row.id)


def _finish_run_success(
    session, run_id: int, price_upload_id: int | None,
) -> None:
    session.execute(
        text(
            "UPDATE auto_price_load_runs "
            "   SET finished_at = NOW(), status = 'success', "
            "       price_upload_id = :pu_id, error_message = NULL "
            " WHERE id = :id"
        ),
        {"id": run_id, "pu_id": price_upload_id},
    )


def _finish_run_error(session, run_id: int, error_message: str) -> None:
    truncated = (error_message or "")[:2000]
    session.execute(
        text(
            "UPDATE auto_price_load_runs "
            "   SET finished_at = NOW(), status = 'error', "
            "       error_message = :msg "
            " WHERE id = :id"
        ),
        {"id": run_id, "msg": truncated},
    )


def _finish_run_no_new_data(session, run_id: int, message: str) -> None:
    """12.1: запуск отбит на этапе IMAP-fetcher'а — нет новых писем.
    Это НЕ ошибка: status='no_new_data', error_message содержит
    текст из NoNewDataException (для журнала). source_ref остаётся
    NULL — Message-ID нечего записать."""
    truncated = (message or "")[:2000]
    session.execute(
        text(
            "UPDATE auto_price_load_runs "
            "   SET finished_at = NOW(), status = 'no_new_data', "
            "       error_message = :msg "
            " WHERE id = :id"
        ),
        {"id": run_id, "msg": truncated},
    )


def _set_no_new_data(session, slug: str) -> None:
    """12.1: фиксируем попытку, не трогая last_success_at / last_error_at.
    last_run_at уже обновлён в _set_running. Статус — 'no_new_data',
    чтобы UI отобразил yellow badge «Нет новых писем». last_error_message
    очищаем — новая попытка ≠ ошибка."""
    session.execute(
        text(
            "UPDATE auto_price_loads "
            "   SET status = 'no_new_data', "
            "       last_error_message = NULL, "
            "       updated_at = NOW() "
            " WHERE supplier_slug = :slug"
        ),
        {"slug": slug},
    )


def _record_source_ref(session, run_id: int, source_ref: str | None) -> None:
    """12.1: на success-пути записываем Message-ID письма, обработанного
    IMAP-fetcher'ом. Для REST-канала (Treolan) source_ref остаётся NULL —
    идемпотентность там обеспечена самим API."""
    if not source_ref:
        return
    session.execute(
        text(
            "UPDATE auto_price_load_runs "
            "   SET source_ref = :ref "
            " WHERE id = :id"
        ),
        {"id": run_id, "ref": source_ref[:1000]},
    )


# ---------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------

def _too_frequent(last_run_at: datetime | None) -> bool:
    """True, если с last_run_at прошло меньше MANUAL_THROTTLE_SECONDS."""
    if last_run_at is None:
        return False
    if last_run_at.tzinfo is None:
        last_run_at = last_run_at.replace(tzinfo=timezone.utc)
    delta = datetime.now(tz=timezone.utc) - last_run_at
    return delta.total_seconds() < MANUAL_THROTTLE_SECONDS


def run_auto_load(slug: str, triggered_by: str) -> dict[str, Any]:
    """Запускает автозагрузку прайса для slug.

    triggered_by:
      'manual'    — кнопка «Запустить сейчас» в UI портала.
                    Действует throttle MANUAL_THROTTLE_SECONDS.
      'scheduled' — APScheduler в 04:00 МСК. Throttle игнорируется.

    Бросает:
      ValueError            — нет такого fetcher'а.
      TooFrequentRunError   — manual слишком быстро после предыдущего.
      Любое другое          — fetcher упал; уже залогировано в auto_price_loads
                              и в Sentry. Перебрасываем наверх, чтобы UI
                              показал 500/flash, а APScheduler-агрегатор
                              в portal/scheduler.py поймал и пошёл к следующему.

    Возвращает dict — сводку для UI, плюс run_id и price_upload_id.
    """
    fetcher_cls = get_fetcher_class(slug)
    if fetcher_cls is None:
        raise ValueError(
            f"Нет зарегистрированного fetcher'а для поставщика «{slug}». "
            "Подключение этого канала появится в одном из подэтапов 12.x."
        )

    session = SessionLocal()
    try:
        state = _get_or_create_state(session, slug)
        if triggered_by == "manual" and _too_frequent(state.get("last_run_at")):
            raise TooFrequentRunError(
                f"С предыдущего запуска прошло меньше "
                f"{MANUAL_THROTTLE_SECONDS // 60} минут. Подождите."
            )

        run_id = _start_run(session, slug, triggered_by)
        _set_running(session, slug)
        session.commit()
    finally:
        session.close()

    # Сам fetcher работает на собственной сессии (orchestrator тоже
    # открывает свою). Мы держим транзакцию auto_price_loads-а отдельно,
    # чтобы её commit/rollback не затирался ошибками внутри fetcher'а.
    price_upload_id: int | None = None
    error: Exception | None = None
    no_new_data: NoNewDataException | None = None
    fetcher_instance = None
    try:
        fetcher_instance = fetcher_cls()
        price_upload_id = fetcher_instance.fetch_and_save()
    except NoNewDataException as exc:
        # 12.1: IMAP-fetcher не нашёл нового письма за окно. Это НЕ
        # ошибка и НЕ повод вызывать orchestrator/save_price_rows —
        # пустой rows обнулил бы supplier_prices.stock_qty (см. 12.3-fix
        # в orchestrator total_rows==0 → failed без disappeared, плюс
        # ещё одна страховка на этом уровне). Поэтому помечаем run
        # 'no_new_data' и выходим.
        no_new_data = exc
        logger.info(
            "auto_price_load: %s — нет новых писем (%s)", slug, exc,
        )
    except Exception as exc:  # любая другая ошибка — пишем в БД и Sentry
        error = exc
        logger.exception(
            "auto_price_load: fetcher %s упал — %s: %s",
            slug, type(exc).__name__, exc,
        )
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except Exception:
            pass

    session = SessionLocal()
    try:
        if no_new_data is not None:
            _finish_run_no_new_data(session, run_id, str(no_new_data))
            _set_no_new_data(session, slug)
        elif error is None:
            _finish_run_success(session, run_id, price_upload_id)
            _set_success(session, slug, price_upload_id)
            # 12.1: для IMAP-канала кладём Message-ID в source_ref —
            # это и есть ключ идемпотентности при следующих запусках.
            # У REST-канала (Treolan) этот атрибут отсутствует — пропустим.
            source_ref = getattr(
                fetcher_instance, "last_processed_message_id", None,
            )
            _record_source_ref(session, run_id, source_ref)
        else:
            err_msg = f"{type(error).__name__}: {error}"
            _finish_run_error(session, run_id, err_msg)
            _set_error(session, slug, err_msg)
        session.commit()
    finally:
        session.close()

    if error is not None:
        raise error

    if no_new_data is not None:
        return {
            "supplier_slug":   slug,
            "triggered_by":    triggered_by,
            "run_id":          run_id,
            "price_upload_id": None,
            "status":          "no_new_data",
            "message":         str(no_new_data),
        }

    return {
        "supplier_slug":   slug,
        "triggered_by":    triggered_by,
        "run_id":          run_id,
        "price_upload_id": price_upload_id,
        "status":          "success",
    }
