# Тесты BaseImapFetcher — общий каркас IMAP-канала автозагрузки (12.1).
#
# Без сети: подменяем imaplib.IMAP4_SSL фейк-клиентом, который отвечает
# заранее заскриптованными письмами.

from __future__ import annotations

import email
import email.utils
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text


# =====================================================================
# Helpers: фейк IMAP-клиента и сборка raw-письма
# =====================================================================

def _make_message_bytes(
    *,
    from_addr: str = "egarifullina@ocs.ru",
    subject: str = "B2B OCS - Состояние склада и цены 06.05.2026",
    msg_id: str = "<msg-test-001@ocs.ru>",
    date_dt: datetime | None = None,
    extra_headers: dict[str, str] | None = None,
    attachment_filename: str | None = "price_06_05_2026.xlsx",
    attachment_bytes: bytes = b"PK\x03\x04fake-xlsx-content",
    attachment_mime: str = "application/octet-stream",
) -> bytes:
    """Собирает MIME-multipart письмо в виде bytes для FakeImap.fetch().

    Достаточно реалистичное, чтобы email.message_from_bytes() корректно
    разобрал заголовки и attachment.walk().
    """
    if date_dt is None:
        date_dt = datetime(2026, 5, 5, 14, 0, tzinfo=timezone.utc)
    date_str = email.utils.format_datetime(date_dt)

    boundary = "===frag-001==="
    headers_lines = [
        f"From: {from_addr}",
        f"Subject: {subject}",
        f"Date: {date_str}",
        f"Message-ID: {msg_id}",
        f"MIME-Version: 1.0",
        f"Content-Type: multipart/mixed; boundary=\"{boundary}\"",
    ]
    for k, v in (extra_headers or {}).items():
        headers_lines.append(f"{k}: {v}")
    body_lines = [
        f"--{boundary}",
        "Content-Type: text/plain; charset=utf-8",
        "Content-Transfer-Encoding: 7bit",
        "",
        "Здравствуйте! Прайс во вложении.",
    ]
    if attachment_filename:
        import base64 as _b64
        b64 = _b64.b64encode(attachment_bytes).decode("ascii")
        body_lines += [
            f"--{boundary}",
            f"Content-Type: {attachment_mime}; name=\"{attachment_filename}\"",
            "Content-Transfer-Encoding: base64",
            f"Content-Disposition: attachment; filename=\"{attachment_filename}\"",
            "",
            b64,
        ]
    body_lines.append(f"--{boundary}--")
    return ("\r\n".join(headers_lines) + "\r\n\r\n" + "\r\n".join(body_lines)).encode("utf-8")


class FakeImap:
    """Минимальный фейк imaplib.IMAP4_SSL.

    Однопапочный режим (для legacy-тестов): принимает список raw-писем
    (bytes) и кладёт их в одну папку INBOX (UID 1..N). LIST возвращает
    INBOX. SEARCH SINCE возвращает все UID; CHARSET-аргумент сохраняется
    в .last_search_charset для проверки тестами (BaseImapFetcher должен
    звать SEARCH без CHARSET).
    """
    def __init__(self, messages: list[bytes]):
        self._folders: dict[str, list[bytes]] = {"INBOX": list(messages)}
        self._current: str | None = None
        self.logged_in = False
        self.last_search_charset: object = "<none>"
        self.search_charsets: list[object] = []

    # noinspection PyUnusedLocal
    def login(self, user, password):
        self.logged_in = True
        return ("OK", [b"LOGIN completed"])

    def list(self, directory='""', pattern="*"):
        # Возвращаем по строке на папку: '(\\HasNoChildren) "/" "<name>"'
        out = []
        for name in self._folders.keys():
            out.append(b'(\\HasNoChildren) "/" "' + name.encode("ascii") + b'"')
        return ("OK", out)

    def select(self, mailbox, readonly=False):
        # mailbox приходит уже в кавычках от base_imap (для не-ASCII).
        name = mailbox.strip('"') if isinstance(mailbox, str) else mailbox
        if name not in self._folders:
            return ("NO", [b"no such mailbox"])
        self._current = name
        return ("OK", [str(len(self._folders[name])).encode()])

    def search(self, charset, *args):
        self.last_search_charset = charset
        self.search_charsets.append(charset)
        msgs = self._folders.get(self._current or "", [])
        if not msgs:
            return ("OK", [b""])
        uids = b" ".join(str(i + 1).encode() for i in range(len(msgs)))
        return ("OK", [uids])

    def fetch(self, uid, what):
        try:
            idx = int(uid) - 1
        except (TypeError, ValueError):
            return ("NO", None)
        msgs = self._folders.get(self._current or "", [])
        if idx < 0 or idx >= len(msgs):
            return ("NO", None)
        # Реальный imaplib возвращает [(prefix_bytes, rfc822_bytes), b")"].
        return ("OK", [(b"1 (RFC822 {%d}" % len(msgs[idx]),
                        msgs[idx]), b")"])

    def close(self):
        return ("OK", [b"close ok"])

    def logout(self):
        return ("BYE", [b"logout"])


