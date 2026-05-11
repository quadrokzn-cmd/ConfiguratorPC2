"""Тесты единого канонизатора брендов (Этап 4 слияния, 2026-05-08).

Перенесены из QT (`auctions_staging/tests/test_brand_normalizer.py`),
плюс расширены парой ПК-кейсов (ASUS/AMD/Palit) — этими брендами
дополнен словарь алиасов в `portal.services.catalog.brand_normalizer`.
"""

from __future__ import annotations

import logging

import pytest

from portal.services.catalog.brand_normalizer import canonical_brand


# По 2+ кейса на каждый канон: (input, expected_canon).
# Минимум один кейс — нестандартное написание из реальных прайсов.
KNOWN_CASES = [
    # --- Печать (перенос из QT) ---
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

    # --- ПК-компоненты (добавлено на Этапе 4 слияния) ---
    ("ASUS", "ASUS"),
    ("asus", "ASUS"),
    ("AsusTek", "ASUS"),
    ("MSI", "MSI"),
    ("micro-star", "MSI"),
    ("Gigabyte", "Gigabyte"),
    ("GIGABYTE", "Gigabyte"),
    ("ASRock", "ASRock"),
    ("AMD", "AMD"),
    ("amd", "AMD"),
    ("Advanced Micro Devices", "AMD"),
    ("Intel", "Intel"),
    ("INTEL", "Intel"),
    ("NVIDIA", "NVIDIA"),
    ("nvidia", "NVIDIA"),
    ("Palit", "Palit"),
    ("PALIT", "Palit"),
    ("Corsair", "Corsair"),
    ("CORSAIR", "Corsair"),
    ("Kingston", "Kingston"),
    ("ADATA", "ADATA"),
    ("A-Data", "ADATA"),
    ("Crucial", "Crucial"),
    ("Western Digital", "Western Digital"),
    ("WD", "Western Digital"),
    ("Seagate", "Seagate"),
    ("Cooler Master", "Cooler Master"),
    ("CoolerMaster", "Cooler Master"),
    ("DeepCool", "DeepCool"),
    ("Noctua", "Noctua"),
    ("Seasonic", "Seasonic"),
    ("EVGA", "EVGA"),
    ("Sapphire", "Sapphire"),
    ("ATI", "ATI"),

    # --- Расширение словаря 2026-05-08 (мини-фикс: 5981 → 1611 правок) ---
    # Спец-канон 'unknown' — orchestrator-fallback, должен оставаться lower-case
    # и не превращаться в 'Unknown' через title()-фоллбэк.
    ("unknown", "unknown"),
    ("Unknown", "unknown"),
    ("UNKNOWN", "unknown"),
    # CPU
    ("Intel Corporation", "Intel"),
    ("Intel Corp", "Intel"),
    ("SuperMicro", "Supermicro"),
    # Motherboards / GPU AIBs (case-fold — самые массовые правки)
    ("ASROCK", "ASRock"),
    ("GIGABYTE", "Gigabyte"),
    ("BIOSTAR", "Biostar"),
    ("Biostar Microtech Netherlands B.V.", "Biostar"),
    ("AFOX CORPORATION", "AFOX"),
    ("AFOX", "AFOX"),
    ("MAXSUN", "MAXSUN"),
    ("PALIT", "Palit"),
    ("ZOTAC", "ZOTAC"),
    ("INNO3D", "INNO3D"),
    ("PNY", "PNY"),
    ("Matrox", "Matrox"),
    # RAM / Storage
    ("Patriot", "Patriot"),
    ("PATRIOT", "Patriot"),
    ("KingSpec", "KingSpec"),
    ("KINGSPEC", "KingSpec"),
    ("Foxline", "Foxline"),
    ("Netac", "Netac"),
    ("Apacer", "Apacer"),
    ("Samsung Electronics", "Samsung"),
    ("MICRON", "Micron"),
    ("DIGMA", "DIGMA"),
    ("Digma", "DIGMA"),
    ("ТМИ", "ТМИ"),
    ("ООО «Телеком и Микроэлектроник Индастриз»", "ТМИ"),
    ("KIOXIA", "KIOXIA"),
    ("KIOXIA Europe GmbH.", "KIOXIA"),
    ("HIKVISION", "Hikvision"),
    ("Solidigm", "Solidigm"),
    # PSU
    ("ExeGate", "ExeGate"),
    ("EXEGATE", "ExeGate"),
    ("CHIEFTEC", "Chieftec"),
    ("FSP GROUP", "FSP"),
    ("1STPLAYER", "1stPlayer"),
    ("SEASONIC", "Seasonic"),
    ("Shenzhen GuoxinHengyu Technology (Gooxi)", "Gooxi"),
    # Cases / Cooling
    ("ZALMAN", "Zalman"),
    ("Aerocool", "Aerocool"),
    ("AEROCOOL", "Aerocool"),
    ("ID-COOLING", "ID-Cooling"),
    ("ID-Cooling", "ID-Cooling"),
    ("PCCOOLER", "PCCooler"),
    ("PcCooler", "PCCooler"),
    ("THERMALRIGHT", "Thermalright"),
    ("THERMALTAKE", "Thermaltake"),
    ("ARCTIC", "Arctic"),
    ("LIAN-LI", "Lian Li"),
    ("Lian-Li", "Lian Li"),
    ("Lian Li", "Lian Li"),
    ("RAIJINTEK CO LTD", "Raijintek"),
    ("ALSEYE CORPORATION LIMITED", "Alseye"),
    ("Raspberry Pi Foundation", "Raspberry Pi"),
    ("IN WIN", "InWin"),
    ("In-Win", "InWin"),
    ("ACCORD", "Accord"),
    ("FORMULA", "Formula"),
    ("Formula V", "Formula V"),
    ("KINGPRICE", "KingPrice"),
    ("SILVERSTONE", "SilverStone"),
    # Сетевые/прочие
    ("Cisco", "Cisco"),
    ("Lenovo", "Lenovo"),
    ("Ubiquiti", "Ubiquiti"),
]


@pytest.mark.parametrize("raw,expected", KNOWN_CASES)
def test_canonical_brand_known(raw: str, expected: str) -> None:
    assert canonical_brand(raw) == expected


def test_whitespace_collapsed() -> None:
    assert canonical_brand("  HP  ") == "HP"
    assert canonical_brand("HP\xa0Inc.") == "HP"
    assert canonical_brand("Konica   Minolta") == "Konica Minolta"
    assert canonical_brand("  ASUS  ") == "ASUS"


def test_empty_inputs_return_empty_string() -> None:
    assert canonical_brand("") == ""
    assert canonical_brand("   ") == ""
    assert canonical_brand("\xa0") == ""
    assert canonical_brand(None) == ""


def test_unknown_brand_falls_back_to_title_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="portal.services.catalog.brand_normalizer")
    result = canonical_brand("foobarcorp")
    assert result == "Foobarcorp"
    assert any(
        "unknown brand" in rec.message and "foobarcorp" in rec.message.lower()
        for rec in caplog.records
    )


def test_idempotent_known() -> None:
    # Повторное применение к уже каноничному значению ничего не меняет.
    for canon in {"HP", "Pantum", "Konica Minolta", "G&G", "iRU", "OKI",
                  "ASUS", "AMD", "Intel", "NVIDIA", "Palit", "ATI"}:
        assert canonical_brand(canon) == canon
        assert canonical_brand(canonical_brand(canon)) == canon


def test_idempotent_unknown() -> None:
    # Для неизвестного бренда title-case-фоллбэк тоже должен быть стабильным.
    once = canonical_brand("Acme Print Lab")
    assert canonical_brand(once) == once
