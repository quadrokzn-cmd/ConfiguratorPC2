# Тест на backfill video_outputs с source='derived_from_name' (Этап 2.5В).
#
# Проверяем только normalize_video_outputs (чистая функция) — поведение БД
# покрыто интеграционными сценариями. Убеждаемся, что для распространённых
# форматов в прайсе получаем ожидаемую нормализованную строку.

import pytest

from app.services.enrichment.claude_code.derive import normalize_video_outputs


@pytest.mark.parametrize("raw,expected", [
    # Типичные форматы из прайсов Merlion/Treolan:
    ("HDMI, DP*3", "1xHDMI+3xDP"),
    ("HDMI*1, DP*3", "1xHDMI+3xDP"),
    ("1xHDMI+1xDVI-D+1xVGA", "1xHDMI+1xDVI-D+1xVGA"),
    ("3 x DisplayPort 2.1, 1 x HDMI 2.1", "3xDP2.1+1xHDMI2.1"),
    ("HDMI 2.1a, 3x DP 1.4a", "1xHDMI2.1+3xDP1.4"),
])
def test_normalize_video_outputs_prod_samples(raw, expected):
    assert normalize_video_outputs(raw) == expected


def test_normalize_empty_returns_none():
    assert normalize_video_outputs("") is None
    assert normalize_video_outputs(None) is None
    assert normalize_video_outputs("no ports mentioned") is None
