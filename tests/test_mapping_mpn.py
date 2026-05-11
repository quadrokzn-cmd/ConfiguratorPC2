# Юнит-тесты MPN-логики mapping_service (этап 7.5).
#
# С этапа 7.5 MPN — главный сигнал подозрительности: идентичный MPN
# даёт 100 очков, MPN совпавший до упаковочного суффикса (-RTL, -OEM,
# -1 и т. п.) — 80, разные MPN — 20. Если MPN нет у одной из сторон —
# fallback на старый «fuzzy»-алгоритм с потолком 70.

from __future__ import annotations

from portal.services.databases.mapping_service import (
    _score_breakdown,
    _strip_mpn_suffix,
)


# ---- _strip_mpn_suffix -------------------------------------------------


def test_strip_mpn_suffix_dash_digit():
    assert _strip_mpn_suffix("PM-500ATX-1") == "PM-500ATX"
    assert _strip_mpn_suffix("ABC-2") == "ABC"


def test_strip_mpn_suffix_rtl_oem_box():
    assert _strip_mpn_suffix("CT500BX500SSD1-RTL") == "CT500BX500SSD1"
    assert _strip_mpn_suffix("CT500BX500SSD1-OEM") == "CT500BX500SSD1"
    assert _strip_mpn_suffix("CT500BX500SSD1-TRAY") == "CT500BX500SSD1"


def test_strip_mpn_suffix_slash_r():
    assert _strip_mpn_suffix("CM8071512400F/R") == "CM8071512400F"
    assert _strip_mpn_suffix("CM8071512400F/OEM") == "CM8071512400F"


def test_strip_mpn_suffix_parens():
    assert _strip_mpn_suffix("CT500BX500SSD1 (OEM)") == "CT500BX500SSD1"
    assert _strip_mpn_suffix("CT500BX500SSD1 (RTL)") == "CT500BX500SSD1"


def test_strip_mpn_suffix_space():
    assert _strip_mpn_suffix("CT500BX500SSD1 BOX") == "CT500BX500SSD1"
    assert _strip_mpn_suffix("CT500BX500SSD1 OEM") == "CT500BX500SSD1"


def test_strip_mpn_suffix_combined():
    """Комбинации суффиксов снимаются за несколько проходов."""
    assert _strip_mpn_suffix("CT500BX500SSD1 (RTL) OEM") == "CT500BX500SSD1"


def test_strip_mpn_suffix_upper_and_trim():
    """Приводит к UPPER и убирает пробелы по краям."""
    assert _strip_mpn_suffix("  ct500bx500ssd1-rtl  ") == "CT500BX500SSD1"


def test_strip_mpn_suffix_no_suffix_returns_as_is():
    """Если суффикса нет — возвращаем UPPER без изменений."""
    assert _strip_mpn_suffix("CT500BX500SSD1") == "CT500BX500SSD1"


def test_strip_mpn_suffix_empty():
    assert _strip_mpn_suffix("") == ""
    assert _strip_mpn_suffix(None) == ""  # type: ignore[arg-type]


# ---- MPN-ветка _score_breakdown ---------------------------------------


def _cand(model: str, *, sku: str, manufacturer: str = "Crucial") -> dict:
    return {"model": model, "manufacturer": manufacturer, "sku": sku}


def test_mpn_identical():
    """row.mpn == cand.sku → 100 «MPN идентичен»."""
    score, reason = _score_breakdown(
        raw_name="Crucial SSD BX500 500GB",
        brand="Crucial",
        cand=_cand("Crucial SSD BX500 500GB", sku="CT500BX500SSD1"),
        category="storage",
        mpn="CT500BX500SSD1",
    )
    assert score == 100
    assert reason == "MPN идентичен"


def test_mpn_differs_suffix_dash_1():
    """«ABC-1» vs «ABC» — совпадают после снятия «-1» → 80."""
    score, reason = _score_breakdown(
        raw_name="Some SSD",
        brand="Brand",
        cand=_cand("Some SSD", sku="ABC", manufacturer="Brand"),
        category="storage",
        mpn="ABC-1",
    )
    assert score == 80
    assert reason == "MPN совпадает с точностью до суффикса"


