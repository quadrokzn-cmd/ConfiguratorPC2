# Сервис обработки SOAP-операции Notification «Ресурс Медиа» (spec v7.5 §4.7).
#
# Notification — обязательная к реализации операция: РМ через неё доводит
# до пользователей анонсы (например, об обновлении версии API или о
# планируемой приостановке). Электронная почта в этих случаях не
# используется, поэтому пропустить уведомление = пропустить анонс
# критичного события.
#
# Архитектурное решение (2026-05-12, см. рефлексию мини-этапа):
#   Notification вызывается ВНУТРИ runner'а auto_price_loads для
#   slug='resurs_media' после основного fetch'а — а не отдельным cron-job'ом
#   в portal/scheduler.py. Один раз в сутки (07:40 МСК) достаточно: spec
#   рекомендует «несколько раз», но это рекомендация, не требование;
#   анонсы РМ публикует заранее, окно 24 ч безопасно. Если поймаем кейс
#   пропущенного критического уведомления — увеличим частоту отдельным
#   мини-этапом.
#
# fetch_and_store_notifications(...):
#   - идемпотентный: dedup по NotificationID через ON CONFLICT DO NOTHING;
#   - сохраняет вложения в storage_dir (по умолчанию
#     data/resurs_media_notifications/), не перезаписывает существующие
#     файлы;
#   - НЕ пробрасывает исключения наверх: Notification — вспомогательная
#     операция, её сбой не должен валить основной auto_price_load.
#     Любая ошибка → лог-warning + result['errors'] += 1.

from __future__ import annotations

import base64
import logging
import re
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from portal.services.configurator.auto_price.fetchers.resurs_media import (
    ResursMediaApiFetcher,
    _get,
    _items_in_tab,
    _strip_or_empty,
)


logger = logging.getLogger(__name__)


# Корень репо: parents[4] от этого файла:
#   [0] auto_price → [1] configurator → [2] services → [3] portal → [4] repo root
# Папка data/ уже под .gitignore.
_DEFAULT_STORAGE_DIR = (
    Path(__file__).resolve().parents[4] / "data" / "resurs_media_notifications"
)


# Безопасное имя файла: только [A-Za-z0-9._-]. Защита от path traversal:
# basename only (никаких / или \), запрет на стартовые точки (чтобы
# не получить «../...» или dotfile). Стартовое подчёркивание оставляем
# валидным — оно появляется при замене кириллицы/пробелов на _ и не
# несёт угрозы (это не parent-dir и не скрытый файл на POSIX).
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_UNSAFE_PREFIX_RE = re.compile(r"^\.+")


def _safe_filename(raw: str | None) -> str:
    if not raw:
        return "attachment"
    base = raw.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not base:
        return "attachment"
    safe = _SAFE_FILENAME_RE.sub("_", base)
    safe = _UNSAFE_PREFIX_RE.sub("", safe)
    if not safe:
        return "attachment"
    # Windows-path limit 255 chars; держим запас под префикс safe_id_.
    return safe[:200]