class FakeImapMultiFolder(FakeImap):
    """FakeImap с несколькими папками. Каждая запись describe — кортеж
    (raw_name, decoded_name_for_log, flags_str, list_of_messages).
    raw_name — то, что в LIST/SELECT (modified UTF-7 или ASCII).
    flags_str — например '\\HasNoChildren \\Trash'.
    """
    def __init__(self, folders: list[tuple[str, str, str, list[bytes]]]):
        # Не вызываем super().__init__: сами строим _folders/flags.
        self._folders = {raw: list(msgs) for raw, _dec, _fl, msgs in folders}
        self._flags: dict[str, str] = {raw: fl for raw, _dec, fl, _msgs in folders}
        self._current = None
        self.logged_in = False
        self.last_search_charset = "<none>"
        self.search_charsets = []

    def list(self, directory='""', pattern="*"):
        out = []
        for raw, fl in self._flags.items():
            line = (
                b"(" + fl.encode("ascii") + b') "/" "'
                + raw.encode("ascii") + b'"'
            )
            out.append(line)
        return ("OK", out)


def _patch_imap(monkeypatch, messages: list[bytes]):
    """Подменяет imaplib.IMAP4_SSL фейком на _imap.IMAP4_SSL внутри base_imap.

    Сохраняет ссылку на созданный FakeImap в monkeypatch._fake_imap_last,
    чтобы тесты могли проверить .search_charsets и т.п."""
    import app.services.auto_price.fetchers.base_imap as base_imap_mod

    fake = FakeImap(messages)

    def _factory(host, port):
        return fake

    monkeypatch.setattr(base_imap_mod.imaplib, "IMAP4_SSL", _factory)
    monkeypatch.setattr(base_imap_mod.imaplib, "IMAP4", _factory)
    return fake


def _patch_imap_multi(monkeypatch, folders):
    """То же, но для FakeImapMultiFolder."""
    import app.services.auto_price.fetchers.base_imap as base_imap_mod

    fake = FakeImapMultiFolder(folders)

    def _factory(host, port):
        return fake

    monkeypatch.setattr(base_imap_mod.imaplib, "IMAP4_SSL", _factory)
    monkeypatch.setattr(base_imap_mod.imaplib, "IMAP4", _factory)
    return fake


@pytest.fixture()
def imap_env(monkeypatch):
    """Базовые env-переменные для IMAP-fetcher'ов."""
    monkeypatch.setenv("IMAP_HOST", "imap.test.local")
    monkeypatch.setenv("IMAP_PORT", "993")
    monkeypatch.setenv("IMAP_USE_SSL", "true")
    monkeypatch.setenv("IMAP_USER", "imap_user@quadro.test")
    monkeypatch.setenv("IMAP_PASSWORD", "imap_secret_pwd")
    # SMTP — пустые, чтобы не конфликтовали.
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_APP_PASSWORD", raising=False)


# =====================================================================
# Подкласс-заглушка для тестов
# =====================================================================

def _make_test_subclass(slug: str = "test_imap"):
    from app.services.auto_price.fetchers.base_imap import BaseImapFetcher

    class _TestFetcher(BaseImapFetcher):
        supplier_slug = slug
        supplier_display_name = "TestSup"
        sender_pattern = r"@ocs\.ru(?![\w.])"
        subject_pattern = r"^B2B OCS"
        attachment_extensions = (".xlsx",)
        # parse_attachment в тестах либо не дойдёт, либо подменится.
        def parse_attachment(self, data, filename):
            return []

    return _TestFetcher


