# Базовый IMAP-fetcher для автозагрузки прайсов (этап 12.1, fix 12.1).
#
# Подклассы:
#   OCSImapFetcher     (этап 12.1) — XLSX вложение, Subject «B2B OCS …».
#   MerlionImapFetcher (этап 12.1) — ZIP с XLSX, Subject «Прайс-лист MERLION».
#
# Поведение:
#   1. Открывает IMAP-соединение (host/user/pass — из env с fallback
#      на SMTP_USER/SMTP_APP_PASSWORD; VK Workspace выдаёт общий
#      app password).
#   2. LIST всех mailbox-ов на сервере, отбрасывает системные
#      (Trash/Drafts/Sent/Junk/Spam/Outbox + русские эквиваленты,
#      а также любые с флагом \Noselect). Имена раскодируются из
#      modified UTF-7 (RFC 3501) — у пользователя VK Workspace
#      письма от OCS/Merlion ушли в кириллическую папку «Прайсы»,
#      raw-имя &BB8EQAQwBDkEQQRL-.
#   3. По каждой оставшейся папке: SELECT readonly + ASCII-only
#      SEARCH SINCE <окно> (CHARSET=None — VK Workspace плохо
#      переваривает CHARSET UTF-8). Адрес/тема фильтруются на
#      клиенте (Merlion идёт через Gmail-forward — реальный адрес
#      в From/Reply-To/X-Forwarded-For/Return-Path).
#   4. Среди найденных оставляет ТОЛЬКО те, чей Message-ID ещё не
#      лежит в auto_price_load_runs.source_ref за последние 30 дней
#      по этому supplier_slug.
#   5. Берёт самое свежее необработанное письмо, извлекает первое
#      attachment с подходящим расширением (xlsx/zip), валидирует
#      размер и отдаёт bytes в parse_attachment(...) подкласса.
#   6. Если ни одного нового письма нет — бросает NoNewDataException;
#      runner ловит её отдельно и помечает запуск 'no_new_data', НЕ
#      вызывая orchestrator (иначе нулевой rows обнулит остатки).

from __future__ import annotations

import base64
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

from portal.services.configurator.auto_price.base import BaseAutoFetcher
from portal.services.configurator.price_loaders.models import PriceRow
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


def _imap_utf7_decode(s: str) -> str:
    """Декодирует имя папки IMAP (modified UTF-7, RFC 3501) в обычную
    строку. Кириллические папки на VK Workspace приходят в виде
    `&BB8EQAQwBDkEQQRL-` (=«Прайсы») — без декодинга мы не сможем
    отличить их от системных по имени."""
    if not s:
        return s
    res: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "&":
            j = s.find("-", i + 1)
            if j == -1:
                res.append(s[i:])
                break
            chunk = s[i + 1:j]
            if chunk == "":
                res.append("&")
            else:
                b64 = chunk.replace(",", "/")
                b64 += "=" * ((-len(b64)) % 4)
                try:
                    res.append(base64.b64decode(b64).decode("utf-16-be"))
                except Exception:
                    res.append(s[i:j + 1])
            i = j + 1
        else:
            res.append(c)
            i += 1
    return "".join(res)


# Этап 12.5b. Приоритетные папки. Благодаря фильтрам VK Workspace
# OCS- и Merlion-письма падают в кириллическую папку «Прайсы»; INBOX
# держим как резерв на случай, если фильтр не сработает. Если в priority
# нашлось хотя бы одно письмо младше _PRIORITY_RECENT_HOURS — это
# «свежее в ожидаемом месте» и сканировать остальные папки не нужно
# (экономия ~6 минут на каждом fetch против 12.1-fix полного обхода).
_PRIORITY_FOLDER_NAMES = frozenset({"inbox", "прайсы"})
_PRIORITY_RECENT_HOURS = 24


def _is_priority_folder(decoded_name: str) -> bool:
    """True для INBOX, любых INBOX/<sub> и «Прайсы» (decoded UTF-7)."""
    if not decoded_name:
        return False
    name = decoded_name.strip().lower()
    if name in _PRIORITY_FOLDER_NAMES:
        return True
    if name.startswith("inbox/"):
        return True
    return False


# RFC 6154 SPECIAL-USE флаги + классические \Trash и т.п.
_SYSTEM_FOLDER_FLAGS = frozenset({
    "\\noselect", "\\trash", "\\drafts", "\\junk",
    "\\sent", "\\all", "\\flagged", "\\important",
})

