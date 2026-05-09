from __future__ import annotations

import logging

import pytest

from app.modules.auctions.catalog.brand_normalizer import canonical_brand


# По 2+ кейса на каждый канон: (input, expected_canon).
# Минимум один кейс — нестандартное написание из реальных прайсов.
KNOWN_CASES = [
    # HP / HPE
    ("HP", "HP"),
    ("hp", "HP"),
    ("HP Inc.", "HP"),
    ("HP INC", "HP"),
    ("Hewlett-Packard", "HP"),
    ("HPE", "HPE"),
    ("hewlett packard enterprise", "HPE"),
    # Pantum
    ("Pantum", "Pantum"),
    ("PANTUM", "Pantum"),
    # Canon
    ("Canon", "Canon"),
    ("CANON", "Canon"),
    # Kyocera
    ("Kyocera", "Kyocera"),
    ("KYOCERA", "Kyocera"),
    ("Kyocera Mita", "Kyocera"),
    # Konica Minolta
    ("Konica Minolta", "Konica Minolta"),
    ("Konica-Minolta", "Konica Minolta"),
    ("KONICAMINOLTA", "Konica Minolta"),
    # Xerox
    ("Xerox", "Xerox"),
    ("XEROX", "Xerox"),
    # Brother
    ("Brother", "Brother"),
    ("BROTHER", "Brother"),
    # Ricoh
    ("Ricoh", "Ricoh"),
    ("RICOH", "Ricoh"),
    # Epson
    ("Epson", "Epson"),
    ("EPSON", "Epson"),
    # Sharp
    ("Sharp", "Sharp"),
    ("SHARP", "Sharp"),
    # Lexmark
    ("Lexmark", "Lexmark"),
    ("LEXMARK", "Lexmark"),
    # OKI
    ("OKI", "OKI"),
    ("oki data", "OKI"),
    # Toshiba
    ("Toshiba", "Toshiba"),
    ("TOSHIBA", "Toshiba"),
    # Samsung
    ("Samsung", "Samsung"),
    ("SAMSUNG", "Samsung"),
    # Sindoh
    ("Sindoh", "Sindoh"),
    ("SINDOH", "Sindoh"),
    # Katusha IT
    ("Katusha IT", "Katusha IT"),
    ("Katusha-IT", "Katusha IT"),
    ("КАТЮША", "Katusha IT"),
    # G&G
    ("G&G", "G&G"),
    ("G G", "G&G"),
    ("GG", "G&G"),
    ("g and g", "G&G"),
    # iRU
    ("iRU", "iRU"),
    ("IRU", "iRU"),
    # Cactus
    ("Cactus", "Cactus"),
    ("CACTUS", "Cactus"),
    # Bulat
    ("Bulat", "Bulat"),
    ("БУЛАТ", "Bulat"),
]


@pytest.mark.parametrize("raw,expected", KNOWN_CASES)
def test_canonical_brand_known(raw: str, expected: str) -> None:
    assert canonical_brand(raw) == expected


def test_whitespace_collapsed() -> None:
    assert canonical_brand("  HP  ") == "HP"
    assert canonical_brand("HP\xa0Inc.") == "HP"
    assert canonical_brand("Konica   Minolta") == "Konica Minolta"


def test_empty_inputs_return_empty_string() -> None:
    assert canonical_brand("") == ""
    assert canonical_brand("   ") == ""
    assert canonical_brand("\xa0") == ""
    assert canonical_brand(None) == ""


def test_unknown_brand_falls_back_to_title_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.modules.auctions.catalog.brand_normalizer")
    result = canonical_brand("foobarcorp")
    assert result == "Foobarcorp"
    assert any(
        "unknown brand" in rec.message and "foobarcorp" in rec.message.lower()
        for rec in caplog.records
    )


def test_idempotent_known() -> None:
    # Повторное применение к уже каноничному значению ничего не меняет.
    for canon in {"HP", "Pantum", "Konica Minolta", "G&G", "iRU", "OKI"}:
        assert canonical_brand(canon) == canon
        assert canonical_brand(canonical_brand(canon)) == canon


def test_idempotent_unknown() -> None:
    # Для неизвестного бренда title-case-фоллбэк тоже должен быть стабильным.
    once = canonical_brand("Acme Print Lab")
    assert canonical_brand(once) == once