# =====================================================================
# 1. Креды: fallback на SMTP, ошибка при отсутствии обоих
# =====================================================================

def test_imap_credentials_use_imap_user_when_set(monkeypatch):
    from app.services.auto_price.fetchers.base_imap import _read_imap_credentials

    monkeypatch.setenv("IMAP_USER", "imap@a.test")
    monkeypatch.setenv("IMAP_PASSWORD", "imap_pwd")
    monkeypatch.setenv("SMTP_USER", "smtp@a.test")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "smtp_pwd")

    host, port, ssl_flag, user, pwd = _read_imap_credentials()
    assert user == "imap@a.test"
    assert pwd == "imap_pwd"


def test_imap_credentials_fallback_to_smtp_when_imap_missing(monkeypatch):
    from app.services.auto_price.fetchers.base_imap import _read_imap_credentials

    monkeypatch.delenv("IMAP_USER", raising=False)
    monkeypatch.delenv("IMAP_PASSWORD", raising=False)
    monkeypatch.setenv("SMTP_USER", "smtp@b.test")
    monkeypatch.setenv("SMTP_APP_PASSWORD", "smtp_pwd_b")

    host, port, ssl_flag, user, pwd = _read_imap_credentials()
    assert user == "smtp@b.test"
    assert pwd == "smtp_pwd_b"


