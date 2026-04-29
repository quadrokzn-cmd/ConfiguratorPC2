# Тесты эвристик shared/component_filters.is_likely_case_fan (этап 9Г.1).
#
# Логика должна срабатывать при загрузке прайса, чтобы корпусные
# вентиляторы автоматически попадали в каталог с is_hidden=True и не
# смешивались с CPU-кулерами в подборе. Тесты проверяют граничные случаи.

from __future__ import annotations

import pytest

from shared.component_filters import (
    is_likely_case_fan,
    is_likely_external_storage,
)


class TestIsLikelyCaseFan:
    def test_case_fan_detected_by_name_keywords(self):
        """Явные ключевые слова про корпус ловятся в любом регистре."""
        assert is_likely_case_fan("Корпусной вентилятор 120 мм") is True
        assert is_likely_case_fan("корпусные вентиляторы DeepCool") is True
        assert is_likely_case_fan("Case Fan 140mm RGB") is True
        assert is_likely_case_fan("CASE FAN 120") is True
        assert is_likely_case_fan("Chassis Fan 92mm") is True
        assert is_likely_case_fan("Вентилятор для корпуса 200мм") is True

    def test_cpu_cooler_not_marked_as_case_fan(self):
        """CPU-кулеры с явными маркерами не должны помечаться."""
        assert is_likely_case_fan("CPU кулер DeepCool AK620") is False
        assert is_likely_case_fan("CPU Cooler Noctua NH-D15") is False
        assert is_likely_case_fan("Tower cooler Thermalright Peerless Assassin") is False
        assert is_likely_case_fan("Башенный кулер процессора 240W") is False
        assert is_likely_case_fan("Кулер для процессора Intel Box") is False

    def test_pwm_fan_with_size_120mm_detected(self):
        """Модельные паттерны AF/SP/PWM 120/140 — корпусные вентиляторы."""
        assert is_likely_case_fan("Corsair AF120 ELITE") is True
        assert is_likely_case_fan("Arctic SP140 PWM PST") is True
        assert is_likely_case_fan("Fan 120mm PWM ARGB") is True
        assert is_likely_case_fan("PWM 140 black") is True

    def test_aio_radiator_not_marked(self):
        """AIO/жидкостное охлаждение — это CPU-кулер, не корпусной вентилятор."""
        assert is_likely_case_fan("AIO 240mm Radiator NZXT Kraken") is False
        assert is_likely_case_fan("Жидкостное охлаждение 360mm") is False
        assert is_likely_case_fan("Liquid cooler 280mm Corsair") is False
        assert is_likely_case_fan("Water cooling kit 120") is False
        assert is_likely_case_fan("Радиатор 240mm с вентиляторами PWM 120") is False

    def test_unknown_returns_false_safely(self):
        """Пустые / None входы не падают, возвращают False."""
        assert is_likely_case_fan(None) is False
        assert is_likely_case_fan("") is False
        assert is_likely_case_fan("   ") is False
        assert is_likely_case_fan(None, None, None) is False
        assert is_likely_case_fan("", "Corsair") is False

    def test_manufacturer_contributes_to_match(self):
        """Если бренд явный (например, для Be Quiet Pure Wings) — без CPU-маркеров
        и при наличии слова 'fan' в имени, пометка остаётся 'корпусной'."""
        assert is_likely_case_fan(
            "Pure Wings 2 PWM 120", manufacturer="be quiet!",
        ) is True

    def test_neutral_name_returns_false(self):
        """Имя без признаков fan/вентилятор — не помечаем."""
        assert is_likely_case_fan("DeepCool AS500 Plus", "DeepCool") is False
        assert is_likely_case_fan("Noctua NH-U12S redux") is False


class TestIsLikelyExternalStorageStub:
    def test_stub_always_returns_false(self):
        """Заготовка под расширение: пока всегда False (см. component_filters.py)."""
        assert is_likely_external_storage(
            "Netac NT01Z9-001T-32BK Z9 1.8\" 1TB USB-C",
            "Netac",
        ) is False
        assert is_likely_external_storage(None) is False
        assert is_likely_external_storage("") is False