def _decode_attachment(value: Any) -> bytes | None:
    """zeep отдаёт base64Binary либо как bytes (уже декодированный),
    либо как str (base64-encoded), либо None. Нормализуем."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        try:
            return base64.b64decode(value, validate=False)
        except Exception:
            return None
    return None


def fetch_and_store_notifications(
    fetcher: ResursMediaApiFetcher | None = None,
    engine: Engine | None = None,
    storage_dir: Path | None = None,
    from_date: date | None = None,
) -> dict[str, int]:
    """Один тик: вызвать Notification, обработать ответ, сохранить новые
    уведомления и вложения. Возвращает счётчики.

    Параметры:
      fetcher     — если None, создаётся ResursMediaApiFetcher(). В тестах
                    подменяется на мок (чтобы не лезть в SOAP).
      engine      — SQLAlchemy Engine. По умолчанию — shared.db.engine,
                    импортируется лениво (чтобы импорт модуля не падал
                    в окружении без БД).
      storage_dir — куда сохранять вложения. По умолчанию —
                    data/resurs_media_notifications/.
      from_date   — FromDate для SOAP. None → актуальные на момент вызова.

    Returns:
        dict {notifications_seen, new_notifications, attachments_saved, errors}.
    """
    result = {
        "notifications_seen": 0,
        "new_notifications":  0,
        "attachments_saved":  0,
        "errors":             0,
    }

    if engine is None:
        # Импорт здесь — чтобы тесты, мокающие fetcher и engine, могли
        # импортировать этот модуль без живой БД.
        from shared.db import engine as default_engine
        engine = default_engine
    if storage_dir is None:
        storage_dir = _DEFAULT_STORAGE_DIR

    if fetcher is None:
        try:
            fetcher = ResursMediaApiFetcher()
        except Exception as exc:
            logger.warning(
                "resurs_media_notifications: не удалось создать fetcher "
                "(%s: %s) — Notification не получен.",
                type(exc).__name__, exc,
            )
            result["errors"] += 1
            return result

    # SOAP-вызов. Result=3 (rate-limit) уже обрабатывается внутри
    # _call_with_rate_limit (sleep + один retry). На повторном Result=3
    # летит RuntimeError — ловим тут, чтобы Notification-сбой не валил
    # auto_price_load.
    try:
        raw_resp = fetcher.call_notification(from_date=from_date)
    except Exception as exc:
        logger.warning(
            "resurs_media_notifications: Notification-вызов упал "
            "(%s: %s) — БД и storage не меняются.",
            type(exc).__name__, exc,
        )
        result["errors"] += 1
        return result

    # spec v7.5 §4.7: Notification_Resp { Notification = таблица Item-ов,
    # Result, ErrorMessage }. zeep в разных версиях может назвать поле
    # Notification, Notifications или Notification_Tab — берём первый
    # непустой ключ; _items_in_tab распакует и list напрямую, и {Item:[...]}.
    raw_tab = _get(raw_resp, "Notification_Tab", "Notification", "Notifications")
    items = _items_in_tab(raw_tab)
    result["notifications_seen"] = len(items)

    if not items:
        logger.info(
            "resurs_media_notifications: Notification ok, активных "
            "уведомлений нет.",
        )
        return result

    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_dir_resolved = str(storage_dir.resolve())

    for item in items:
        try:
            notification_id = _strip_or_empty(_get(item, "NotificationID"))
            text_value = _strip_or_empty(_get(item, "Text"))
            attachment_name_raw = _strip_or_empty(_get(item, "AttachmentName"))
            attachment_bytes = _decode_attachment(_get(item, "Attachment"))

            if not notification_id:
                logger.warning(
                    "resurs_media_notifications: пропускаю Item без "
                    "NotificationID.",
                )
                result["errors"] += 1
                continue

            attachment_path_value: str | None = None
            if attachment_bytes is not None and attachment_name_raw:
                safe_name = _safe_filename(attachment_name_raw)
                safe_id = _safe_filename(notification_id)
                target = storage_dir / f"{safe_id}_{safe_name}"
                # Двойной щит от path traversal: после _safe_filename
                # всё должно остаться внутри storage_dir, но проверяем
                # ещё и через resolve().
                if not str(target.resolve()).startswith(storage_dir_resolved):
                    logger.warning(
                        "resurs_media_notifications: путь %s выходит за "
                        "пределы storage_dir, пропуск вложения для "
                        "notification_id=%s.",
                        target.resolve(), notification_id,
                    )
                    result["errors"] += 1
                else:
                    if not target.exists():
                        target.write_bytes(attachment_bytes)
                        result["attachments_saved"] += 1
                    # В БД храним basename — storage_dir может отличаться
                    # между dev/preprod/prod, абсолютный путь не нужен.
                    attachment_path_value = target.name

            with engine.begin() as conn:
                row = conn.execute(
                    text(
                        "INSERT INTO resurs_media_notifications "
                        "    (notification_id, text, attachment_name, "
                        "     attachment_path) "
                        "VALUES (:nid, :txt, :att_name, :att_path) "
                        "ON CONFLICT (notification_id) DO NOTHING "
                        "RETURNING id"
                    ),
                    {
                        "nid":      notification_id,
                        "txt":      text_value,
                        "att_name": attachment_name_raw or None,
                        "att_path": attachment_path_value,
                    },
                ).first()
            if row is not None:
                result["new_notifications"] += 1
                logger.info(
                    "resurs_media_notifications: новое уведомление "
                    "id=%s, attachment=%s",
                    notification_id, bool(attachment_bytes),
                )
        except Exception as exc:
            logger.warning(
                "resurs_media_notifications: ошибка обработки Item "
                "(%s: %s) — пропускаю.",
                type(exc).__name__, exc,
            )
            result["errors"] += 1

    logger.info(
        "resurs_media_notifications: seen=%d new=%d attachments=%d errors=%d",
        result["notifications_seen"], result["new_notifications"],
        result["attachments_saved"], result["errors"],
    )
    return result