def test_imap_credentials_raises_when_both_missing(monkeypatch):
    from app.services.auto_price.fetchers.base_imap import _read_imap_credentials

    monkeypatch.delenv("IMAP_USER", raising=False)
    monkeypatch.delenv("IMAP_PASSWORD", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_APP_PASSWORD", raising=False)

    with pytest.raises(RuntimeError, match="IMAP_USER"):
        _read_imap_credentials()


# =====================================================================
# 2. _find_latest_unprocessed_message — фильтрация и идемпотентность
# =====================================================================

def test_find_latest_message_filters_by_subject_and_sender(imap_env, monkeypatch, db_session):
    """Если в почте лежит smesь писем — берём только те, что подходят
    под sender_pattern И subject_pattern."""
    msgs = [
        # Не подходит: чужой отправитель.
        _make_message_bytes(
            from_addr="info@spam.test",
            subject="B2B OCS - Состояние склада и цены 01.05.2026",
            msg_id="<spam-1@spam.test>",
        ),
        # Не подходит: тема не та (хотя домен ocs.ru).
        _make_message_bytes(
            from_addr="news@ocs.ru",
            subject="Анонс распродажи к 9 мая",
            msg_id="<news-1@ocs.ru>",
        ),
        # Подходит — самое старое.
        _make_message_bytes(
            from_addr="egarifullina@ocs.ru",
            subject="B2B OCS - Состояние склада и цены 02.05.2026",
            msg_id="<ok-old@ocs.ru>",
            date_dt=datetime(2026, 5, 2, 13, 50, tzinfo=timezone.utc),
        ),
        # Подходит — самое свежее.
        _make_message_bytes(
            from_addr="egarifullina@ocs.ru",
            subject="B2B OCS - Состояние склада и цены 06.05.2026",
            msg_id="<ok-new@ocs.ru>",
            date_dt=datetime(2026, 5, 6, 13, 50, tzinfo=timezone.utc),
        ),
    ]
    _patch_imap(monkeypatch, msgs)

    Cls = _make_test_subclass(slug="test_imap_filter")
    fetcher = Cls()
    import imaplib as _imaplib
    client = _imaplib.IMAP4_SSL("h", 993)  # из-за патча — это FakeImap
    client.login("u", "p")
    msg, msg_id, _folder = fetcher._find_latest_unprocessed_message(client)
    assert msg is not None
    assert msg_id == "<ok-new@ocs.ru>"


def test_find_latest_message_skips_already_processed(imap_env, monkeypatch, db_session):
    """Если Message-ID самого свежего письма уже лежит в
    auto_price_load_runs.source_ref — берём предыдущее."""
    msgs = [
        _make_message_bytes(
            from_addr="egarifullina@ocs.ru",
            subject="B2B OCS - Состояние склада и цены 02.05.2026",
            msg_id="<old@ocs.ru>",
            date_dt=datetime(2026, 5, 2, 13, 50, tzinfo=timezone.utc),
        ),
        _make_message_bytes(
            from_addr="egarifullina@ocs.ru",
            subject="B2B OCS - Состояние склада и цены 06.05.2026",
            msg_id="<new@ocs.ru>",
            date_dt=datetime(2026, 5, 6, 13, 50, tzinfo=timezone.utc),
        ),
    ]
    _patch_imap(monkeypatch, msgs)

    # Подсуем «новое» письмо как уже обработанное.
    db_session.execute(text(
        "INSERT INTO auto_price_load_runs "
        "  (supplier_slug, started_at, status, triggered_by, source_ref) "
        "VALUES "
        "  ('test_imap_idem', NOW() - INTERVAL '1 hour', 'success', "
        "   'scheduled', '<new@ocs.ru>')"
    ))
    db_session.commit()

    Cls = _make_test_subclass(slug="test_imap_idem")
    fetcher = Cls()
    import imaplib as _imaplib
    client = _imaplib.IMAP4_SSL("h", 993)
    client.login("u", "p")
    msg, msg_id, _folder = fetcher._find_latest_unprocessed_message(client)
    assert msg is not None
    assert msg_id == "<old@ocs.ru>"


def test_find_latest_message_returns_none_when_all_processed(imap_env, monkeypatch, db_session):
    """Все Message-ID уже в source_ref — возвращаем (None, None)."""
    msgs = [
        _make_message_bytes(
            from_addr="egarifullina@ocs.ru",
            subject="B2B OCS - Состояние склада и цены",
            msg_id="<a@ocs.ru>",
        ),
        _make_message_bytes(
            from_addr="egarifullina@ocs.ru",
            subject="B2B OCS - Состояние склада и цены",
            msg_id="<b@ocs.ru>",
        ),
    ]
    _patch_imap(monkeypatch, msgs)

    db_session.execute(text(
        "INSERT INTO auto_price_load_runs "
        "  (supplier_slug, started_at, status, triggered_by, source_ref) "
        "VALUES "
        "  ('test_imap_all', NOW(), 'success', 'scheduled', '<a@ocs.ru>'), "
        "  ('test_imap_all', NOW(), 'success', 'scheduled', '<b@ocs.ru>')"
    ))
    db_session.commit()

    Cls = _make_test_subclass(slug="test_imap_all")
    fetcher = Cls()
    import imaplib as _imaplib
    client = _imaplib.IMAP4_SSL("h", 993)
    client.login("u", "p")
    msg, msg_id, _folder = fetcher._find_latest_unprocessed_message(client)
    assert msg is None
    assert msg_id is None


# =====================================================================
# 3. _extract_attachment — расширение и размер
# =====================================================================

def test_extract_attachment_picks_xlsx(imap_env):
    raw = _make_message_bytes(
        attachment_filename="prices.xlsx",
        attachment_bytes=b"PK\x03\x04hello",
    )
    msg = email.message_from_bytes(raw)
    Cls = _make_test_subclass()
    fetcher = Cls()
    payload, fname = fetcher._extract_attachment(msg)
    assert fname == "prices.xlsx"
    assert payload == b"PK\x03\x04hello"


def test_extract_attachment_rejects_oversized(imap_env):
    big = b"x" * (3 * 1024 * 1024)  # 3 МБ
    raw = _make_message_bytes(
        attachment_filename="big.xlsx",
        attachment_bytes=big,
    )
    msg = email.message_from_bytes(raw)
    Cls = _make_test_subclass()
    fetcher = Cls()
    fetcher.max_attachment_size_mb = 2  # лимит 2 МБ
    with pytest.raises(RuntimeError, match="превышает лимит"):
        fetcher._extract_attachment(msg)


def test_extract_attachment_raises_when_no_match(imap_env):
    raw = _make_message_bytes(
        attachment_filename="document.pdf",  # не xlsx
        attachment_bytes=b"%PDF-1.4",
    )
    msg = email.message_from_bytes(raw)
    Cls = _make_test_subclass()
    fetcher = Cls()
    with pytest.raises(RuntimeError, match="нет вложения"):
        fetcher._extract_attachment(msg)


# =====================================================================
# 4. fetch_and_save: NoNewDataException когда писем нет
# =====================================================================

def test_fetch_and_save_raises_no_new_data_when_inbox_empty(imap_env, monkeypatch):
    from app.services.auto_price.fetchers.base_imap import NoNewDataException

    _patch_imap(monkeypatch, [])  # пустой ящик

    Cls = _make_test_subclass(slug="test_imap_empty")
    fetcher = Cls()
    with pytest.raises(NoNewDataException):
        fetcher.fetch_and_save()


def test_fetch_and_save_raises_no_new_data_when_no_match(imap_env, monkeypatch):
    """В ящике есть письма, но ни одно не подходит под sender/subject —
    тоже NoNewDataException, не привычная ошибка."""
    from app.services.auto_price.fetchers.base_imap import NoNewDataException

    _patch_imap(monkeypatch, [
        _make_message_bytes(
            from_addr="other@example.com",
            subject="Какой-то другой mail",
            msg_id="<unrel@x.test>",
        ),
    ])
    Cls = _make_test_subclass(slug="test_imap_unrel")
    fetcher = Cls()
    with pytest.raises(NoNewDataException):
        fetcher.fetch_and_save()


# =====================================================================
# 5. Multi-folder обход: ВСЕ папки, не только INBOX (12.1-fix)
# =====================================================================

def test_searches_across_all_user_folders_not_only_inbox(imap_env, monkeypatch, db_session):
    """Письмо лежит ТОЛЬКО в кастомной папке «Прайсы» (raw=&BB8EQAQwBDkEQQRL-),
    не в INBOX. Fetcher должен его найти."""
    msg_in_priceses = _make_message_bytes(
        from_addr="egarifullina@ocs.ru",
        subject="B2B OCS - Состояние склада и цены на 06.05.2026 13:44, Ф05, партнер: К0077581",
        msg_id="<found-in-priceses@ocs.ru>",
        date_dt=datetime(2026, 5, 6, 13, 44, tzinfo=timezone.utc),
    )
    msg_in_other = _make_message_bytes(
        from_addr="news@example.com",
        subject="Newsletter (нерелевантно)",
        msg_id="<unrelated@example.com>",
        date_dt=datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc),
    )

    _patch_imap_multi(monkeypatch, [
        ("INBOX",                "INBOX",         "\\HasNoChildren",       [msg_in_other]),
        ("&BB8EQAQwBDkEQQRL-",   "Прайсы",        "\\HasNoChildren",       [msg_in_priceses]),
    ])

    Cls = _make_test_subclass(slug="test_imap_multi_priceses")
    fetcher = Cls()
    import imaplib as _imaplib
    client = _imaplib.IMAP4_SSL("h", 993)
    client.login("u", "p")
    msg, msg_id, folder = fetcher._find_latest_unprocessed_message(client)
    assert msg is not None
    assert msg_id == "<found-in-priceses@ocs.ru>"
    # Имя папки в логе должно быть raskодировано из UTF-7.
    assert folder == "Прайсы"


