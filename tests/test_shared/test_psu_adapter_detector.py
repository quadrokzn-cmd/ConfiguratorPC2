# Тесты детектора is_likely_psu_adapter (этап 11.6.2.5.0b).
#
# По итогам аудита 5.0a в bucket manufacturer='unknown' в категории psu
# обнаружились ~70 не-PSU позиций (адаптеры/POE/charger/USB-PD/dock).
# Детектор должен ловить их и при этом НЕ задевать ~150 настоящих
# ATX/SFX-PSU без manufacturer-маппинга.
#
# Тесты построены на реальных raw_name из БД (этап 5.0a, ids указаны
# рядом с каждым кейсом). Положительные срабатывания — id 252, 274 и
# выборка Gembird/KS-is/ORIENT/BURO/Бастион. Отрицательные — id 153
# Thermaltake Smart BX1, id 212 Zalman ZM700-LXII, id 731-747 CBR ATX,
# id 921 ExeGate UN450, id 1267-1277 Ginzzu CB/PB/PC, id 1452-1463 XPG
# KYBER/CORE REACTOR, id 1066 1STPLAYER NGDP, id 1482-1492 «Повреждение
# упаковки».

from __future__ import annotations

import pytest

from shared.component_filters import is_likely_psu_adapter


class TestIsLikelyPsuAdapterPositive:
    """Кейсы, которые ОБЯЗАНЫ помечаться как адаптер (is_hidden=true)."""

    def test_id_274_fsp_fsp040_notebook_charger(self):
        """id=274 FSP GROUP FSP040-RHBN3 — ноутбучный 40W AC-DC адаптер."""
        assert is_likely_psu_adapter(
            "Адаптер питания FSP FSP040-RHBN3    AC-DC 40W Adapter, "
            "Вход 100Vac~240Vac, разъем C6 (для ноутбучного кабеля), "
            "Кабель 1500мм, Выход 12V, 5.5*2.5*7.5 - 180° (прямой штеккер)",
            "FSP GROUP",
        ) is True

    def test_id_252_ubiquiti_poe_injector(self):
        """id=252 Ubiquiti POE-15-12W — Passive PoE инжектор."""
        assert is_likely_psu_adapter(
            "Блок питания для сетевого устройства Ubiquiti POE-15-12W "
            "блок питания 15 В 0.8 А Passive PoE (в комплекте вилка US, "
            "Переходник на евровилку US-EU арт. 104404)",
            "Ubiquiti",
        ) is True

    def test_gembird_npa_ac1d_universal_adapter(self):
        """id=638 Gembird NPA-AC1D — универсальный адаптер 90Вт."""
        assert is_likely_psu_adapter(
            "Gembird NPA-AC1D { Адаптер-автомат питания, 220В "
            "Универсальный для ноутбуков 90Вт}",
            "unknown",
        ) is True

    def test_gembird_npa_dc_pd(self):
        """id=665 Gembird NPA-DC10 — PD3.0 65Вт зарядка USB-C."""
        assert is_likely_psu_adapter(
            "Gembird Блок питания для ноутбуков PD3.0, 65Вт, "
            "штекер Type-C (NPA-DC10)",
            "unknown",
        ) is True

    def test_ks_is_universal_adapter(self):
        """id=669 KS-is KS-256 — универсальный адаптер 90Вт."""
        assert is_likely_psu_adapter(
            "KS-is KS-256 Универсальный адаптер питания от сети "
            "Ledoj KS-256, 90 Вт, LED дисплей",
            "unknown",
        ) is True

    def test_ks_is_pd_usb_c_charger(self):
        """id=684 KS-is KS-503 — PD USB-C 130Вт. Защита 200W не должна
        срабатывать (130<200), бренд-серия KS-is решает."""
        assert is_likely_psu_adapter(
            "KS-is KS-503 Блок питания универсальный PD USB-C 130Вт",
            "unknown",
        ) is True

    def test_orient_pu_c45w_charger(self):
        """id=696 ORIENT PU-C45W — сетевое зарядное QC3.0+PD 45W."""
        assert is_likely_psu_adapter(
            "ORIENT PU-C45W, Сетевое зарядное устройство с функцией "
            "быстрой зарядки, мощность 45Вт, Quick Charge 3.0+Power Delivery",
            "unknown",
        ) is True

    def test_orient_sap_dc_psu(self):
        """id=702 ORIENT SAP-48B — DC 48V/3A блок-розетка."""
        assert is_likely_psu_adapter(
            "ORIENT SAP-48B, Блок питания DC 48V, 3.0A, защита от КЗ "
            "и перегрузки, вилка с кабелем",
            "unknown",
        ) is True

    def test_buro_bum_notebook_charger(self):
        """id=694 BURO BUM-1157L90 — ноутбучный автомат 90W."""
        assert is_likely_psu_adapter(
            "BURO Блок питания для ноутбука автоматический 90W "
            "18.5V-20V 11-connectors 4.62A (BUM-1157L90)",
            "unknown",
        ) is True

    def test_buro_bu_pa_apple_plug(self):
        """id=706 Buro BU-PA01-B — переходник для Apple."""
        assert is_likely_psu_adapter(
            "Адаптер-переходник Buro  BU-PA01-B (1 розетка) черный",
            "unknown",
        ) is True

    def test_gopower_apple_adapter(self):
        """id=705 GOPOWER KT-168 — переходник Apple."""
        assert is_likely_psu_adapter(
            "Адаптер переходник 00-00025058 GOPOWER / ISA KT-168",
            "unknown",
        ) is True

    def test_bastion_rapan_security_psu(self):
        """id=711 Бастион РАПАН-20 — ББП для охранки 12V/2A с АКБ."""
        assert is_likely_psu_adapter(
            "ББП Бастион РАПАН-20 Li-ion, питание 12 В, 2А, "
            "3 отсека под Li-ion ячейки 18650 или 26650",
            "unknown",
        ) is True