# Имена системных папок (lowercase, с учётом локализаций mail.ru / VK
# Workspace и стандартных IMAP-серверов). Сравнение с decoded UTF-7.
# INBOX-вложенные подпапки обрабатываются отдельно (мы их НЕ исключаем).
_SYSTEM_FOLDER_NAMES = frozenset({
    "trash", "drafts", "sent", "junk", "spam", "outbox", "archive",
    "корзина", "удаленные", "удалённые", "черновики",
    "отправленные", "отправлено", "спам", "исходящие",
    "нежелательная почта", "архив",
    # Gmail-style контейнеры (если кто-то когда-нибудь подключит).
    "[gmail]",
})

_LIST_LINE_RE = re.compile(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<mailbox>.+)$')


def _parse_list_line(raw: str) -> tuple[str, str, str] | None:
    """Парсит одну строку ответа IMAP LIST.

    Возвращает (flags_lower, raw_mailbox, decoded_mailbox) или None
    если строка не распознана. raw_mailbox — оригинал в modified UTF-7
    (для SELECT), decoded_mailbox — человекочитаемое имя (для матчинга
    системных папок и логов).
    """
    m = _LIST_LINE_RE.match(raw)
    if not m:
        return None
    flags = (m.group("flags") or "").lower()
    token = m.group("mailbox").strip()
    if token.startswith('"') and token.endswith('"'):
        mailbox = token[1:-1]
    else:
        mailbox = token
    decoded = _imap_utf7_decode(mailbox)
    return (flags, mailbox, decoded)