def test_skips_system_folders(imap_env, monkeypatch, db_session):
    """Письмо лежит в Trash (с флагом \\Trash) — fetcher НЕ должен его
    находить. Ни одного нового письма → возвращает (None, None, None)."""
    msg_in_trash = _make_message_bytes(
        from_addr="egarifullina@ocs.ru",
        subject="B2B OCS - Состояние склада и цены 06.05.2026",
        msg_id="<deleted@ocs.ru>",
        date_dt=datetime(2026, 5, 6, 13, 44, tzinfo=timezone.utc),
    )

    _patch_imap_multi(monkeypatch, [
        ("INBOX",                          "INBOX",      "\\HasNoChildren",            []),
        # Корзина (mail.ru) — раскодированное имя совпадает с системным
        # списком, плюс флаг \Trash.
        ("&BBoEPgRABDcEOAQ9BDA-",          "Корзина",    "\\HasNoChildren \\Trash",    [msg_in_trash]),
        # Удаленные — старое имя trash mail.ru (без флага, только по
        # имени — должна тоже отбрасываться).
        ("&BCMENAQwBDsENQQ9BD0ESwQ1-",     "Удаленные",  "\\HasNoChildren",            [msg_in_trash]),
        # Черновики
        ("&BCcENQRABD0EPgQyBDgEOgQ4-",     "Черновики",  "\\HasNoChildren \\Drafts",   []),
        # Спам
        ("&BCEEPwQwBDw-",                  "Спам",       "\\HasNoChildren \\Junk",     []),
        # Отправленные
        ("&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-",  "Отправленные", "\\HasNoChildren \\Sent",  []),
    ])

    Cls = _make_test_subclass(slug="test_imap_skip_sys")
    fetcher = Cls()
    import imaplib as _imaplib
    client = _imaplib.IMAP4_SSL("h", 993)
    client.login("u", "p")
    msg, msg_id, folder = fetcher._find_latest_unprocessed_message(client)
    assert msg is None
    assert msg_id is None
    assert folder is None