class TestIsLikelyPsuAdapterNegativeAtxSeries:
    """Кейсы настоящих ATX/SFX PSU, которые НЕ должны помечаться."""

    def test_id_153_thermaltake_smart_bx1(self):
        """id=153 Thermaltake Smart BX1 — 750W ATX PSU."""
        assert is_likely_psu_adapter(
            "Блок питания Thermaltake ATX 750W Smart BX1 RGB 80+ bronze "
            "(20+4pin) APFC 120mm fan color LED 8xSATA RTL",
            "Thermaltake",
        ) is False

    def test_id_212_zalman_zm700_lxii(self):
        """id=212 Zalman ZM700-LXII — 700W ATX PSU."""
        assert is_likely_psu_adapter(
            "Блок питания ZALMAN ATX 700W ZM700-LXII",
            "Zalman",
        ) is False

    def test_id_731_cbr_atx_400w(self):
        """id=731 CBR ATX 400W — настоящий ATX PSU."""
        assert is_likely_psu_adapter(
            "Блок питания CBR ATX 400W, 8cm fan, 24pin/1*4pin/1*IDE/2*SATA, "
            "кабель питания 1.2м,черный [PSU-ATX400-08EC] OEM",
            "unknown",
        ) is False

    def test_id_745_cbr_atx_850w_modular(self):
        """id=745 CBR ATX 850W Full Modular Gold — настоящий PSU."""
        assert is_likely_psu_adapter(
            "Блок питания CBR ATX 850W 80+ Gold, Full Modular, DC-DC, "
            "APFC, 24pin, 1*PCIE5.1(12+4pin), 2*8-pin(4+4P), 3*6+2pin",
            "unknown",
        ) is False

    def test_id_921_exegate_un450(self):
        """id=921 ExeGate UN450 — настоящий 450W ATX PSU."""
        assert is_likely_psu_adapter(
            "Exegate EX244554RUS-PC Блок питания 450W ExeGate UN450 "
            "+ кабель питания (ATX, 12cm fan, 24+4pin, 6pin PCI-E, "
            "3xSATA, 2xIDE)",
            "unknown",
        ) is False

    def test_id_1267_ginzzu_cb450(self):
        """id=1267 Ginzzu CB450 — PSU без явного 'ATX' / '80+',
        ловится защитой 3 (Ginzzu CB/PB/PC/MC серии)."""
        assert is_likely_psu_adapter(
            "Ginzzu CB450 12CM black,24+4p,PCI-E, 3*SATA, 2*IDE,"
            "оплетка MB, кабель питания",
            "unknown",
        ) is False

    def test_id_1273_ginzzu_pb450_oem(self):
        """id=1273 Ginzzu PB450 80+ OEM — PSU."""
        assert is_likely_psu_adapter(
            "Ginzzu PB450 12CM 80+ black,APFC,20+4p,1 PCI-E(6+2), "
            "4*SATA, 2*IDE, OEM",
            "unknown",
        ) is False

    def test_id_1452_xpg_kyber_650(self):
        """id=1452 XPG KYBER 650 — настоящий ATX 3.0 650W PSU."""
        assert is_likely_psu_adapter(
            "XPG KYBER 650 (ATX 2.52, 650W, PWM 120mm fan, Active PFC, "
            "DC to DC, Full-Bridge & LLC converter,  80+ GOLD) RET",
            "unknown",
        ) is False

    def test_id_1455_xpg_core_reactor_ii(self):
        """id=1455 XPG CORE REACTOR II VE 650 — настоящий PSU."""
        assert is_likely_psu_adapter(
            "XPG CORE REACTOR II VE 650 (ATX 3.1, 650W, Full Modular, "
            "PWM 120mm FDB fan,  Active PFC, DC to DC, 80+ GOLD,) RET",
            "unknown",
        ) is False

    def test_id_1066_1stplayer_ngdp_1300w(self):
        """id=1066 1STPLAYER NGDP Platinum 1300W — настоящий ATX 3.0."""
        assert is_likely_psu_adapter(
            "1STPLAYER NGDP Platinum 1300W White / ATX3.0, APFC, "
            "80 PLUS Platinum, SR + LLC + DC-DC, 120mm fan, full modular",
            "unknown",
        ) is False


