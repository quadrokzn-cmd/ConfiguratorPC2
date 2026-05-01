# Тесты case-детекторов в shared/component_filters (этап 11.6.2.4.0).
#
# В отличие от cooler-детекторов, в категории cases мусора оказалось
# крайне мало (1 реальный кейс на 1876 видимых cases в локальной БД —
# id=1065 «Устройство охлаждения(кулер) Aerocool Core Plus 120мм»).
# Поэтому 5 эвристик здесь работают в режиме инверсии: маркер мусора
# блокируется защитным слоем _CASE_HOUSING_HINTS, чтобы корпуса с
# предустановленными аксессуарами (Lian Li SUP01X с PCIe Riser Cable,
# Lian Li A3-mATX с Bottom Dust Filter, AIC J2024 JBOD-шасси) не
# становились ложно-положительными срабатываниями.
#
# Каждый из 5 классов проверяет:
#   * 5–10 положительных кейсов («это настоящий мусор»);
#   * 5+ отрицательных («это полноценный корпус, не трогать»);
#   * защитные слои (housing-маркеры в имени блокируют детектор).
#
# Если кто-то из вендоров пришлёт новый формат raw_name, тесты должны
# подсветить это до того, как массовая переклассификация заденет
# валидные корпуса.

from __future__ import annotations

import pytest

from shared.component_filters import (
    is_likely_case_panel_or_filter,
    is_likely_drive_cage,
    is_likely_gpu_support_bracket,
    is_likely_loose_case_fan,
    is_likely_pcie_riser,
)


class TestIsLikelyLooseCaseFan:
    def test_real_trash_from_db_id_1065(self):
        """id=1065 в kvadro_tech — реальный кейс для этого детектора."""
        assert is_likely_loose_case_fan(
            "Устройство охлаждения(кулер) Aerocool Core Plus,  120мм, Ret",
        ) is True

    def test_explicit_case_fan_keywords(self):
        assert is_likely_loose_case_fan("Корпусной вентилятор 120 мм") is True
        assert is_likely_loose_case_fan("Case fan 140mm RGB") is True
        assert is_likely_loose_case_fan("Chassis fan 92mm be quiet!") is True
        assert is_likely_loose_case_fan("System fan 200mm Noctua") is True

    def test_loose_cooler_at_start(self):
        """Самостоятельный товар «Кулер ...» / «Вентилятор ...» — мусор."""
        assert is_likely_loose_case_fan("Кулер Noctua NF-A12 PWM") is True
        assert is_likely_loose_case_fan("Вентилятор Arctic P14 PWM PST") is True

    def test_known_case_fan_series_ported_from_cooler_logic(self):
        """Серии корпусных вентиляторов ловятся внутри cases тоже."""
        assert is_likely_loose_case_fan("Aerocool Frost 12 ARGB") is True
        assert is_likely_loose_case_fan("be quiet! Pure Wings 2 140mm") is True
        assert is_likely_loose_case_fan("Cooler Master MasterFan SF120R") is True

    def test_full_case_with_fans_not_marked(self):
        """Полноценный корпус с предустановленными вентиляторами — НЕ мусор."""
        assert is_likely_loose_case_fan(
            "Корпус Deepcool CG530 4F Mid-Tower с 4×120mm ARGB PWM"
        ) is False
        assert is_likely_loose_case_fan(
            "корпус MidiTower Powerman 4U rack-mount, Front fan 12cm"
        ) is False
        assert is_likely_loose_case_fan(
            "PC Case ATX Mid-Tower 3x120mm ARGB"
        ) is False

    def test_empty_input_safe(self):
        assert is_likely_loose_case_fan(None) is False
        assert is_likely_loose_case_fan("") is False
        assert is_likely_loose_case_fan("   ") is False


class TestIsLikelyDriveCage:
    def test_loose_drive_cage_marked(self):
        assert is_likely_drive_cage(
            'Корзина для HDD 3.5" mobile rack 5.25 to 4×3.5'
        ) is True
        assert is_likely_drive_cage("HDD cage 4-bay 3.5\" SATA") is True
        assert is_likely_drive_cage("Mobile rack hot-swap backplane") is True
        assert is_likely_drive_cage("Салазки для жёсткого диска 3.5") is True
        assert is_likely_drive_cage("HDD enclosure 2.5\" external") is True

    def test_server_jbod_chassis_not_marked(self):
        """AIC JBOD chassis имеет «hot-swap bays» в названии — но это корпус."""
        assert is_likely_drive_cage(
            'AIC J2024 2U 24x 2.5" hot-swap bays JBOD chassis 549W'
        ) is False
        assert is_likely_drive_cage(
            'AIC RSC-4BT 4U 36x 3.5" hot-swap bays rack-mount 1200W'
        ) is False
        assert is_likely_drive_cage(
            'InWin IW-RS436 hot-swap module ATX server case'
        ) is False

    def test_regular_pc_case_not_marked(self):
        assert is_likely_drive_cage(
            "Корпус Deepcool MATREXX 55 Mid-Tower ATX"
        ) is False

    def test_empty_input_safe(self):
        assert is_likely_drive_cage(None) is False
        assert is_likely_drive_cage("") is False