def test_decodes_utf7_folder_names():
    """imap_utf7_decode корректно декодирует имена папок mail.ru."""
    from app.services.auto_price.fetchers.base_imap import _imap_utf7_decode
    assert _imap_utf7_decode("INBOX") == "INBOX"
    assert _imap_utf7_decode("&BB8EQAQwBDkEQQRL-") == "Прайсы"
    assert _imap_utf7_decode("&BBoEPgRABDcEOAQ9BDA-") == "Корзина"
    assert _imap_utf7_decode("&BCcENQRABD0EPgQyBDgEOgQ4-") == "Черновики"
    assert _imap_utf7_decode("INBOX/Newsletters") == "INBOX/Newsletters"
    # Mixed (не должен ломаться на &-)
    assert _imap_utf7_decode("Project &- Plans") == "Project & Plans"


def test_skips_noselect_folders(imap_env, monkeypatch, db_session):
    """Папка с флагом \\Noselect (контейнерный узел иерархии) пропускается
    — SELECT на ней вернул бы NO. Проверяем что fetcher игнорирует её
    ещё на стадии LIST-фильтрации."""
    msg_ok = _make_message_bytes(
        from_addr="egarifullina@ocs.ru",
        subject="B2B OCS - Состояние склада и цены 06.05.2026",
        msg_id="<ok@ocs.ru>",
        date_dt=datetime(2026, 5, 6, 13, 44, tzinfo=timezone.utc),
    )

    _patch_imap_multi(monkeypatch, [
        ("INBOX",          "INBOX",          "\\HasNoChildren",       [msg_ok]),
        # Контейнер без писем (\Noselect) — типичен для иерархий типа [Gmail].
        ("[Custom]",       "[Custom]",       "\\Noselect \\HasChildren", []),
    ])

    Cls = _make_test_subclass(slug="test_imap_noselect")
    fetcher = Cls()
    import imaplib as _imaplib
    client = _imaplib.IMAP4_SSL("h", 993)
    client.login("u", "p")
    msg, msg_id, folder = fetcher._find_latest_unprocessed_message(client)
    assert msg_id == "<ok@ocs.ru>"
    assert folder == "INBOX"


def test_idempotency_works_across_folders(imap_env, monkeypatch, db_session):
    """Самое свежее письмо в кастомной папке уже обработано (его
    Message-ID есть в auto_price_load_runs.source_ref) — fetcher должен
    взять предыдущее письмо ИЗ ЛЮБОЙ папки. Проверяем, что
    идемпотентность работает не только в пределах INBOX."""
    msg_old_inbox = _make_message_bytes(
        from_addr="egarifullina@ocs.ru",
        subject="B2B OCS - Состояние склада и цены 02.05.2026",
        msg_id="<old-inbox@ocs.ru>",
        date_dt=datetime(2026, 5, 2, 13, 44, tzinfo=timezone.utc),
    )
    msg_new_priceses = _make_message_bytes(
        from_addr="egarifullina@ocs.ru",
        subject="B2B OCS - Состояние склада и цены 06.05.2026",
        msg_id="<new-priceses@ocs.ru>",
        date_dt=datetime(2026, 5, 6, 13, 44, tzinfo=timezone.utc),
    )

    _patch_imap_multi(monkeypatch, [
        ("INBOX",              "INBOX",   "\\HasNoChildren", [msg_old_inbox]),
        ("&BB8EQAQwBDkEQQRL-", "Прайсы",  "\\HasNoChildren", [msg_new_priceses]),
    ])

    db_session.execute(text(
        "INSERT INTO auto_price_load_runs "
        "  (supplier_slug, started_at, status, triggered_by, source_ref) "
        "VALUES "
        "  ('test_imap_idem_x', NOW() - INTERVAL '1 hour', 'success', "
        "   'scheduled', '<new-priceses@ocs.ru>')"
    ))
    db_session.commit()

    Cls = _make_test_subclass(slug="test_imap_idem_x")
    fetcher = Cls()
    import imaplib as _imaplib
    client = _imaplib.IMAP4_SSL("h", 993)
    client.login("u", "p")
    msg, msg_id, folder = fetcher._find_latest_unprocessed_message(client)
    assert msg_id == "<old-inbox@ocs.ru>"
    assert folder == "INBOX"