class TestIsLikelyPsuAdapterNegativeDamagedPackaging:
    """Префикс «Повреждение упаковки» / «Поврежденная упаковка» —
    это валидные PSU с косметическим браком, должны оставаться."""

    def test_id_1482_damaged_cbr_atx_600w(self):
        """id=1482 Повреждение упаковки + CBR ATX 600W."""
        assert is_likely_psu_adapter(
            "Повреждение упраковки Блок питания CBR ATX 600W 80+ "
            "Bronze, DC-DC, APFC, 24pin, 1*8-pin(4+4P), 2*6+2pin",
            "unknown",
        ) is False

    def test_id_1483_damaged_aerocool_vx_550(self):
        """id=1483 Повреждение упаковки + Aerocool 550W VX 550 PLUS."""
        assert is_likely_psu_adapter(
            "Повреждение упаковки Aerocool 550W VX 550 PLUS RTL "
            "(замена 11082401)",
            "unknown",
        ) is False

    def test_id_1485_damaged_ginzzu_pc700(self):
        """id=1485 Повреждение упаковки + Ginzzu PC700 80+."""
        assert is_likely_psu_adapter(
            "Повреждение упаковки Ginzzu PC700 14CM(Red) 80+ black,"
            "APFC,24+4p,2 PCI-E(6+2), 7*SATA, 4*IDE",
            "unknown",
        ) is False

    def test_id_1489_damaged_zalman_xe(self):
        """id=1489 Поврежденная упаковка + Zalman XE ZM600-XE II."""
        assert is_likely_psu_adapter(
            "Поврежденная упаковка Zalman XE ZM600-XE II Wattbit 600 83+",
            "unknown",
        ) is False

    def test_id_1495_damaged_zalman_atx400(self):
        """id=1495 + Zalman ZM400 ATX, 20+4 pin."""
        assert is_likely_psu_adapter(
            "Повреждение упаковки Zalman XE ZM400-XEII Wattbit 83+ "
            "(ATX, 20+4 pin, 120mm fan, 4xSATA) (ZM400-XEII)",
            "unknown",
        ) is False


class TestIsLikelyPsuAdapterDefenseLayers:
    """Юнит-проверки защитных слоёв в изоляции."""

    def test_atx12v_form_factor_protects(self):
        """ATX12V (без пробела/границы) тоже считается form-factor."""
        assert is_likely_psu_adapter(
            "БП Aerocool VX Plus 800  ATX12V 2.3, 20+4P, 4+4P, PCIe 6+2P x4",
        ) is False

    def test_sfx_form_factor_protects(self):
        """SFX блок с словом 'адаптер' в чужом контексте — не помечать."""
        assert is_likely_psu_adapter(
            "POWERMAN PMP-300sfx, SFX 300W, адаптер в комплекте",
        ) is False

    def test_wattage_threshold_200w_protects_real_psu(self):
        """550W в имени → настоящий PSU, защита 2."""
        assert is_likely_psu_adapter(
            "Aerocool 550W какой-то PSU",
        ) is False

    def test_wattage_below_200w_does_not_protect_charger(self):
        """150W зарядка → защита 2 НЕ срабатывает; ключевое слово
        «зарядное» позитивит."""
        assert is_likely_psu_adapter(
            "Универсальное зарядное устройство 150W",
        ) is True

    def test_model_code_with_w_prefix_does_not_trigger_wattage(self):
        """W700 в коде модели Cooler Master Elite NEX W700 не должен
        срабатывать как 'мощность 700W' (буква перед числом).
        Здесь PSU защищён по 80 PLUS / ATX признакам."""
        assert is_likely_psu_adapter(
            "Cooler Master Elite NEX W700 230V Active PFC КПД 85%, "
            "120-мм тихий вентилятор, ATX, MPW-7001-ACBW-BE",
        ) is False

    def test_empty_input_returns_false(self):
        """Пустой name — защитное поведение, False без ложноположительности."""
        assert is_likely_psu_adapter(None) is False
        assert is_likely_psu_adapter("") is False
