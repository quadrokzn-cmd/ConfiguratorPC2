# Тесты OCSImapFetcher — IMAP-канал OCS (12.1).

from __future__ import annotations

import re

import pytest


# =====================================================================
# 1. Subject regex: реальные форматы из разведки
# =====================================================================

REAL_OCS_SUBJECTS = [
    "B2B OCS - Состояние склада и цены 06.05.2026",
    "B2B OCS - Состояние склада и цены 01.05.2026",
    "B2B OCS - Состояние склада и цены 30.04.2026",
    "  B2B OCS - Состояние склада и цены 15.04.2026",  # с пробелами
    "B2B OCS  -  Состояние склада и цены",  # двойные пробелы
    # Реальные строки из IMAP-разведки 2026-05-05/06 (папка «Прайсы»):
    "B2B OCS - Состояние склада и цены на 17.04.2026 13:44, Ф05, партнер: К0077581",
    "B2B OCS - Состояние склада и цены на 06.05.2026 13:44, Ф05, партнер: К0077581",
    "B2B OCS - Состояние склада и цены на 28.04.2026 13:43, Ф05, партнер: К0077581",
]


def test_ocs_subject_regex_matches_real_subjects():
    from portal.services.configurator.auto_price.fetchers.ocs_imap import OCSImapFetcher

    pat = re.compile(OCSImapFetcher.subject_pattern, re.IGNORECASE)
    for s in REAL_OCS_SUBJECTS:
        assert pat.search(s), f"subject должен матчиться: {s!r}"


def test_ocs_subject_regex_rejects_unrelated():
    from portal.services.configurator.auto_price.fetchers.ocs_imap import OCSImapFetcher

    pat = re.compile(OCSImapFetcher.subject_pattern, re.IGNORECASE)
    rejects = [
        "OCS - Какой-то другой mail",
        "Анонс новой акции от OCS",
        "Re: B2B OCS - Состояние склада и цены",  # ответ в треде
        "Прайс-лист MERLION",
    ]
    for s in rejects:
        assert not pat.search(s), f"subject должен быть отклонён: {s!r}"


# =====================================================================
# 2. Sender regex: домен ocs.ru, любой адрес
# =====================================================================

def test_ocs_sender_regex_matches_ocs_ru_addresses():
    from portal.services.configurator.auto_price.fetchers.ocs_imap import OCSImapFetcher

    pat = re.compile(OCSImapFetcher.sender_pattern, re.IGNORECASE)
    for s in [
        "egarifullina@ocs.ru",
        "Гарифуллина <egarifullina@ocs.ru>",
        "noreply@ocs.ru",
        "from manager@ocs.ru via gmail-forward",
        # Реальный From из разведки (без угловых скобок):
        "egarifullina@ocs.ru",
        # Реальный Return-Path из разведки (с угловыми скобками):
        "<egarifullina@ocs.ru>",
        # Возможный смешанный From с display name:
        "Egarifullina E. <egarifullina@ocs.ru>",
    ]:
        assert pat.search(s), f"sender должен матчиться: {s!r}"


def test_ocs_sender_regex_rejects_other_domains():
    from portal.services.configurator.auto_price.fetchers.ocs_imap import OCSImapFetcher

    pat = re.compile(OCSImapFetcher.sender_pattern, re.IGNORECASE)
    for s in [
        "info@merlion.ru",
        "noreply@example.com",
        "test@OCS.RU.spam.test",  # subdomain abuse
    ]:
        assert not pat.search(s), f"sender должен быть отклонён: {s!r}"


# =====================================================================
# 3. parse_attachment передаёт bytes в OcsLoader через временный файл
# =====================================================================

def test_ocs_parse_attachment_uses_existing_loader(tmp_path, monkeypatch):
    """parse_attachment должен записать bytes во временный xlsx и вызвать
    OcsLoader.iter_rows(filepath). Проверяем именно вызов и аргументы —
    содержимое xlsx моки не важно (OcsLoader тестируется отдельно)."""
    from portal.services.configurator.auto_price.fetchers.ocs_imap import OCSImapFetcher
    import portal.services.configurator.auto_price.fetchers.ocs_imap as ocs_imap_mod

    captured = {}

    class _StubLoader:
        def iter_rows(self, filepath):
            captured["filepath"] = filepath
            # Проверяем, что файл реально существует в этот момент.
            import os as _os
            captured["exists"] = _os.path.exists(filepath)
            with open(filepath, "rb") as f:
                captured["bytes"] = f.read()
            return iter([])  # пустой Iterator[PriceRow]

    monkeypatch.setattr(ocs_imap_mod, "OcsLoader", lambda: _StubLoader())

    # Чтобы __init__ не упал на проверке кред — поставим минимум.
    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")

    fetcher = OCSImapFetcher()
    rows = fetcher.parse_attachment(b"PK\x03\x04SAMPLE", "price_06_05_2026.xlsx")

    assert rows == []
    assert captured["bytes"] == b"PK\x03\x04SAMPLE"
    assert captured["exists"] is True
    assert captured["filepath"].endswith(".xlsx")
