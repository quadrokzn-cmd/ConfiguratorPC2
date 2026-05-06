# Базовый IMAP-fetcher для автозагрузки прайсов (этап 12.1).
#
# Подклассы:
#   OCSImapFetcher     (этап 12.1) — XLSX вложение, Subject «B2B OCS …».
#   MerlionImapFetcher (этап 12.1) — ZIP с XLSX, Subject «Прайс-лист MERLION».
#
# Поведение:
#   1. Открывает IMAP-соединение к INBOX (host/user/pass — из env с
#      fallback на SMTP_USER/SMTP_APP_PASSWORD; VK Workspace выдаёт общий
#      app password).
#   2. Ищет письма за последние search_window_days (=14) от sender_pattern
#      и с темой, подходящей под subject_pattern.
#   3. Среди найденных оставляет ТОЛЬКО те, чей Message-ID ещё не лежит в
#      auto_price_load_runs.source_ref за последние 30 дней по этому
#      supplier_slug. Это защита от повторной обработки одного и того же
#      письма при ручном запуске сразу после планового.
#   4. Берёт самое свежее необработанное письмо, извлекает первое
#      attachment с подходящим расширением (xlsx/zip), валидирует размер
#      и отдаёт bytes в parse_attachment(...) подкласса.
#   5. Подкласс возвращает List[PriceRow]; общий save_price_rows() из
#      orchestrator делает остальное (UPSERT supplier_prices, mapping,
#      unmapped, disappeared, запись price_uploads).
#   6. Если ни одного нового письма нет — бросает NoNewDataException;
#      runner ловит её отдельно и помечает запуск 'no_new_data', НЕ
#      вызывая orchestrator (это критично — иначе без нового прайса
#      orchestrator получит пустой rows и обнулит остатки).

from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import logging
import os
import re
from abc import abstractmethod
from datetime import date, datetime, timedelta, timezone
from typing import Tuple

from sqlalchemy import text

from app.services.auto_price.base import BaseAutoFetcher
from app.services.price_loaders.models import PriceRow
from shared.db import SessionLocal


logger = logging.getLogger(__name__)


# =====================================================================
# Public exception
# =====================================================================

class NoNewDataException(Exception):
    """Поднимается, когда fetcher не нашёл ни одного нового письма для
    обработки в окне search_window_days. Runner должен поймать её и
    пометить запуск 'no_new_data' — это НЕ ошибка, и orchestrator
    вызывать НЕ нужно (иначе нулевой rows обнулит остатки)."""


# =====================================================================
# IMAP credentials helper
# =====================================================================

def _read_imap_credentials() -> tuple[str, int, bool, str, str]:
    """Читает IMAP-параметры из env с fallback на SMTP_*.

    Возвращает (host, port, use_ssl, user, password). На VK Workspace
    SMTP и IMAP используют общий app-password — отдельные ENV не нужны,
    fallback покрывает 99% случаев. Если ни IMAP_USER/PASSWORD ни SMTP_*
    не заданы — RuntimeError с понятным списком переменных.
    """
    host = (os.environ.get("IMAP_HOST") or "imap.mail.ru").strip()
    port_raw = (os.environ.get("IMAP_PORT") or "993").strip()
    try:
        port = int(port_raw)
    except ValueError:
        port = 993
    use_ssl_raw = (os.environ.get("IMAP_USE_SSL") or "true").strip().lower()
    use_ssl = use_ssl_raw in ("1", "true", "yes", "on")

    user = (
        os.environ.get("IMAP_USER")
        or os.environ.get("SMTP_USER")
        or ""
    ).strip()
    password = (
        os.environ.get("IMAP_PASSWORD")
        or os.environ.get("SMTP_APP_PASSWORD")
        or ""
    ).strip()
    if not user or not password:
        raise RuntimeError(
            "IMAP-канал автозагрузки: не заданы креды. Ожидаются "
            "переменные окружения IMAP_USER, IMAP_PASSWORD (либо их "
            "fallback SMTP_USER, SMTP_APP_PASSWORD — VK Workspace "
            "выдаёт общий app-password для SMTP и IMAP)."
        )
    return host, port, use_ssl, user, password


