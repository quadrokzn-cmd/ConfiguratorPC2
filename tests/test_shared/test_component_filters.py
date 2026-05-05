# Тесты эвристик shared/component_filters.is_likely_case_fan (этап 9Г.1).
#
# Логика должна срабатывать при загрузке прайса, чтобы корпусные
# вентиляторы автоматически попадали в каталог с is_hidden=True и не
# смешивались с CPU-кулерами в подборе. Тесты проверяют граничные случаи.

from __future__ import annotations

import pytest

from shared.component_filters import (
    is_likely_cable_or_adapter,
    is_likely_case_fan,
    is_likely_external_storage,
    is_likely_mounting_kit,
    is_likely_non_psu_in_psus,
    is_likely_thermal_paste,
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


    # --- Этап 11.6.2.3.2: PCCooler корпусные серии ---
    def test_pccooler_case_fan_series_detected(self):
        """PCCooler F5R / EF / F3 T120 — корпусные вентиляторы (формат 120x120x25mm)."""
        assert is_likely_case_fan(
            "PCCooler F5R120 (120x120x25mm, 4-pin PWM, 86.73CFM, 32dBA, 2200RPM, Black)"
        ) is True
        assert is_likely_case_fan(
            "PCCooler EF120 ARGB BK (120x120x25mm, 4-pin PWM, ARGB)"
        ) is True
        assert is_likely_case_fan(
            "PCCooler F3 T120 ARGB BK (120x120x25mm, 4-pin PWM, ARGB, 46CFM)"
        ) is True

    def test_pccooler_aio_not_marked(self):
        """PCCooler AIO 360 / 240 — это процессорные кулеры, не корпусные."""
        assert is_likely_case_fan(
            "AIO 360 PCCooler DT360 ARGB Display BK"
        ) is False
        assert is_likely_case_fan(
            "AIO 240 PCCooler DA240 ARGB BK"
        ) is False


class TestIsLikelyThermalPaste:
    def test_thermal_paste_keywords_detected(self):
        """Термопасты и термопрокладки ловятся в любом регистре."""
        assert is_likely_thermal_paste("Термопаста Arctic MX-6 8 г") is True
        assert is_likely_thermal_paste("Thermal paste Noctua NT-H2") is True
        assert is_likely_thermal_paste("Термопрокладка 1.0 мм 100x100") is True
        assert is_likely_thermal_paste("Thermal pad Thermalright Odyssey") is True
        assert is_likely_thermal_paste("Тепловая прокладка Gelid GP-Ultimate") is True

    def test_cpu_cooler_not_marked_as_paste(self):
        """Если упоминается процессор/AIO/радиатор — не помечаем."""
        assert is_likely_thermal_paste(
            "Кулер для процессора DeepCool AK620 + термопаста"
        ) is False
        assert is_likely_thermal_paste(
            "AIO с предустановленной термопастой Cooler Master ML240"
        ) is False

    def test_paste_empty_returns_false(self):
        assert is_likely_thermal_paste(None) is False
        assert is_likely_thermal_paste("") is False
        assert is_likely_thermal_paste("DeepCool AS500 Plus") is False


class TestIsLikelyCableOrAdapter:
    def test_usb_cable_detected(self):
        """USB-кабели и удлинители ловятся."""
        assert is_likely_cable_or_adapter(
            "CROWN кабель CM-CP3.5U32C2 2 порта USB 3.0 в гнездо 3.5\" + Type-C"
        ) is True
        assert is_likely_cable_or_adapter(
            "Удлинитель USB Rexant DX-40 5 м"
        ) is True
        assert is_likely_cable_or_adapter(
            "Adapter USB-A на USB-C, 1 м"
        ) is True

    def test_panel_for_connection_detected(self):
        """Панели подключения (Exegate EG-040..090) ловятся."""
        assert is_likely_cable_or_adapter(
            "Exegate EG-040PSFB панель для подключения 40x40"
        ) is True
        assert is_likely_cable_or_adapter(
            "Front panel USB 3.0 hub разветвитель"
        ) is True

    def test_cooler_with_usb_not_marked(self):
        """CPU-кулер с USB-подсветкой — это всё ещё кулер, не кабель."""
        assert is_likely_cable_or_adapter(
            "AIO 240 DeepCool LS520 USB подсветка"
        ) is False
        assert is_likely_cable_or_adapter(
            "Радиатор AIO 240 с USB разъёмом ARGB"
        ) is False
        assert is_likely_cable_or_adapter(
            "Корпусной вентилятор с USB-кабелем для подключения"
        ) is False  # содержит «вентилятор» — защита блокирует

    def test_cable_empty_returns_false(self):
        assert is_likely_cable_or_adapter(None) is False
        assert is_likely_cable_or_adapter("") is False
        assert is_likely_cable_or_adapter("Noctua NH-D15") is False


class TestIsLikelyMountingKit:
    def test_mounting_kit_detected(self):
        """Mounting kit / back-plate / bracket ловятся."""
        assert is_likely_mounting_kit("Mounting kit для LGA1700") is True
        assert is_likely_mounting_kit(
            "Exegate BKT-0126L бэк-плейт для материнской платы"
        ) is True
        assert is_likely_mounting_kit(
            "Bracket back-plate LGA3647 серверный"
        ) is True
        assert is_likely_mounting_kit(
            "Backplate Noctua NM-i115x кронштейн"
        ) is True

    def test_secure_frame_detected(self):
        """AM5 / Intel secure frame — это рамка, помечается."""
        assert is_likely_mounting_kit("AM5 secure frame чёрный") is True

    def test_cpu_cooler_not_marked_as_mount(self):
        """Сам процессорный кулер с креплением в комплекте — не помечать."""
        assert is_likely_mounting_kit(
            "DeepCool AK620 кулер для процессора с креплением"
        ) is False
        assert is_likely_mounting_kit(
            "Башенный кулер Thermalright с mounting kit AM5"
        ) is False
        assert is_likely_mounting_kit(
            "AIO 360 с back-plate в комплекте"
        ) is False

    def test_mount_empty_returns_false(self):
        assert is_likely_mounting_kit(None) is False
        assert is_likely_mounting_kit("") is False


class TestIsLikelyExternalStorageStub:
    def test_stub_always_returns_false(self):
        """Заготовка под расширение: пока всегда False (см. component_filters.py)."""
        assert is_likely_external_storage(
            "Netac NT01Z9-001T-32BK Z9 1.8\" 1TB USB-C",
            "Netac",
        ) is False
        assert is_likely_external_storage(None) is False
        assert is_likely_external_storage("") is False


class TestIsLikelyNonPsuInPsus:
    """Этап 11.6.2.5.0c: корпуса / кулеры / вентиляторы внутри psus.

    Положительные кейсы взяты из реального дампа локальной БД (выборка по
    SQL из ШАГ 1: model ~* '(корпус|кулер|вентилятор|...)').
    Отрицательные кейсы — тоже из дампа, это настоящие PSU, у которых в
    названии случайно встречается слово «вентилятор» / «корпус» как
    атрибут совместимости.
    """

    def test_leading_korpus_marked(self):
        """Имя начинается с «Корпус …» — это корпус, а не PSU."""
        assert is_likely_non_psu_in_psus(
            "Корпус Chieftec Hawk черный без БП ATX 2x80mm 4x120mm"
        ) is True
        assert is_likely_non_psu_in_psus(
            "Корпус Thermaltake CTE C750 TG ARGB белый без БП ATX"
        ) is True
        assert is_likely_non_psu_in_psus(
            "Корпус XL-ATX Thermaltake AX700 CA-11B-00F1NN-00 черный, без БП"
        ) is True
        assert is_likely_non_psu_in_psus(
            "Корпус Thermaltake The Tower 300 mATX без БП Limestone"
        ) is True

    def test_leading_kuler_or_ventilator_marked(self):
        """Имя начинается с «Кулер» / «Вентилятор» — нон-PSU."""
        assert is_likely_non_psu_in_psus(
            "Кулер DeepCool AN400 (R-AN400-SRWNMN-G)"
        ) is True
        assert is_likely_non_psu_in_psus(
            "Вентилятор в корпус Thermaltake TOUGHFAN 12 [CL-F117-PL12BL-A]"
        ) is True
        assert is_likely_non_psu_in_psus(
            "Кулер для компьютерного корпуса, Thermaltake, CT140 ARGB Sync"
        ) is True
        # «Устройство охлаждения(кулер) Astria 600 ARGB ... 265W» — слово
        # «265W» защитой по wattage НЕ должно прикрыть, потому что строка
        # начинается с «Устройство охлажден».
        assert is_likely_non_psu_in_psus(
            "Устройство охлаждения(кулер) Thermaltake Astria 600 ARGB "
            "Soc-AM5/AM4/1200/1700/1851 черный 4-pin 26.8dB Al 265W Ret"
        ) is True

    def test_masterbox_in_middle_marked(self):
        """«MasterBox» — серия корпусов Cooler Master, не PSU. Имя
        начинается с бренда «Cooler Master MasterBox …» — leading-маркер
        не сработает, но позитивный _NON_PSU_KEYWORDS поймает MasterBox.
        """
        assert is_likely_non_psu_in_psus(
            "Cooler Master MasterBox NR200P V2 белый без БП miniITX 3x120mm",
            manufacturer="Cooler Master",
        ) is True
        assert is_likely_non_psu_in_psus(
            "Cooler Master MasterBox Q300L V2 черный без БП mATX 4x120mm"
        ) is True

    def test_real_psu_with_blok_pitaniya_keyword_protected(self):
        """«Блок питания …» в имени — это явно PSU, даже если рядом стоит
        слово «вентилятор» (характеристика самого БП) или «для корпуса».
        """
        # FSP FSP550-50FS — настоящий ATX-PSU, в имени упоминается «корпус».
        assert is_likely_non_psu_in_psus(
            "Блок питания FSP FSP550-50FS для корпуса Chenbro",
            manufacturer="FSP GROUP",
        ) is False
        # Aerocool VX-700 — настоящий PSU, в имени есть «120mm fan,
        # RGB-подсветка вентилятора».
        assert is_likely_non_psu_in_psus(
            "Блок питания Aerocool VX-700 RGB PLUS (ATX 2.3, 700W, "
            "120mm fan, RGB-подсветка вентилятора) Box",
            manufacturer="Aerocool",
        ) is False
        # Cooler Master Elite NEX W700 — настоящий PSU, в имени есть
        # «120-мм тихий вентилятор» как атрибут.
        assert is_likely_non_psu_in_psus(
            "Блок питания CoolerMaster Elite NEX W700 230V Active PFC КПД "
            "85% 200-240V. Входящий в комплект 120-мм тихий вентилятор",
            manufacturer="Cooler Master",
        ) is False
        # CROWN CM-PS500W — настоящий PSU, в имени есть «длина корпуса 140мм».
        assert is_likely_non_psu_in_psus(
            "CROWN Блок питания CM-PS500W PRO (ATX, 500W, 80 PLUS SILVER, "
            "длина корпуса 140мм, FAN120)",
            manufacturer="Crown",
        ) is False

    def test_real_psu_with_high_wattage_protected(self):
        """Защита по watts: если в имени \\d{3,4}\\s*W ≥200W, это
        PSU, даже если рядом «вентилятор» / «корпус» (например, описание
        размера вентилятора БП или «к корпусам»).
        """
        # Aerocool 400W SX400 — SFX-PSU, в имени «размер вентилятора 80x80 мм»
        assert is_likely_non_psu_in_psus(
            "Aerocool 400W SX400 (Мощность: 400W, форм-фактор: SFX, "
            "размер вентилятора: 80x80 мм)",
            manufacturer="Aerocool",
        ) is False
        # INWIN 400W OEM «к корпусам BK» — настоящий OEM-PSU
        assert is_likely_non_psu_in_psus(
            "INWIN 400W OEM [RB-S400BN1-0 H] к корпусам BK"
        ) is False
        # Zircon ATX 400W — настоящий PSU
        assert is_likely_non_psu_in_psus(
            "Zircon Блок питания ATX 400W ATX-400W Black, Безвентиляторный"
        ) is False

    def test_psu_series_whitelist_protected(self):
        """Серии настоящих PSU (Mirage/NGDP/KYBER/UN/CB/PC/Smart BX) не
        помечаются, даже если в имени есть слабый нон-PSU маркер."""
        # Aerocool Mirage Gold — настоящий PSU из 5.0a
        assert is_likely_non_psu_in_psus(
            "Aerocool Mirage Gold 750W модульный (с вентилятором 140mm)",
            manufacturer="Aerocool",
        ) is False
        # Thermaltake Smart 600W — Smart-серия в whitelist
        assert is_likely_non_psu_in_psus(
            "Thermaltake Smart 600W (с тихим вентилятором)",
            manufacturer="Thermaltake",
        ) is False

    def test_neutral_psu_name_not_marked(self):
        """Обычные PSU без слов корпус/кулер/вентилятор — не помечать."""
        assert is_likely_non_psu_in_psus(
            "ATX 750W 80+ Bronze APFC модульный",
        ) is False
        assert is_likely_non_psu_in_psus(
            "ExeGate UN500 500W ATX",
            manufacturer="ExeGate",
        ) is False
        assert is_likely_non_psu_in_psus(
            "Ginzzu CB650 650W ATX",
            manufacturer="Ginzzu",
        ) is False

    def test_empty_name_returns_false(self):
        """Пустые входы → False."""
        assert is_likely_non_psu_in_psus(None) is False
        assert is_likely_non_psu_in_psus("") is False
        assert is_likely_non_psu_in_psus("   ") is False
        assert is_likely_non_psu_in_psus(None, "Cooler Master") is False
