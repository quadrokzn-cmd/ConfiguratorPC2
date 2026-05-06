"""[scratch] Разведка IMAP-ящика quadro@quadro.tatar перед этапом 12.1.

Read-only:
  - перечисляет все папки IMAP-ящика
  - в папках, чьё имя содержит «прайс»/«price»/«inbox»,
    обходит письма за последние 30 дней и фильтрует те, что
    похожи на прайсы OCS / Merlion (по From / Reply-To /
    X-Forwarded-For / Subject)
  - для каждого подходящего письма печатает заголовки и список
    вложений
  - сохраняет первое свежее xlsx-вложение от каждого поставщика
    в /tmp/inspect_imap/<supplier>/...
  - выдаёт сводку по паттернам

Учётные данные читаются ИСКЛЮЧИТЕЛЬНО из env (IMAP_USER /
IMAP_PASSWORD; fallback — SMTP_USER / SMTP_APP_PASSWORD, т.к. на
VK Workspace appassword обычно общий). В БД ничего не пишет.
В git коммитим как scratch для воспроизводимости разведки.

Запуск:
  railway run --service <с IMAP creds> python scripts/_diag_imap_inbox.py
  ИЛИ локально с .env, в котором заполнены IMAP_USER/IMAP_PASSWORD.
"""
from __future__ import annotations

import base64
import collections
import email
import email.header
import email.utils
import imaplib
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Принудительно UTF-8 для stdout: на Windows консоль по умолчанию cp1251
# и не может вывести стрелки/эмодзи/часть кириллицы.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
except Exception:
    pass

# --- Загрузка .env (если есть) ----------------------------------------------
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass


# --- ENV --------------------------------------------------------------------
IMAP_HOST = os.environ.get("IMAP_HOST") or "imap.mail.ru"
IMAP_PORT = int(os.environ.get("IMAP_PORT") or "993")
IMAP_USE_SSL = (os.environ.get("IMAP_USE_SSL") or "true").lower() in ("1", "true", "yes")
IMAP_USER = (
    os.environ.get("IMAP_USER")
    or os.environ.get("SMTP_USER")
    or ""
).strip()
IMAP_PASSWORD = (
    os.environ.get("IMAP_PASSWORD")
    or os.environ.get("SMTP_APP_PASSWORD")
    or ""
).strip()

DAYS_BACK = 30
_SAVE_DIR_RAW = os.environ.get("DIAG_IMAP_SAVE_DIR") or "/tmp/inspect_imap"
if os.name == "nt" and _SAVE_DIR_RAW.startswith("/tmp/"):
    SAVE_DIR = Path("D:/tmp/inspect_imap")
else:
    SAVE_DIR = Path(_SAVE_DIR_RAW)

SUPPLIER_PATTERNS = {
    "OCS":     [r"ocs\.ru", r"\bocs\b", r"\bосс\b"],
    "Merlion": [r"merlion\.ru", r"\bmerlion\b", r"\bмерлион\b"],
}
GENERIC_PRICE_RE = re.compile(r"прайс|price|каталог|catalog", re.IGNORECASE)
FOLDER_HINT_RE = re.compile(r"прайс|price|inbox", re.IGNORECASE)


# --- IMAP modified UTF-7 (RFC 3501) -----------------------------------------
def imap_utf7_decode(s: str) -> str:
    """Декодирует имя папки IMAP (modified UTF-7) в обычную строку."""
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


def decode_header_value(raw: str | None) -> str:
    if not raw:
        return ""
    parts = email.header.decode_header(raw)
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


def detect_supplier(headers: dict[str, str], subject: str) -> str | None:
    haystack = " ".join(
        [
            headers.get("From", ""),
            headers.get("Reply-To", ""),
            headers.get("X-Forwarded-For", ""),
            headers.get("Delivered-To", ""),
            headers.get("Return-Path", ""),
            subject,
        ]
    ).lower()
    for name, patterns in SUPPLIER_PATTERNS.items():
        for p in patterns:
            if re.search(p, haystack):
                return name
    return None


