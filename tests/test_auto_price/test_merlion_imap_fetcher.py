# Тесты MerlionImapFetcher — IMAP-канал Merlion (12.1).

from __future__ import annotations

import io
import re
import zipfile

import pytest


# =====================================================================
# 1. Subject regex
# =====================================================================

def test_merlion_subject_regex_matches():
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    pat = re.compile(MerlionImapFetcher.subject_pattern, re.IGNORECASE)
    for s in [
        "Прайс-лист MERLION",
        "Прайс-лист MERLION Москва 06.05.2026",
        "  Прайс-лист MERLION 30.04.2026",
        # Реальный Subject из IMAP-разведки 2026-05-06 (только этот):
        "Прайс-лист MERLION",
    ]:
        assert pat.search(s), f"subject должен матчиться: {s!r}"


def test_merlion_subject_regex_rejects_unrelated():
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    pat = re.compile(MerlionImapFetcher.subject_pattern, re.IGNORECASE)
    for s in [
        "Новости MERLION",
        "Прайс OCS - Состояние склада и цены",
        "Re: Прайс-лист MERLION",
    ]:
        assert not pat.search(s), f"subject должен быть отклонён: {s!r}"


# =====================================================================
# 2. Sender regex (домен merlion.ru, в т.ч. через Gmail-forward)
# =====================================================================

def test_merlion_sender_regex_matches():
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    pat = re.compile(MerlionImapFetcher.sender_pattern, re.IGNORECASE)
    for s in [
        "matveeva.y@merlion.ru",
        "Матвеева <matveeva.y@merlion.ru>",
        "пересылка via gmail; original: news@merlion.ru",
        # Реальные From из IMAP-разведки 2026-05-06:
        "matveeva.y@merlion.ru <matveeva.y@merlion.ru>",  # двойной From после Gmail-forward
        "Matveeva Yuliya <Matveeva.Y@merlion.ru>",        # display name + capitalized
        "Antonov Sergey <Antonov.S@merlion.ru>",          # другой менеджер
    ]:
        assert pat.search(s), f"sender должен матчиться: {s!r}"


def test_merlion_from_via_gmail_forward_matches():
    """После Gmail-forward Reply-To/X-Forwarded-For/Return-Path содержат
    quadro.kzn@gmail.com и caf_-префиксы, но FROM сохраняет реальный
    @merlion.ru. BaseImapFetcher склеивает все адресные заголовки в один
    haystack, и должен матчить именно по From — gmail в Reply-To не
    должен мешать (regex ищет @merlion.ru, gmail.com его не сматчит)."""
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher
    from app.services.auto_price.fetchers.base_imap import _addresses_in_header

    headers = {
        "From":            "matveeva.y@merlion.ru <matveeva.y@merlion.ru>",
        "Reply-To":        "",
        "X-Forwarded-For": "quadro.kzn@gmail.com quadro@quadro.tatar",
        "Return-Path":     "<quadro.kzn+caf_=quadro=quadro.tatar@gmail.com>",
        "Sender":          "",
    }
    haystack = _addresses_in_header(headers)
    pat = re.compile(MerlionImapFetcher.sender_pattern, re.IGNORECASE)
    assert pat.search(haystack), (
        f"Gmail-forwarded Merlion должен матчиться по From, "
        f"haystack={haystack!r}"
    )


def test_merlion_sender_regex_rejects_other():
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    pat = re.compile(MerlionImapFetcher.sender_pattern, re.IGNORECASE)
    for s in ["egarifullina@ocs.ru", "noreply@merlion.com", "scam@merlion.ru.fake"]:
        assert not pat.search(s)


# =====================================================================
# 3. ZIP extraction
# =====================================================================

def _make_zip_with(files: list[tuple[str, bytes]]) -> bytes:
    """Собирает in-memory ZIP с указанными файлами."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
    return buf.getvalue()


def test_merlion_zip_extraction_picks_largest_xlsx(monkeypatch):
    """В ZIP может быть несколько .xlsx (основной + сопровождающие).
    parse_attachment должен взять самый большой и передать в MerlionLoader."""
    import app.services.auto_price.fetchers.merlion_imap as mer_mod
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    # Готовим ZIP с двумя xlsx разного размера + один pdf.
    big = b"PK\x03\x04" + (b"X" * 5000) + b"big-xlsx-end"
    small = b"PK\x03\x04small-xlsx"
    pdf = b"%PDF-1.4 lic"
    zip_bytes = _make_zip_with([
        ("license.pdf", pdf),
        ("merlion_msk_06_05.xlsx", big),
        ("hint_short.xlsx", small),
    ])

    captured = {}

    class _StubLoader:
        def iter_rows(self, filepath):
            captured["filepath"] = filepath
            with open(filepath, "rb") as f:
                captured["bytes"] = f.read()
            return iter([])

    monkeypatch.setattr(mer_mod, "MerlionLoader", lambda: _StubLoader())
    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")

    fetcher = MerlionImapFetcher()
    rows = fetcher.parse_attachment(zip_bytes, "merlion_06_05.zip")

    assert rows == []
    assert captured["filepath"].endswith(".xlsx")
    assert captured["bytes"] == big  # выбран самый большой


def test_merlion_zip_extraction_with_single_xlsx(monkeypatch):
    """Базовый кейс: один XLSX внутри — берём его."""
    import app.services.auto_price.fetchers.merlion_imap as mer_mod
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    payload = b"PK\x03\x04only-one"
    zip_bytes = _make_zip_with([("price.xlsx", payload)])

    seen = {}

    class _StubLoader:
        def iter_rows(self, filepath):
            with open(filepath, "rb") as f:
                seen["bytes"] = f.read()
            return iter([])

    monkeypatch.setattr(mer_mod, "MerlionLoader", lambda: _StubLoader())
    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")

    MerlionImapFetcher().parse_attachment(zip_bytes, "merlion.zip")
    assert seen["bytes"] == payload


def test_merlion_zip_no_xlsx_raises(monkeypatch):
    """ZIP без xlsx — RuntimeError, чтобы runner и orchestrator не делали
    загрузку с пустыми rows (не обнуляли остатки)."""
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    zip_bytes = _make_zip_with([("license.pdf", b"%PDF-1.4")])

    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")
    fetcher = MerlionImapFetcher()
    with pytest.raises(RuntimeError, match="не содержит ни одного файла .xlsx"):
        fetcher.parse_attachment(zip_bytes, "merlion.zip")


def test_merlion_bad_zip_raises(monkeypatch):
    """Bytes — не ZIP. RuntimeError, NoNewDataException не уместен —
    письмо есть, но кривое."""
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")
    fetcher = MerlionImapFetcher()
    with pytest.raises(RuntimeError, match="не распознано как ZIP"):
        fetcher.parse_attachment(b"not a zip", "broken.zip")