def _is_system_folder(flags_lower: str, decoded_name: str) -> bool:
    """Возвращает True для папок, которые нужно ИСКЛЮЧИТЬ из обхода:
    Trash/Drafts/Sent/Junk/Spam/Outbox/Archive и их русские
    эквиваленты, плюс всё со флагом \\Noselect (контейнерные узлы)."""
    flag_tokens = {tok.strip() for tok in flags_lower.split() if tok.strip()}
    if flag_tokens & _SYSTEM_FOLDER_FLAGS:
        return True
    name = (decoded_name or "").strip().lower()
    if not name:
        # Пустое имя — не SELECT-абельно, безопаснее пропустить.
        return True
    # Базовое имя (последний сегмент) — на случай Gmail-style "[Gmail]/Trash"
    # или mail.ru-вложений. INBOX/* — это пользовательские подпапки, их
    # мы НЕ должны фильтровать, поэтому проверяем только если базовое
    # имя совпало с системным И это не вложенность INBOX/.
    if name in _SYSTEM_FOLDER_NAMES:
        return True
    # Иерархические разделители — '/' (mail.ru), '.' (cyrus), '\\' (редко).
    last_segment = re.split(r"[/\.\\]", name)[-1].strip()
    if last_segment and last_segment in _SYSTEM_FOLDER_NAMES:
        # Подпапка под системной — тоже мусор (например "Корзина/Архив").
        # Но НЕ INBOX/<что-то>: в INBOX подпапок может быть пользовательский
        # фильтр.
        if not name.startswith("inbox/"):
            return True
    return False


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
                msg, message_id, found_in = (
                    self._find_latest_unprocessed_message(client)
                )
            finally:
                try:
                    client.close()
                except Exception:
                    # close() ругается, если SELECT не было — на пустом
                    # ящике такое возможно. Подавляем, чтобы не маскировать
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

        logger.info(
            "IMAP %s: нашли свежее письмо в папке %r (Message-ID=%s)",
            self.supplier_slug, found_in, message_id,
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
    ) -> tuple[email.message.Message | None, str | None, str | None]:
        """Двухфазный обход IMAP-ящика (этап 12.5b).

        Фаза 1 — приоритетные папки (INBOX, INBOX/*, «Прайсы»). Если в
        них нашлось хотя бы одно подходящее письмо младше 24 ч — это
        «свежее в ожидаемом месте», берём самое свежее из priority,
        фазу 2 пропускаем. Так в типичном дневном прогоне мы выполняем
        2 SELECT-а вместо ~10 (экономия ~6 минут на VK Workspace).

        Фаза 2 — все остальные пользовательские папки (полный обход с
        фильтрацией системных, как в 12.1-fix). Только если фаза 1 не
        дала свежего кандидата. Кандидаты обеих фаз объединяются, выбор
        — самое свежее по Date.

        Возвращает (msg, Message-ID, decoded_folder_name) либо
        (None, None, None). Folder name — для логирования (помогает
        отличить «прилетело по фильтру в Прайсы» от «упало в INBOX»).
        """
        folders = self._list_searchable_folders(client)
        if not folders:
            logger.info(
                "IMAP %s: после фильтрации не осталось папок для обхода",
                self.supplier_slug,
            )
            return (None, None, None)

        priority_folders = [f for f in folders if _is_priority_folder(f[2])]
        other_folders = [f for f in folders if not _is_priority_folder(f[2])]
        logger.info(
            "IMAP %s: всего %d папок; priority=%d (%s); прочие=%d (%s)",
            self.supplier_slug, len(folders),
            len(priority_folders),
            ", ".join(repr(d) for _, _, d in priority_folders) or "(нет)",
            len(other_folders),
            ", ".join(repr(d) for _, _, d in other_folders) or "(нет)",
        )

        since_dt = datetime.now(timezone.utc) - timedelta(days=self.search_window_days)
        since_str = since_dt.strftime("%d-%b-%Y")

        # Список «уже обработанных» Message-ID за окно
        # idempotency_window_days — кэшируем один раз на весь обход.
        processed_ids = self._load_processed_message_ids()

        # Дедуп между папками (одно письмо может быть в нескольких через
        # label-style копии) — общий set на обе фазы.
        seen_msg_ids: set[str] = set()

        # ----- Фаза 1: priority -----------------------------------------
        phase1_candidates = self._scan_folders_for_candidates(
            client, priority_folders, since_str, processed_ids, seen_msg_ids,
        )
        recent_threshold = datetime.now(timezone.utc) - timedelta(hours=_PRIORITY_RECENT_HOURS)
        has_recent_priority = any(c[0] >= recent_threshold for c in phase1_candidates)

        if has_recent_priority:
            phase1_candidates.sort(key=lambda x: x[0], reverse=True)
            best_dt, best_msg, best_msg_id, best_folder = phase1_candidates[0]
            logger.info(
                "IMAP %s: фаза 1 (priority) дала результат — кандидатов %d, "
                "выбран %r из %r (Date=%s, моложе %dч); фазу 2 пропускаем",
                self.supplier_slug, len(phase1_candidates),
                best_msg_id, best_folder, best_dt.isoformat(),
                _PRIORITY_RECENT_HOURS,
            )
            return (best_msg, best_msg_id, best_folder)

        logger.info(
            "IMAP %s: фаза 1 — кандидатов %d, свежих (<%dч) нет; "
            "запускаю фазу 2 по %d прочим папкам",
            self.supplier_slug, len(phase1_candidates),
            _PRIORITY_RECENT_HOURS, len(other_folders),
        )

        # ----- Фаза 2: остальные папки ----------------------------------
        phase2_candidates = self._scan_folders_for_candidates(
            client, other_folders, since_str, processed_ids, seen_msg_ids,
        )

        all_candidates = phase1_candidates + phase2_candidates
        if not all_candidates:
            logger.info(
                "IMAP %s: ни в priority, ни в остальных папках кандидатов нет",
                self.supplier_slug,
            )
            return (None, None, None)

        all_candidates.sort(key=lambda x: x[0], reverse=True)
        best_dt, best_msg, best_msg_id, best_folder = all_candidates[0]
        winning_phase = "1 (priority)" if _is_priority_folder(best_folder) else "2 (fallback)"
        logger.info(
            "IMAP %s: фаза 2 завершена; всего кандидатов %d (priority=%d, fallback=%d); "
            "выбран %r из %r (Date=%s, фаза %s)",
            self.supplier_slug, len(all_candidates),
            len(phase1_candidates), len(phase2_candidates),
            best_msg_id, best_folder, best_dt.isoformat(), winning_phase,
        )
        return (best_msg, best_msg_id, best_folder)

    def _scan_folders_for_candidates(
        self,
        client: imaplib.IMAP4,
        folders: list[tuple[str, str, str]],
        since_str: str,
        processed_ids: set[str],
        seen_msg_ids: set[str],
    ) -> list[tuple[datetime, email.message.Message, str, str]]:
        """Сканирует указанные папки, возвращает (date, msg, msg_id, decoded_folder).

        seen_msg_ids — мутируемый общий set для дедупа между вызовами
        (одно письмо может быть в нескольких папках через label-style копии).
        """
        candidates: list[tuple[datetime, email.message.Message, str, str]] = []
        for _flags, raw_name, decoded_name in folders:
            # SELECT с raw-именем (modified UTF-7 для кириллицы); imaplib
            # требует кавычки вокруг имён с пробелами/спецсимволами.
            try:
                typ, _ = client.select(f'"{raw_name}"', readonly=True)
            except Exception as exc:
                logger.warning(
                    "IMAP %s: SELECT %r упал (%s: %s) — пропуск",
                    self.supplier_slug, decoded_name,
                    type(exc).__name__, exc,
                )
                continue
            if typ != "OK":
                logger.warning(
                    "IMAP %s: SELECT %r вернул %s — пропуск",
                    self.supplier_slug, decoded_name, typ,
                )
                continue

            # ASCII-only SEARCH (CHARSET=None): у VK Workspace IMAP
            # CHARSET UTF-8 на SEARCH иногда отвечает пустотой. От
            # адреса/темы фильтруем уже на клиенте, т.к. From/Reply-To
            # могут отличаться (Merlion идёт через Gmail-forward).
            try:
                typ, data = client.search(None, "SINCE", since_str)
            except Exception as exc:
                logger.warning(
                    "IMAP %s: SEARCH в %r упал (%s: %s) — пропуск",
                    self.supplier_slug, decoded_name,
                    type(exc).__name__, exc,
                )
                continue
            if typ != "OK" or not data or not data[0]:
                continue
            uids = data[0].split()
            if not uids:
                continue

            # Идём от свежих к старым — минимизирует FETCH-ы при удаче.
            # Но всё равно собираем всё, чтобы сравнить с другими папками.
            for uid in reversed(uids):
                raw_msg = self._fetch_rfc822(client, uid)
                if raw_msg is None:
                    continue
                msg = email.message_from_bytes(raw_msg)
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
                    logger.warning(
                        "IMAP %s: письмо без Message-ID в %r (Subject=%r), пропуск.",
                        self.supplier_slug, decoded_name, subject,
                    )
                    continue
                if msg_id in processed_ids:
                    continue
                if msg_id in seen_msg_ids:
                    continue
                seen_msg_ids.add(msg_id)
                try:
                    date_dt = email.utils.parsedate_to_datetime(msg.get("Date") or "")
                except Exception:
                    date_dt = datetime.now(tz=timezone.utc)
                if date_dt is None:
                    date_dt = datetime.now(tz=timezone.utc)
                if date_dt.tzinfo is None:
                    date_dt = date_dt.replace(tzinfo=timezone.utc)
                candidates.append((date_dt, msg, msg_id, decoded_name))
        return candidates

    # ----- LIST: пользовательские папки -----------------------------------

    def _list_searchable_folders(
        self,
        client: imaplib.IMAP4,
    ) -> list[tuple[str, str, str]]:
        """LIST ящика и фильтрация системных папок.

        Возвращает список (flags_lower, raw_name, decoded_name) папок,
        которые нужно обойти. INBOX всегда в списке (если сервер его
        возвращает). Системные (Trash/Drafts/Sent/Junk/Spam/Outbox/Archive
        и их русские эквиваленты + флаги \\Noselect/\\Trash/\\Drafts/...)
        исключаются.

        Если LIST почему-то ничего не вернул — fallback на одну папку
        INBOX (это поведение до 12.1-fix).
        """
        try:
            typ, data = client.list()
        except Exception as exc:
            logger.warning(
                "IMAP %s: LIST упал (%s: %s) — fallback на INBOX",
                self.supplier_slug, type(exc).__name__, exc,
            )
            return [("", "INBOX", "INBOX")]
        if typ != "OK" or not data:
            logger.warning(
                "IMAP %s: LIST вернул %s — fallback на INBOX",
                self.supplier_slug, typ,
            )
            return [("", "INBOX", "INBOX")]

        out: list[tuple[str, str, str]] = []
        for raw in data:
            if raw is None:
                continue
            if isinstance(raw, bytes):
                line = raw.decode("ascii", errors="replace")
            else:
                line = str(raw)
            parsed = _parse_list_line(line)
            if parsed is None:
                logger.debug(
                    "IMAP %s: не распознал строку LIST: %r",
                    self.supplier_slug, line,
                )
                continue
            flags, raw_name, decoded_name = parsed
            if _is_system_folder(flags, decoded_name):
                logger.debug(
                    "IMAP %s: пропускаю системную папку %r (flags=%s)",
                    self.supplier_slug, decoded_name, flags,
                )
                continue
            out.append(parsed)
        if not out:
            logger.warning(
                "IMAP %s: после фильтрации не осталось папок — "
                "fallback на INBOX", self.supplier_slug,
            )
            return [("", "INBOX", "INBOX")]
        return out

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
        from portal.services.configurator.price_loaders.orchestrator import save_price_rows

        result = save_price_rows(
            supplier_name=self.supplier_display_name,
            source=virtual_filename,
            rows=rows,
        )
        return int(result["upload_id"])