def parse_attachments(msg: email.message.Message) -> list[dict]:
    out: list[dict] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        disp = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if filename:
            filename = decode_header_value(filename)
        if not filename and "attachment" not in disp:
            continue
        if not filename:
            continue
        payload = part.get_payload(decode=True) or b""
        out.append(
            {
                "filename": filename,
                "mime": part.get_content_type(),
                "size": len(payload),
                "payload": payload,
            }
        )
    return out


# --- Main -------------------------------------------------------------------
def main() -> int:
    if not IMAP_USER or not IMAP_PASSWORD:
        print(
            "ERR: IMAP_USER/IMAP_PASSWORD (или SMTP_USER/SMTP_APP_PASSWORD)"
            " не заданы в env. Скрипт не может подключиться.",
            file=sys.stderr,
        )
        return 2

    print(f"[diag] IMAP: {IMAP_HOST}:{IMAP_PORT} SSL={IMAP_USE_SSL} user={IMAP_USER}")
    print(f"[diag] Окно: последние {DAYS_BACK} дней")
    print(f"[diag] Сохраняю xlsx в: {SAVE_DIR}")
    print()

    if IMAP_USE_SSL:
        M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    else:
        M = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)

    try:
        M.login(IMAP_USER, IMAP_PASSWORD)
    except imaplib.IMAP4.error as exc:
        print(f"ERR: IMAP LOGIN failed: {exc}", file=sys.stderr)
        return 3

    try:
        # ---- 1. LIST folders ------------------------------------------------
        typ, data = M.list()
        if typ != "OK":
            print(f"ERR: LIST failed: {typ}", file=sys.stderr)
            return 4
        print("=" * 70)
        print(f"СПИСОК ПАПОК ({len(data)})")
        print("=" * 70)
        folders: list[tuple[str, str]] = []  # (raw_quoted, decoded)
        for raw in data:
            if isinstance(raw, bytes):
                raw_s = raw.decode("ascii", errors="replace")
            else:
                raw_s = str(raw)
            # Формат: (\HasNoChildren) "/" "ИМЯ"
            m = re.match(r'\(.*?\)\s+(?:"[^"]*"|NIL)\s+(.+)$', raw_s)
            if not m:
                print(f"  [skip-parse] {raw_s}")
                continue
            mailbox_token = m.group(1).strip()
            if mailbox_token.startswith('"') and mailbox_token.endswith('"'):
                mailbox = mailbox_token[1:-1]
            else:
                mailbox = mailbox_token
            decoded = imap_utf7_decode(mailbox)
            folders.append((mailbox, decoded))

            # размер папки (count)
            count = "?"
            try:
                typ2, num = M.select(f'"{mailbox}"', readonly=True)
                if typ2 == "OK" and num and num[0]:
                    count = num[0].decode("ascii") if isinstance(num[0], bytes) else str(num[0])
            except Exception as exc:
                count = f"ERR:{exc}"
            finally:
                try:
                    M.close()
                except Exception:
                    pass
            print(f"  {decoded!s:40s} | raw={mailbox!s:40s} | {count} писем")

        print()

        # ---- 2. Поиск по подходящим папкам ---------------------------------
        target_folders = [(raw, dec) for raw, dec in folders if FOLDER_HINT_RE.search(dec) or FOLDER_HINT_RE.search(raw)]
        if not target_folders:
            print("[!] Не нашёл папок с 'прайс'/'price'/'inbox'. Пробую INBOX как fallback.")
            target_folders = [(raw, dec) for raw, dec in folders if dec.upper() == "INBOX" or raw.upper() == "INBOX"]

        # Приоритет — папки с "прайс"/"price" в имени (там скорее всего лежат
        # настоящие прайсы), затем INBOX и его подпапки. Так первое xlsx-
        # сохранение от поставщика возьмётся именно из прайс-папки.
        def _folder_priority(item: tuple[str, str]) -> tuple[int, str]:
            raw, dec = item
            haystack = (raw + " " + dec).lower()
            if "прайс" in haystack or "price" in haystack:
                score = 0
            elif haystack.strip().lower() == "inbox":
                score = 1
            else:
                score = 2
            return (score, dec.lower())
        target_folders.sort(key=_folder_priority)

        print("=" * 70)
        print(f"ОБХОД ПАПОК ({len(target_folders)})")
        print("=" * 70)
        for raw, dec in target_folders:
            print(f"  ->{dec} (raw={raw})")
        print()

        since_dt = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
        since_str = since_dt.strftime("%d-%b-%Y")

        matched_messages: list[dict] = []  # для сводки
        saved_per_supplier: set[str] = set()
        seen_msg_ids: set[str] = set()

        # Серверные фильтры (ASCII-only, чтобы избежать проблем с CHARSET
        # на VK Workspace IMAP). Резко сокращают поток — не нужно тянуть
        # RFC822 для всех 885 писем INBOX, только для интересных.
        SERVER_SEARCHES: list[tuple[str, list[str]]] = [
            ("OCS-from",     ["FROM", "ocs.ru"]),
            ("Merlion-from", ["FROM", "merlion.ru"]),
        ]
        # Для папок с "прайс"/"price" в имени — дополнительно берём sample
        # из первых N писем за окно (без серверного фильтра), чтобы понять
        # паттерны прайс-рассылок других поставщиков (для будущих этапов).
        PRICE_FOLDER_SAMPLE_LIMIT = 30

        def reconnect() -> imaplib.IMAP4:
            nonlocal M
            try:
                M.logout()
            except Exception:
                pass
            if IMAP_USE_SSL:
                M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
            else:
                M = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
            M.login(IMAP_USER, IMAP_PASSWORD)
            return M

        def fetch_one(num: bytes, retries: int = 2) -> bytes | None:
            """FETCH RFC822 с reconnect при разрыве."""
            for attempt in range(retries + 1):
                try:
                    typ, fetched = M.fetch(num, "(RFC822)")
                    if typ != "OK" or not fetched:
                        return None
                    for part in fetched:
                        if isinstance(part, tuple) and len(part) >= 2:
                            return part[1]
                    return None
                except (imaplib.IMAP4.abort, imaplib.IMAP4.error,
                        ConnectionError, OSError) as exc:
                    if attempt >= retries:
                        print(f"  [warn] FETCH {num!r} failed после {retries} попыток: {exc}")
                        return None
                    print(f"  [warn] FETCH {num!r} прервался ({exc}), reconnect...")
                    try:
                        reconnect()
                    except Exception as exc2:
                        print(f"  [warn] reconnect failed: {exc2}")
                        return None
                    # после reconnect нужно заново SELECT — это сделает caller
                    return None

        def select_folder(raw: str) -> bool:
            try:
                typ, _ = M.select(f'"{raw}"', readonly=True)
                return typ == "OK"
            except (imaplib.IMAP4.abort, ConnectionError, OSError):
                try:
                    reconnect()
                    typ, _ = M.select(f'"{raw}"', readonly=True)
                    return typ == "OK"
                except Exception:
                    return False

        for raw, dec in target_folders:
            print("-" * 70)
            print(f"ПАПКА: {dec}  (raw={raw})")
            print("-" * 70)
            if not select_folder(raw):
                print("  [skip] не удалось SELECT")
                continue

            # Общий счёт для контекста
            try:
                typ, data = M.search(None, "SINCE", since_str)
                total_in_window = len(data[0].split()) if (typ == "OK" and data and data[0]) else 0
            except Exception:
                total_in_window = -1
            print(f"  Всего писем за {DAYS_BACK} дней: {total_in_window}")

            # Объединение UID-ов из серверных фильтров
            folder_uids: dict[bytes, str] = {}  # uid -> название первого попавшего фильтра
            for label, search_args in SERVER_SEARCHES:
                try:
                    typ, data = M.search(None, "SINCE", since_str, *search_args)
                except (imaplib.IMAP4.abort, ConnectionError, OSError) as exc:
                    print(f"  [warn] SEARCH {label} прервался ({exc}), reconnect и пропуск")
                    try:
                        reconnect()
                        select_folder(raw)
                    except Exception:
                        pass
                    continue
                if typ != "OK" or not data or not data[0]:
                    print(f"  SEARCH {label}: 0")
                    continue
                uids = data[0].split()
                print(f"  SEARCH {label}: {len(uids)}")
                for u in uids:
                    folder_uids.setdefault(u, label)

            # Sample из первых писем за окно — для прайс-папок (чтобы
            # увидеть паттерны рассылок других поставщиков).
            is_price_folder = bool(re.search(r"прайс|price", dec, re.IGNORECASE)) or \
                              bool(re.search(r"price", raw, re.IGNORECASE))
            if is_price_folder:
                try:
                    typ, data = M.search(None, "SINCE", since_str)
                    sample_uids = data[0].split() if (typ == "OK" and data and data[0]) else []
                except Exception as exc:
                    print(f"  [warn] sample-search failed: {exc}")
                    sample_uids = []
                # Берём первые N (oldest first) и последние N (newest first)
                # для разнообразия, дедуп по UID.
                limit = PRICE_FOLDER_SAMPLE_LIMIT // 2
                sampled = list(sample_uids[:limit]) + list(sample_uids[-limit:])
                added = 0
                for u in sampled:
                    if u not in folder_uids:
                        folder_uids[u] = "PRICE-FOLDER-SAMPLE"
                        added += 1
                print(f"  SAMPLE прайс-папки: +{added} UID (всего в папке за окно: {len(sample_uids)})")

            print(f"  Уникальных UID для разбора: {len(folder_uids)}")

            for num, label in folder_uids.items():
                raw_msg = fetch_one(num)
                if raw_msg is None:
                    # был reconnect — переselect и попробуем ещё раз
                    if not select_folder(raw):
                        print("  [skip-rest] не вернулись в папку после reconnect")
                        break
                    raw_msg = fetch_one(num)
                if raw_msg is None:
                    continue
                msg = email.message_from_bytes(raw_msg)

                headers = {
                    "From":             decode_header_value(msg.get("From")),
                    "Reply-To":         decode_header_value(msg.get("Reply-To")),
                    "X-Forwarded-For":  decode_header_value(msg.get("X-Forwarded-For")),
                    "Delivered-To":     decode_header_value(msg.get("Delivered-To")),
                    "Return-Path":      decode_header_value(msg.get("Return-Path")),
                }
                subject = decode_header_value(msg.get("Subject"))
                msg_id = (msg.get("Message-ID") or "").strip()
                date_raw = msg.get("Date") or ""
                try:
                    date_dt = email.utils.parsedate_to_datetime(date_raw)
                except Exception:
                    date_dt = None

                supplier = detect_supplier(headers, subject)
                attachments = parse_attachments(msg)

                tag = supplier or ("GENERIC" if GENERIC_PRICE_RE.search(subject) else label)
                print()
                print(f"  >>[{tag}] {date_dt.isoformat() if date_dt else date_raw}  (hit={label})")
                print(f"     Subject:        {subject!r}")
                print(f"     From:           {headers['From']!r}")
                if headers["Reply-To"]:
                    print(f"     Reply-To:       {headers['Reply-To']!r}")
                if headers["X-Forwarded-For"]:
                    print(f"     X-Forwarded-For:{headers['X-Forwarded-For']!r}")
                if headers["Delivered-To"]:
                    print(f"     Delivered-To:   {headers['Delivered-To']!r}")
                if headers["Return-Path"]:
                    print(f"     Return-Path:    {headers['Return-Path']!r}")
                print(f"     Message-ID:     {msg_id}")
                if attachments:
                    for a in attachments:
                        print(
                            f"     [att]{a['filename']!r} | {a['mime']} | {a['size']} bytes"
                        )
                else:
                    print("     (без вложений)")

                # Сохраняем первое xlsx-вложение от каждого supplier из не-INBOX
                # папок (или же если subject содержит "прайс") — чтобы не
                # сохранить служебное вложение из переписки.
                save_eligible = (
                    supplier
                    and supplier not in saved_per_supplier
                    and (
                        ("прайс" in subject.lower() or "price" in subject.lower())
                        or "прайс" in dec.lower()
                        or "price" in dec.lower()
                    )
                )
                if save_eligible:
                    for a in attachments:
                        fname_lower = a["filename"].lower()
                        is_xlsx = (
                            fname_lower.endswith(".xlsx")
                            or fname_lower.endswith(".xls")
                            or "spreadsheetml" in (a["mime"] or "").lower()
                            or (a["mime"] or "").lower() == "application/vnd.ms-excel"
                        )
                        if not is_xlsx:
                            continue
                        out_dir = SAVE_DIR / supplier
                        out_dir.mkdir(parents=True, exist_ok=True)
                        safe_name = re.sub(r"[^\w.\-]+", "_", a["filename"])
                        out_path = out_dir / safe_name
                        out_path.write_bytes(a["payload"])
                        print(
                            f"     [save]сохранено: {out_path} ({a['size']} bytes)"
                        )
                        saved_per_supplier.add(supplier)
                        break

                if msg_id and msg_id in seen_msg_ids:
                    continue
                if msg_id:
                    seen_msg_ids.add(msg_id)
                matched_messages.append(
                    {
                        "folder":       dec,
                        "supplier":     supplier or "GENERIC",
                        "hit":          label,
                        "date":         date_dt,
                        "subject":      subject,
                        "from":         headers["From"],
                        "reply_to":     headers["Reply-To"],
                        "fwd_for":      headers["X-Forwarded-For"],
                        "msg_id":       msg_id,
                        "attachments":  attachments,
                    }
                )

            try:
                M.close()
            except Exception:
                pass

        # ---- 3. Сводка ------------------------------------------------------
        print()
        print("=" * 70)
        print("СВОДКА")
        print("=" * 70)
        by_supplier = collections.Counter(m["supplier"] for m in matched_messages)
        for supplier, n in by_supplier.items():
            print(f"  {supplier}: {n} писем")

        for supplier in sorted(by_supplier):
            sub = [m for m in matched_messages if m["supplier"] == supplier]
            dates = sorted([m["date"] for m in sub if m["date"]])
            if len(dates) >= 2:
                deltas = [(dates[i + 1] - dates[i]).total_seconds() / 86400.0 for i in range(len(dates) - 1)]
                avg = sum(deltas) / len(deltas)
                print(f"  [{supplier}] средний интервал между письмами: {avg:.2f} дней (n={len(deltas)})")
            elif dates:
                print(f"  [{supplier}] только одно письмо в окне ({dates[0].isoformat()})")

            subjects = collections.Counter(m["subject"] for m in sub)
            print(f"  [{supplier}] уникальных Subject: {len(subjects)}")
            for s, n in subjects.most_common(10):
                print(f"      x{n:>3}  {s!r}")

            attach_names = collections.Counter()
            attach_exts = collections.Counter()
            for m in sub:
                for a in m["attachments"]:
                    attach_names[a["filename"]] += 1
                    ext = os.path.splitext(a["filename"])[1].lower() or "(no-ext)"
                    attach_exts[ext] += 1
            print(f"  [{supplier}] расширения вложений: {dict(attach_exts)}")
            print(f"  [{supplier}] топ имён вложений:")
            for name, n in attach_names.most_common(10):
                print(f"      x{n:>3}  {name!r}")

            unique_msg_ids = {m["msg_id"] for m in sub if m["msg_id"]}
            print(
                f"  [{supplier}] Message-ID: уникальных {len(unique_msg_ids)} / всего {len(sub)}"
            )

        print()
        print(f"[diag] done. matched_total={len(matched_messages)}")
        return 0

    finally:
        try:
            M.logout()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