# =====================================================================
# Helpers
# =====================================================================

def _decode_header_value(raw: str | None) -> str:
    """Раскодирует RFC 2047 заголовок (`=?utf-8?B?...?=`)."""
    if not raw:
        return ""
    try:
        parts = email.header.decode_header(raw)
    except Exception:
        return raw
    out: list[str] = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            try:
                out.append(chunk.decode(enc or "utf-8", errors="replace"))
            except (LookupError, UnicodeDecodeError):
                out.append(chunk.decode("utf-8", errors="replace"))
        else:
            out.append(chunk)
    return "".join(out)


def _addresses_in_header(headers: dict[str, str]) -> str:
    """Слепляет все адресные/forwarded заголовки в одну строку для
    regex-поиска по sender_pattern. Merlion прилетает через Gmail-forward,
    поэтому реальный адрес может оказаться в Reply-To / X-Forwarded-For /
    Return-Path — проверяем все."""
    return " ".join([
        headers.get("From", ""),
        headers.get("Reply-To", ""),
        headers.get("X-Forwarded-For", ""),
        headers.get("Return-Path", ""),
        headers.get("Sender", ""),
    ])


# =====================================================================
# BaseImapFetcher
# =====================================================================

class BaseImapFetcher(BaseAutoFetcher):
    """Общий каркас IMAP-канала. См. модульный docstring."""

    # Подкласс задаёт.
    sender_pattern: str = ""        # regex по адресным заголовкам
    subject_pattern: str = ""       # regex по Subject (decoded)
    attachment_extensions: tuple[str, ...] = ()  # ('.xlsx',) | ('.zip',)
    search_window_days: int = 14
    max_attachment_size_mb: int = 50
    # 30 дней — окно идемпотентности по Message-ID (см. _is_already_processed).
    idempotency_window_days: int = 30

    # Заполняется fetch_and_save() для runner-а: после успешной обработки
    # runner возьмёт отсюда Message-ID и положит в source_ref.
    last_processed_message_id: str | None = None

    def __init__(self) -> None:
        if not self.supplier_slug:
            raise RuntimeError(
                f"{type(self).__name__}: supplier_slug должен быть задан."
            )
        if not self.sender_pattern or not self.subject_pattern:
            raise RuntimeError(
                f"{type(self).__name__}: sender_pattern и subject_pattern "
                "должны быть заданы в подклассе."
            )
        if not self.attachment_extensions:
            raise RuntimeError(
                f"{type(self).__name__}: attachment_extensions должно "
                "содержать хотя бы одно расширение (например, ('.xlsx',))."
            )
        # Проверяем креды на этапе __init__ — это даст понятную ошибку
        # ещё до открытия соединения. Состояние не сохраняем.
        _read_imap_credentials()

        self._sender_re = re.compile(self.sender_pattern, re.IGNORECASE)
        self._subject_re = re.compile(self.subject_pattern, re.IGNORECASE)

    # ----- main entrypoint -------------------------------------------------

    def fetch_and_save(self) -> int:
        """Основной поток. См. модульный docstring."""
        host, port, use_ssl, user, password = _read_imap_credentials()
        logger.info(
            "IMAP %s: %s:%d ssl=%s user=%s, окно %dд",
            self.supplier_slug, host, port, use_ssl, user,
            self.search_window_days,
        )
        if use_ssl:
            client = imaplib.IMAP4_SSL(host, port)
        else:
            client = imaplib.IMAP4(host, port)
        try:
            client.login(user, password)
            try:
                msg, message_id = self._find_latest_unprocessed_message(client)
            finally:
                try:
                    client.close()
                except Exception:
                    # close() ругается, если SELECT не было — на Empty INBOX
                    # такое возможно. Подавляем, чтобы не маскировать
                    # реальную ошибку выше.
                    pass
        finally:
            try:
                client.logout()
            except Exception:
                pass

        if msg is None:
            raise NoNewDataException(
                f"IMAP {self.supplier_slug}: нет новых писем за последние "
                f"{self.search_window_days} дней (окно идемпотентности — "
                f"{self.idempotency_window_days} дней)."
            )

        attachment_bytes, attachment_filename = self._extract_attachment(msg)
        rows = list(self.parse_attachment(attachment_bytes, attachment_filename))
        price_upload_id = self._save_via_orchestrator(rows, attachment_filename)

        # Runner потом запишет это в auto_price_load_runs.source_ref.
        self.last_processed_message_id = message_id
        return price_upload_id

    # ----- supplier-specific parsing --------------------------------------

    @abstractmethod
    def parse_attachment(self, data: bytes, filename: str) -> list[PriceRow]:
        """Подкласс возвращает List[PriceRow] из bytes XLSX/ZIP-вложения.
        filename — оригинальное имя из письма (для логов/имени upload-а).
        """

    # Подкласс ОБЯЗАН переопределить — это имя в suppliers.name (не slug).
    supplier_display_name: str = ""

    # ----- IMAP search ----------------------------------------------------

    def _find_latest_unprocessed_message(
        self,
        client: imaplib.IMAP4,
    ) -> tuple[email.message.Message | None, str | None]:
        """Открывает INBOX, ищет письма за окно search_window_days от
        sender_pattern, фильтрует по subject_pattern и идемпотентности.
        Возвращает (msg, Message-ID) самого свежего необработанного письма
        либо (None, None)."""
        typ, _ = client.select("INBOX", readonly=True)
        if typ != "OK":
            raise RuntimeError(
                f"IMAP {self.supplier_slug}: не удалось открыть INBOX (typ={typ})."
            )

        since_dt = datetime.now(timezone.utc) - timedelta(days=self.search_window_days)
        since_str = since_dt.strftime("%d-%b-%Y")

        # ASCII-only серверный фильтр по дате — у VK Workspace IMAP плохо
        # работают CHARSET и кириллица в SEARCH. От адреса/темы фильтруем
        # уже на клиенте, т.к. From/Reply-To могут отличаться (Merlion
        # пересылается через Gmail).
        typ, data = client.search(None, "SINCE", since_str)
        if typ != "OK" or not data or not data[0]:
            return (None, None)
        uids = data[0].split()
        if not uids:
            return (None, None)

        # Список «уже обработанных» Message-ID за окно idempotency_window_days.
        processed_ids = self._load_processed_message_ids()

        # Идём от самых свежих к старым — UID растёт в порядке прихода в
        # папку, поэтому reverse-обход даёт примерно свежее→старое. Этого
        # достаточно: первое подходящее письмо и берём.
        candidates: list[tuple[datetime, bytes, email.message.Message, str]] = []
        for uid in reversed(uids):
            raw = self._fetch_rfc822(client, uid)
            if raw is None:
                continue
            msg = email.message_from_bytes(raw)
            subject = _decode_header_value(msg.get("Subject"))
            headers = {
                "From":            _decode_header_value(msg.get("From")),
                "Reply-To":        _decode_header_value(msg.get("Reply-To")),
                "X-Forwarded-For": _decode_header_value(msg.get("X-Forwarded-For")),
                "Return-Path":     _decode_header_value(msg.get("Return-Path")),
                "Sender":          _decode_header_value(msg.get("Sender")),
            }
            haystack = _addresses_in_header(headers)
            if not self._sender_re.search(haystack):
                continue
            if not self._subject_re.search(subject or ""):
                continue
            msg_id = (msg.get("Message-ID") or "").strip()
            if not msg_id:
                # Без Message-ID нельзя гарантировать идемпотентность —
                # пропустим, чтобы не зацикливаться на одном письме.
                logger.warning(
                    "IMAP %s: письмо без Message-ID (Subject=%r), пропуск.",
                    self.supplier_slug, subject,
                )
                continue
            if msg_id in processed_ids:
                continue
            try:
                date_dt = email.utils.parsedate_to_datetime(msg.get("Date") or "")
            except Exception:
                date_dt = datetime.now(tz=timezone.utc)
            if date_dt is None:
                date_dt = datetime.now(tz=timezone.utc)
            if date_dt.tzinfo is None:
                date_dt = date_dt.replace(tzinfo=timezone.utc)
            candidates.append((date_dt, uid, msg, msg_id))

        if not candidates:
            return (None, None)

        # Самое свежее по Date.
        candidates.sort(key=lambda x: x[0], reverse=True)
        _, _, best_msg, best_msg_id = candidates[0]
        return (best_msg, best_msg_id)

    @staticmethod
    def _fetch_rfc822(client: imaplib.IMAP4, uid: bytes) -> bytes | None:
        try:
            typ, fetched = client.fetch(uid, "(RFC822)")
        except Exception as exc:
            logger.warning(
                "IMAP fetch %r: %s: %s", uid, type(exc).__name__, exc,
            )
            return None
        if typ != "OK" or not fetched:
            return None
        for part in fetched:
            if isinstance(part, tuple) and len(part) >= 2:
                return part[1]
        return None

    # ----- идемпотентность через auto_price_load_runs.source_ref ----------

    def _load_processed_message_ids(self) -> set[str]:
        """Возвращает множество Message-ID, которые уже успешно
        обработаны для этого supplier_slug за idempotency_window_days
        дней. Считаем «обработанным» любой run с непустым source_ref —
        включая 'success' и 'error' статусы. Если что-то упало после
        обработки письма — лучше не повторять, иначе можно дважды
        обнулить disappeared SKU при кривом xlsx."""
        session = SessionLocal()
        try:
            rows = session.execute(
                text(
                    "SELECT source_ref FROM auto_price_load_runs "
                    "WHERE supplier_slug = :slug "
                    "  AND source_ref IS NOT NULL "
                    "  AND started_at > NOW() - make_interval(days => :d)"
                ),
                {"slug": self.supplier_slug, "d": self.idempotency_window_days},
            ).all()
        finally:
            session.close()
        return {r.source_ref for r in rows if r.source_ref}

    # ----- attachment extraction ------------------------------------------

    def _extract_attachment(
        self, msg: email.message.Message,
    ) -> Tuple[bytes, str]:
        """Перебирает части письма, ищет attachment с расширением из
        attachment_extensions. Если несколько — берёт первое. Проверяет
        размер ≤ max_attachment_size_mb."""
        max_bytes = self.max_attachment_size_mb * 1024 * 1024

        for part in msg.walk():
            if part.is_multipart():
                continue
            filename = part.get_filename()
            if filename:
                filename = _decode_header_value(filename)
            if not filename:
                continue
            low = filename.lower()
            if not any(low.endswith(ext) for ext in self.attachment_extensions):
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            if len(payload) > max_bytes:
                raise RuntimeError(
                    f"IMAP {self.supplier_slug}: вложение «{filename}» "
                    f"({len(payload)} байт) превышает лимит "
                    f"{self.max_attachment_size_mb} МБ."
                )
            return (payload, filename)

        raise RuntimeError(
            f"IMAP {self.supplier_slug}: в найденном письме нет вложения с "
            f"расширением из {self.attachment_extensions}."
        )

    # ----- save через orchestrator ----------------------------------------

    def _save_via_orchestrator(
        self, rows: list[PriceRow], source_filename: str,
    ) -> int:
        """Зовёт общий save_price_rows() (тот же путь, что и Treolan и
        ручные /admin/price-uploads). Возвращает price_uploads.id."""
        if not self.supplier_display_name:
            raise RuntimeError(
                f"{type(self).__name__}: supplier_display_name должен быть "
                "задан в подклассе (это имя в suppliers.name)."
            )
        # filename для price_uploads: префикс auto_<slug>_imap_<дата>_…
        # — UI журнала отличит «откуда» (как у Treolan).
        date_str = date.today().isoformat()
        virtual_filename = f"auto_{self.supplier_slug}_imap_{date_str}_{source_filename}"

        # Импорт локальный — orchestrator тянет много тяжёлого, не нужно
        # поднимать его при чистом импорте fetcher-модуля.
        from app.services.price_loaders.orchestrator import save_price_rows

        result = save_price_rows(
            supplier_name=self.supplier_display_name,
            source=virtual_filename,
            rows=rows,
        )
        return int(result["upload_id"])