def test_search_uses_ascii_only_not_utf8_charset(imap_env, monkeypatch, db_session):
    """VK Workspace IMAP плохо переваривает CHARSET UTF-8 в SEARCH —
    BaseImapFetcher должен звать SEARCH с CHARSET=None (ASCII-only)."""
    msg = _make_message_bytes(
        from_addr="egarifullina@ocs.ru",
        subject="B2B OCS - Состояние склада и цены 06.05.2026",
        msg_id="<x@ocs.ru>",
    )
    fake = _patch_imap(monkeypatch, [msg])

    Cls = _make_test_subclass(slug="test_imap_charset")
    fetcher = Cls()
    import imaplib as _imaplib
    client = _imaplib.IMAP4_SSL("h", 993)
    client.login("u", "p")
    fetcher._find_latest_unprocessed_message(client)

    # Должен был выполниться хотя бы один SEARCH, и каждый из них —
    # с charset=None.
    assert fake.search_charsets, "SEARCH должен был быть вызван"
    for ch in fake.search_charsets:
        assert ch is None, (
            f"SEARCH должен идти ASCII-only (CHARSET=None), а не {ch!r}"
        )


# =====================================================================
# 6. Парсинг LIST-ответа (UTF-7 + флаги)
# =====================================================================

def test_parse_list_line_extracts_flags_and_decoded_name():
    from app.services.auto_price.fetchers.base_imap import _parse_list_line

    line = '(\\HasNoChildren) "/" "&BB8EQAQwBDkEQQRL-"'
    parsed = _parse_list_line(line)
    assert parsed is not None
    flags, raw, decoded = parsed
    assert "\\hasnochildren" in flags
    assert raw == "&BB8EQAQwBDkEQQRL-"
    assert decoded == "Прайсы"

    line2 = '(\\HasNoChildren \\Trash) "/" "Trash"'
    flags2, raw2, decoded2 = _parse_list_line(line2)
    assert "\\trash" in flags2
    assert raw2 == "Trash"
    assert decoded2 == "Trash"


def test_is_system_folder_drops_trash_drafts_sent_etc():
    from app.services.auto_price.fetchers.base_imap import _is_system_folder

    # Только по флагу.
    assert _is_system_folder("\\hasnochildren \\trash", "Корзина")
    assert _is_system_folder("\\drafts", "Drafts")
    assert _is_system_folder("\\junk", "Spam")
    assert _is_system_folder("\\noselect \\haschildren", "[Gmail]")

    # Только по русскому имени (mail.ru без \-флагов).
    assert _is_system_folder("\\hasnochildren", "Удаленные")
    assert _is_system_folder("\\hasnochildren", "Черновики")
    assert _is_system_folder("\\hasnochildren", "Отправленные")
    assert _is_system_folder("\\hasnochildren", "Спам")
    assert _is_system_folder("\\hasnochildren", "Нежелательная почта")

    # НЕ системные.
    assert not _is_system_folder("\\hasnochildren", "INBOX")
    assert not _is_system_folder("\\hasnochildren", "Прайсы")
    assert not _is_system_folder("\\hasnochildren", "Tender-Win")
    # Подпапки INBOX (под пользовательскими фильтрами).
    assert not _is_system_folder("\\hasnochildren", "INBOX/Newsletters")
    assert not _is_system_folder("\\hasnochildren", "INBOX/Receipts")
