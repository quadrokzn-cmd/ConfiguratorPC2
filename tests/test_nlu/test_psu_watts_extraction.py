# Тесты 2026-05-14: извлечение мощности БП из текста запроса.
#
# Проверяем две вещи:
#   1) regex-fallback в pipeline._augment_psu_watts_from_text — детерминированно
#      выдёргивает «550W», «550 Вт», «550 ватт» из произвольного текста.
#   2) request_builder.build пробрасывает overrides.psu_min_watts в
#      BuildRequest.min_psu_watts.

from __future__ import annotations

from portal.services.configurator.nlu import request_builder
from portal.services.configurator.nlu.pipeline import _augment_psu_watts_from_text
from portal.services.configurator.nlu.schema import ParsedRequest


def _empty_parsed() -> ParsedRequest:
    return ParsedRequest(is_empty=False, overrides={})


def test_augment_extracts_550w_from_config_title():
    """Реальный случай собственника: '... 550W)' в скобочной спецификации."""
    parsed = _empty_parsed()
    _augment_psu_watts_from_text(
        parsed,
        "Компьютер (Intel Core i7, 16Gb DDR4, 512 SSD, 550W)",
    )
    assert parsed.overrides.get("psu_min_watts") == 550


def test_augment_extracts_cyrillic_vt():
    """«550 Вт» (русское «Вт») также распознаётся."""
    parsed = _empty_parsed()
    _augment_psu_watts_from_text(parsed, "БП на 650 Вт обязательно")
    assert parsed.overrides.get("psu_min_watts") == 650


def test_augment_extracts_vatt_word():
    """Полное слово «ватт» тоже работает."""
    parsed = _empty_parsed()
    _augment_psu_watts_from_text(parsed, "блок питания 750 ватт")
    assert parsed.overrides.get("psu_min_watts") == 750


def test_augment_respects_parser_value():
    """Если парсер уже извлёк psu_min_watts — regex не перезаписывает."""
    parsed = ParsedRequest(is_empty=False, overrides={"psu_min_watts": 700})
    _augment_psu_watts_from_text(parsed, "БП 550W")
    assert parsed.overrides["psu_min_watts"] == 700  # из парсера


def test_augment_ignores_tdp_like_low_values():
    """65W в тексте (TDP CPU) не считаем мощностью БП."""
    parsed = _empty_parsed()
    _augment_psu_watts_from_text(parsed, "Intel Core i5 65W TDP")
    assert parsed.overrides.get("psu_min_watts") is None


def test_augment_ignores_unrealistic_high_values():
    """5000W — очевидно не БП в офисной сборке, игнорируем."""
    parsed = _empty_parsed()
    _augment_psu_watts_from_text(parsed, "ASIC ферма 5000W")
    assert parsed.overrides.get("psu_min_watts") is None


def test_augment_ignores_when_no_match():
    parsed = _empty_parsed()
    _augment_psu_watts_from_text(parsed, "ПК офисный 16 ГБ DDR4 512 ГБ SSD")
    assert parsed.overrides.get("psu_min_watts") is None


def test_request_builder_passes_psu_min_watts_to_build_request():
    """request_builder должен пробросить overrides.psu_min_watts в BuildRequest."""
    parsed = ParsedRequest(
        is_empty=False,
        overrides={"psu_min_watts": 550},
    )
    req = request_builder.build(parsed)
    assert req.min_psu_watts == 550


def test_request_builder_ignores_invalid_psu_min_watts():
    """Невалидное значение psu_min_watts → None в BuildRequest, без падения."""
    parsed = ParsedRequest(
        is_empty=False,
        overrides={"psu_min_watts": "не число"},
    )
    req = request_builder.build(parsed)
    assert req.min_psu_watts is None