class TestIsLikelyPcieRiser:
    def test_loose_riser_cable_marked(self):
        assert is_likely_pcie_riser("PCIe 4.0 riser cable 200mm") is True
        assert is_likely_pcie_riser("PCI-E extender cable 90 degrees") is True
        assert is_likely_pcie_riser(
            "Райзер-кабель Lian Li PCIe 4.0 PW-PCI4-AR4N",
        ) is True
        assert is_likely_pcie_riser("Riser card 1U PCIe x16") is True
        assert is_likely_pcie_riser("PCIe extension 200mm vertical GPU mount") is True

    def test_case_with_riser_in_box_not_marked(self):
        """Lian Li SUP01X — корпус с PCIe Riser Cable в комплекте — НЕ мусор."""
        assert is_likely_pcie_riser(
            "Lian Li Корпус SUP01X / 3 x 120mm PWM Fan / "
            "PCIe4.0 Riser Cable / Mid-Tower"
        ) is False
        assert is_likely_pcie_riser(
            "Phanteks Eclipse G500A ATX case with vertical GPU riser kit"
        ) is False

    def test_empty_input_safe(self):
        assert is_likely_pcie_riser(None) is False
        assert is_likely_pcie_riser("") is False


class TestIsLikelyCasePanelOrFilter:
    def test_replacement_panel_marked(self):
        assert is_likely_case_panel_or_filter(
            "Replacement tempered glass side panel for NZXT H510"
        ) is True
        assert is_likely_case_panel_or_filter(
            "Spare panel for Fractal Design Meshify 2"
        ) is True
        assert is_likely_case_panel_or_filter(
            "Сменная боковая панель из закалённого стекла"
        ) is True
        assert is_likely_case_panel_or_filter(
            "Запасная панель для корпуса"
        ) is True

    def test_standalone_dust_filter(self):
        assert is_likely_case_panel_or_filter(
            "Standalone dust filter 120mm 4-pack"
        ) is True
        assert is_likely_case_panel_or_filter(
            "Отдельный пылевой фильтр 140mm"
        ) is True

    def test_full_case_with_glass_panel_not_marked(self):
        """JONSBO MOD 5 — корпус с tempered glass-панелью, не сменная панель."""
        assert is_likely_case_panel_or_filter(
            'Корпус компьютерный ATX/ JONSBO MOD 5, Black, 4mm tempered glass panel'
        ) is False
        assert is_likely_case_panel_or_filter(
            "Lian Li Корпус A3-mATX TG Black / Tempered Glass Edition / Bottom Dust Filter"
        ) is False
        assert is_likely_case_panel_or_filter(
            "Deepcool MACUBE 110 BK mATX, боковая панель из стекла"
        ) is False

    def test_empty_input_safe(self):
        assert is_likely_case_panel_or_filter(None) is False
        assert is_likely_case_panel_or_filter("") is False


class TestIsLikelyGpuSupportBracket:
    def test_loose_gpu_bracket_marked(self):
        assert is_likely_gpu_support_bracket(
            "GPU support bracket adjustable RGB"
        ) is True
        assert is_likely_gpu_support_bracket(
            "Graphics card holder anti-sag"
        ) is True
        assert is_likely_gpu_support_bracket(
            "Антипровисная стойка для видеокарты"
        ) is True
        assert is_likely_gpu_support_bracket(
            "Кронштейн для видеокарты GPU brace"
        ) is True
        assert is_likely_gpu_support_bracket(
            "Sag bracket video card holder"
        ) is True

    def test_full_case_not_marked(self):
        """Корпуса с интегрированной анти-саг-системой — НЕ мусор."""
        assert is_likely_gpu_support_bracket(
            "Корпус ПК с антипровисной системой видеокарты ATX Mid-Tower"
        ) is False

    def test_empty_input_safe(self):
        assert is_likely_gpu_support_bracket(None) is False
        assert is_likely_gpu_support_bracket("") is False


class TestHousingHintBlocksAllDetectors:
    """Перекрёстная проверка: все 5 детекторов уважают housing-защиту.

    Если в имени присутствуют явные case-маркеры (midi tower / ATX case /
    «корпус ПК»), ни один из детекторов мусора не должен сработать.
    """

    @pytest.mark.parametrize("name", [
        "Корпус компьютерный ATX Mid-Tower с PCIe riser cable в комплекте",
        "PC case Mid-Tower with HDD cage and tempered glass panel",
        "Корпус ПК с GPU support bracket и mobile rack",
        "AIC J2024 JBOD chassis 24x hot-swap bays",
        "InWin IW-RS436 4U rack-mount server case ATX",
    ])
    def test_no_detector_fires_when_housing_hint_present(self, name):
        assert is_likely_loose_case_fan(name) is False
        assert is_likely_drive_cage(name) is False
        assert is_likely_pcie_riser(name) is False
        assert is_likely_case_panel_or_filter(name) is False
        assert is_likely_gpu_support_bracket(name) is False
