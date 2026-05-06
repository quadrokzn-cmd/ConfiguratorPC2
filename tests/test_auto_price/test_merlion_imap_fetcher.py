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
    ]:
        assert pat.search(s)


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
