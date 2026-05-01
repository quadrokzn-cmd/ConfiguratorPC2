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

    # --- Этап 11.6.2.3.1: серии конкретных корпусных вентиляторов ---
    def test_arctic_p_f_bionix_series_detected(self):
        """ARCTIC P12 / P14 / F12 / BioniX 120 — корпусные."""
        assert is_likely_case_fan("ARCTIC P12 Pro PST ACFAN00308A") is True
        assert is_likely_case_fan("ARCTIC P14 Pro A-RGB ACFAN00315A") is True
        assert is_likely_case_fan("ARCTIC F12 PWM PST CO") is True
        assert is_likely_case_fan("ARCTIC BioniX P120 A-RGB") is True

    def test_arctic_freezer_is_cpu_cooler(self):
        """ARCTIC Freezer — это серия CPU-кулеров, НЕ корпусной."""
        assert is_likely_case_fan("ARCTIC Freezer 34 eSports DUO") is False
        assert is_likely_case_fan("ARCTIC Freezer i35 CO") is False

    def test_thermalright_tl_series_detected(self):
        """Thermalright TL-* (TL-C12, TL-D12, TL-K12) — корпусные."""
        assert is_likely_case_fan("Thermalright TL-C12 Black") is True
        assert is_likely_case_fan("Thermalright TL-D14") is True

    def test_thermalright_cpu_lines_not_marked(self):
        """Thermalright Peerless Assassin / Phantom Spirit — CPU."""
        assert is_likely_case_fan(
            "Thermalright Peerless Assassin 120 SE"
        ) is False
        assert is_likely_case_fan("Thermalright Phantom Spirit 120") is False
        # «башен» / «tower» в имени — явный CPU-маркер, блокирует пометку.
        assert is_likely_case_fan(
            "Башенный кулер Thermalright TL-D14 — для радиатора"
        ) is False

    def test_aerocool_case_fan_series_detected(self):
        """Aerocool Frost/Force/Motion/Eclipse 12/14 — корпусные."""
        assert is_likely_case_fan("Aerocool Frost 12 RGB") is True
        assert is_likely_case_fan("Aerocool Force 14") is True
        assert is_likely_case_fan("Aerocool Eclipse 12 PRO ARGB") is True
        assert is_likely_case_fan("Aerocool Motion 14") is True

    def test_aerocool_air_frost_is_cpu_cooler(self):
        """Aerocool Air Frost / Air Force — это CPU-кулеры (не Frost 12/14)."""
        # «Air Frost 2 90мм» — CPU-кулер, не должен быть помечен.
        assert is_likely_case_fan("Cooler Aerocool Air Frost 2 90мм") is False

    def test_be_quiet_wings_detected(self):
        """be quiet! Pure Wings / Silent Wings / Light Wings — корпусные."""
        assert is_likely_case_fan("be quiet! Pure Wings 3 PWM 140") is True
        assert is_likely_case_fan("Silent Wings 4 PWM high-speed") is True
        assert is_likely_case_fan("Light Wings PWM 120 ARGB") is True

    def test_be_quiet_rock_lines_not_marked(self):
        """Pure Rock / Dark Rock / Pure Loop — CPU-кулеры."""
        assert is_likely_case_fan("be quiet! Pure Rock 2") is False
        assert is_likely_case_fan("be quiet! Dark Rock Pro 4") is False
        assert is_likely_case_fan("be quiet! Pure Loop 2 FX 360mm") is False

    def test_cooler_master_case_fan_lines_detected(self):
        """Cooler Master MasterFan / Sickleflow — корпусные."""
        assert is_likely_case_fan("Cooler Master MasterFan MF120 Halo") is True
        assert is_likely_case_fan("Cooler Master Sickleflow 120 ARGB") is True

    def test_cooler_master_hyper_is_cpu(self):
        """Cooler Master Hyper / MasterAir / MasterLiquid — CPU."""
        assert is_likely_case_fan("Cooler Master Hyper 212 RGB Black") is False
        assert is_likely_case_fan("Cooler Master MasterAir MA612") is False
        assert is_likely_case_fan(
            "Cooler Master MasterLiquid 240L Core ARGB"
        ) is False

    def test_noctua_nf_series_detected(self):
        """Noctua NF-A12 / NF-A14 / NF-S12 / NF-P12 / NF-F12 — корпусные."""
        assert is_likely_case_fan("Noctua NF-A12x25 PWM Chromax") is True
        assert is_likely_case_fan("Noctua NF-A14 PWM") is True
        assert is_likely_case_fan("Noctua NF-S12B redux-1200") is True
        assert is_likely_case_fan("Noctua NF-F12 PWM industrialPPC") is True

    def test_noctua_nh_series_is_cpu(self):
        """Noctua NH-* — CPU-кулеры, не корпусные."""
        assert is_likely_case_fan("Noctua NH-D15 chromax.black") is False
        assert is_likely_case_fan("Noctua NH-U12S redux") is False
        assert is_likely_case_fan("Noctua NH-L9i") is False

    def test_size_80_92_200_mm_models_detected(self):
        """Расширение типоразмеров: AF80 / PWM 92 / SP200 — корпусные."""
        assert is_likely_case_fan("PWM 80mm fan ARGB") is True
        assert is_likely_case_fan("Corsair AF200 ELITE") is True
        assert is_likely_case_fan("Arctic SP92 PWM") is True

    def test_system_fan_keyword_detected(self):
        """System fan — синоним case fan."""
        assert is_likely_case_fan("System Fan 120mm PWM") is True


class TestIsLikelyExternalStorageStub:
    def test_stub_always_returns_false(self):
        """Заготовка под расширение: пока всегда False (см. component_filters.py)."""
        assert is_likely_external_storage(
            "Netac NT01Z9-001T-32BK Z9 1.8\" 1TB USB-C",
            "Netac",
        ) is False
        assert is_likely_external_storage(None) is False
        assert is_likely_external_storage("") is False