def test_mpn_differs_suffix_rtl():
    """«CT500BX500SSD1-RTL» vs «CT500BX500SSD1» → 80."""
    score, reason = _score_breakdown(
        raw_name="Crucial BX500 500GB",
        brand="Crucial",
        cand=_cand("Crucial BX500 500GB", sku="CT500BX500SSD1"),
        category="storage",
        mpn="CT500BX500SSD1-RTL",
    )
    assert score == 80
    assert "суффикс" in reason


def test_mpn_differs_suffix_oem():
    """«ABC OEM» vs «ABC BOX» — оба теряют упаковочный суффикс → 80."""
    score, reason = _score_breakdown(
        raw_name="Some component",
        brand="Brand",
        cand=_cand("Some component", sku="ABC BOX", manufacturer="Brand"),
        category="storage",
        mpn="ABC OEM",
    )
    assert score == 80
    assert "суффикс" in reason


def test_mpn_differs_in_middle():
    """«CT500BX500SSD1» vs «CT240BX500SSD1» — MPN различаются в середине → 20.

    Это продакшен-случай: разные объёмы одной серии Crucial BX500 имеют
    разные MPN (CT500 vs CT240), но по названию и бренду почти
    идентичны. До этапа 7.5 алгоритм давал 100, теперь — 20.
    """
    score, reason = _score_breakdown(
        raw_name="Crucial SSD BX500 500GB",
        brand="Crucial",
        cand=_cand("Crucial SSD BX500 240GB", sku="CT240BX500SSD1"),
        category="storage",
        mpn="CT500BX500SSD1",
    )
    assert score == 20
    assert reason == "MPN различается"


def test_mpn_completely_different():
    """«AAA» vs «BBB» → 20 независимо от совпадения бренда/имени."""
    score, reason = _score_breakdown(
        raw_name="Same name",
        brand="Brand",
        cand=_cand("Same name", sku="BBB", manufacturer="Brand"),
        category="storage",
        mpn="AAA",
    )
    assert score == 20
    assert reason == "MPN различается"


def test_mpn_missing_fallback():
    """mpn=None → fallback на старую логику, score ≤ 70.

    Идентичные имена при отсутствии MPN раньше давали 100, теперь
    fallback капается до _SCORE_FALLBACK_CAP = 70.
    """
    score, reason = _score_breakdown(
        raw_name="Intel Core i5-12400",
        brand="Intel",
        cand=_cand("Intel Core i5-12400", sku="CM8071512400F",
                   manufacturer="Intel"),
        category="cpu",
        # mpn не передан → None
    )
    assert score <= 70
    # Fallback помечается префиксом, чтобы админ видел, что MPN нет.
    assert reason.startswith("без MPN:")


def test_mpn_missing_in_candidate_fallback():
    """У row есть MPN, у кандидата — нет (sku=None/пусто) → fallback."""
    score, reason = _score_breakdown(
        raw_name="Intel Core i5-12400",
        brand="Intel",
        cand=_cand("Intel Core i5-12400", sku="", manufacturer="Intel"),
        category="cpu",
        mpn="CM8071512400F",
    )
    assert score <= 70
    assert reason.startswith("без MPN:")


def test_crucial_bx500_production_case():
    """Точный продакшен-кейс: row с MPN «CT500BX500SSD1» vs кандидат со
    sku «CT240BX500SSD1». До этапа 7.5 эта пара давала score=100 и
    оседала в «подозрительных». Теперь — 20 «MPN различается», пара
    уходит в «вероятно новые»."""
    a = ("Crucial SSD Disk BX500 500GB SATA 2.5 7mm SSD "
         "(540 MB/s Read 500 MB/s Write), 1 Year Warranty OEM")
    b = ("Crucial SSD Disk BX500 240GB SATA 2.5 7mm SSD "
         "(540 MB/s Read 500 MB/s Write), 1 Year Warranty OEM")
    score, _reason = _score_breakdown(
        raw_name=a,
        brand="Crucial",
        cand=_cand(b, sku="CT240BX500SSD1"),
        category="storage",
        mpn="CT500BX500SSD1",
    )
    assert score == 20


def test_mpn_case_insensitive():
    """Сравнение MPN нечувствительно к регистру."""
    score, _ = _score_breakdown(
        raw_name="X",
        brand="B",
        cand=_cand("X", sku="ct500bx500ssd1", manufacturer="B"),
        category="storage",
        mpn="CT500BX500SSD1",
    )
    assert score == 100
