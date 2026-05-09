"""Тесты printer/mfu классификаторов 4 общих адаптеров.

Перенесено из QT (`auctions_staging/tests/test_price_loaders_categorization.py`,
43 кейса). Адаптировано под C-PC2-стиль и канонические импорты после Этапа 4
слияния. Запись в БД для printer/mfu пока не подключена — она появится
Этапом 6 (создание таблицы `printers_mfu`); сейчас тестируем только
функции `_classify_*` чистым unit-тестом.
"""

from __future__ import annotations

import pytest

from app.services.price_loaders.merlion import _classify_merlion
from app.services.price_loaders.ocs import _classify_ocs
from app.services.price_loaders.resurs_media import _classify_resursmedia
from app.services.price_loaders.treolan import _classify_treolan


# === MERLION ===
# Реальные значения G3 из прайса Merlion (после prefilter
# g1='Периферия и аксессуары', g2='Принтеры').

@pytest.mark.parametrize(
    "g3, expected",
    [
        ("МФУ лазерные", "mfu"),
        ("Лазерные", "printer"),
        ("МФУ струйные", "mfu"),
        ("Струйные", "printer"),
    ],
)
def test_merlion_classify_printer_mfu(g3: str, expected: str) -> None:
    assert _classify_merlion(g3) == expected


@pytest.mark.parametrize(
    "g3",
    ["Термопринтеры", "Мини-Фото-принтеры", "Матричные", "", "что-то новое"],
)
def test_merlion_classify_ignore(g3: str) -> None:
    assert _classify_merlion(g3) == "ignore"


# === OCS ===
# Реальные пары (B, C) из прайса OCS.

@pytest.mark.parametrize(
    "cat_b, kind_c, expected",
    [
        ("Принтеры", "Принтеры лазерные", "printer"),
        ("Принтеры", "Принтеры струйные", "printer"),
        ("МФУ", "МФУ лазерные", "mfu"),
        ("МФУ", "МФУ струйные", "mfu"),
    ],
)
def test_ocs_classify_printer_mfu(cat_b: str, kind_c: str, expected: str) -> None:
    assert _classify_ocs(cat_b, kind_c) == expected


@pytest.mark.parametrize(
    "cat_b, kind_c",
    [
        ("Принтеры", "Принтеры матричные"),
        ("МФУ", "МФУ матричные"),
        ("Принтеры", "что-то"),
        ("Сканеры", "Сканеры протяжные"),
    ],
)
def test_ocs_classify_ignore(cat_b: str, kind_c: str) -> None:
    assert _classify_ocs(cat_b, kind_c) == "ignore"


# === TREOLAN ===
# Реальные пути из прайса Treolan.

@pytest.mark.parametrize(
    "path, expected",
    [
        (
            "Принтеры, сканеры, МФУ->МФУ->МФУ лазерные/светодиодные/электрографические->Монохромные",
            "mfu",
        ),
        (
            "Принтеры, сканеры, МФУ->Принтеры->Принтеры лазерные/светодиодные/электрографические->Монохромные",
            "printer",
        ),
        (
            "Принтеры, сканеры, МФУ->МФУ->МФУ Струйные->Цветные",
            "mfu",
        ),
        (
            "Принтеры, сканеры, МФУ->Широкоформатные Принтеры/Плоттеры",
            "printer",
        ),
        (
            "Принтеры, сканеры, МФУ->Широкоформатные МФУ",
            "mfu",
        ),
    ],
)
def test_treolan_classify_printer_mfu(path: str, expected: str) -> None:
    assert _classify_treolan(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "Принтеры, сканеры, МФУ->Сканеры->Поточные",
        "Принтеры, сканеры, МФУ->Аксессуары->Картриджи",
        "Серверы->Стоечные->1U",
        "",
    ],
)
def test_treolan_classify_ignore(path: str) -> None:
    assert _classify_treolan(path) == "ignore"


# === RESURS-MEDIA ===
# Имена моделей — паттерны, реально встречающиеся в прайсе priceresurs.xlsx
# (см. адаптер resurs_media.py: классификатор по 1-3 словам имени).

@pytest.mark.parametrize(
    "name, expected",
    [
        ("МФУ Pantum M6500W", "mfu"),
        ("Принтер HP LaserJet Pro M404n", "printer"),
        ("Плоттер Canon imagePROGRAF TX-3000", "printer"),
        ("Фабрика печати Epson L3210", "printer"),
        ("Цветное МФУ Kyocera ECOSYS M5526cdn", "mfu"),
        ("Лазерный принтер Brother HL-L2375DW", "printer"),
        ("Kyocera МФУ TASKalfa 4054ci", "mfu"),
        ("Kyocera Цветное МФУ M5526cdn", "mfu"),
        ("Монохромный принтер HP LJ Pro M404n", "printer"),
    ],
)
def test_resursmedia_classify_printer_mfu(name: str, expected: str) -> None:
    assert _classify_resursmedia(name) == expected


@pytest.mark.parametrize(
    "name",
    [
        "Тумба для принтера Kyocera",
        "Комплект для апгрейда HP",
        "Лоток подачи бумаги Brother",
        "Сканер Canon DR-C225",
        "Автоподатчик Kyocera",
        "Модуль факса Brother",
        "Дополнительный лоток Pantum",
        "",
    ],
)
def test_resursmedia_classify_ignore(name: str) -> None:
    assert _classify_resursmedia(name) == "ignore"
