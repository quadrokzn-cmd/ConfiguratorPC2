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

    Принимает список raw-писем (bytes) и отдаёт их по UID 1..N.
    SEARCH SINCE возвращает все UID. Любой другой SEARCH с FROM/SUBJECT
    игнорируется (server-side фильтр в BaseImapFetcher не используется,
    мы фильтруем уже на клиенте).
    """
    def __init__(self, messages: list[bytes]):
        self._messages = messages
        self.logged_in = False

    # noinspection PyUnusedLocal
    def login(self, user, password):
        self.logged_in = True
        return ("OK", [b"LOGIN completed"])

    def select(self, mailbox, readonly=False):
        return ("OK", [str(len(self._messages)).encode()])

    def search(self, charset, *args):
        # SINCE-фильтр игнорируем — отдаём всё, тест сам проверяет
        # клиентскую сторону.
        if not self._messages:
            return ("OK", [b""])
        uids = b" ".join(str(i + 1).encode() for i in range(len(self._messages)))
        return ("OK", [uids])

    def fetch(self, uid, what):
        try:
            idx = int(uid) - 1
        except (TypeError, ValueError):
            return ("NO", None)
        if idx < 0 or idx >= len(self._messages):
            return ("NO", None)
        # Реальный imaplib возвращает [(prefix_bytes, rfc822_bytes), b")"].
        return ("OK", [(b"1 (RFC822 {%d}" % len(self._messages[idx]),
                        self._messages[idx]), b")"])

    def close(self):
        return ("OK", [b"close ok"])

    def logout(self):
        return ("BYE", [b"logout"])


def _patch_imap(monkeypatch, messages: list[bytes]):
    """Подменяет imaplib.IMAP4_SSL фейком на _imap.IMAP4_SSL внутри base_imap."""
    import app.services.auto_price.fetchers.base_imap as base_imap_mod

    def _factory(host, port):
        return FakeImap(messages)

    monkeypatch.setattr(base_imap_mod.imaplib, "IMAP4_SSL", _factory)
    monkeypatch.setattr(base_imap_mod.imaplib, "IMAP4", _factory)


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
    msg, msg_id = fetcher._find_latest_unprocessed_message(client)
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
    msg, msg_id = fetcher._find_latest_unprocessed_message(client)
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
    msg, msg_id = fetcher._find_latest_unprocessed_message(client)
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
