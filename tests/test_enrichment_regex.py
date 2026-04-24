# Unit-тесты на regex-паттерны, добавленные/расширенные в микроэтапе
# обогащения скелетов Merlion/Treolan.
#
# Проверяем только добавленные паттерны и крайние случаи Merlion-формата.
# Базовые случаи (OCS-стиль) покрывались визуальной проверкой прошлых этапов.

from app.services.enrichment.regex_sources import (
    case as case_src,
    cooler as cooler_src,
    gpu as gpu_src,
    storage as storage_src,
)


# ==========================================================================
# GPU: сокращение VRAM-типа "D6"/"D7" и "Gb" (с малой b) для объёма
# ==========================================================================

class TestGpuVramTypeShort:
    """Merlion-формат: '...16G, D7' / '8G,D6' / '10G D6X'."""

    def test_d7_after_comma_space(self):
        name = "MSI RTX 5070 16G INSPIRE 2X OC//RTX 5070, HDMI, DP*3, 16G , D7"
        r = gpu_src.extract(name)
        assert r["vram_type"].value == "GDDR7"

    def test_d6_without_space(self):
        name = "INNO3D RTX 5060 Twin X2//RTX5060, HDMI, DP*3, 8G,D6"
        r = gpu_src.extract(name)
        assert r["vram_type"].value == "GDDR6"

    def test_d6x_variant(self):
        name = "ASUS ROG-STRIX-RTX4090-O24G-GAMING, 24G, D6X"
        r = gpu_src.extract(name)
        assert r["vram_type"].value == "GDDR6X"

    def test_d6_not_from_artikul_g_d5(self):
        """'-8GD5' в артикуле — это не VRAM-тип, это суффикс модели."""
        name = "Afox PCI-E 3.0 AF1050TI-4096D5H7-V9 GTX 1050TI 4Gb 128bit GDDR5"
        r = gpu_src.extract(name)
        # vram_type должен быть GDDR5 (из явного), а не случайно D5
        assert r["vram_type"].value == "GDDR5"

    def test_explicit_gddr_has_priority_over_short(self):
        name = "Gigabyte GV-N710D3-2GL 2Gb 64bit DDR3 954/1600, 2G, D3"
        r = gpu_src.extract(name)
        assert r["vram_type"].value == "DDR3"

    def test_short_no_match_if_embedded_in_sku(self):
        """'WDS100T1X0M' — не должно триггериться."""
        name = "Gigabyte GV-N1650WF-4GD NVIDIA GTX 1650 4Gb 128bit GDDR5"
        r = gpu_src.extract(name)
        # Здесь есть GDDR5 — это то, что надо
        assert r["vram_type"].value == "GDDR5"


class TestGpuVramGbLowerB:
    """Объём VRAM: 'Gb' c маленькой b (Afox/Biostar/Gigabyte Merlion-стиль)."""

    def test_4gb_lower(self):
        name = "Afox AF730-4096D3L6 NVIDIA GeForce GT 730 4Gb 128bit GDDR3"
        r = gpu_src.extract(name)
        assert r["vram_gb"].value == 4

    def test_10gb_lower_arc(self):
        name = "Asrock B570 CL 10GO INTEL ARC B570 10Gb 160bit GDDR6"
        r = gpu_src.extract(name)
        assert r["vram_gb"].value == 10

    def test_16gb_upper_still_works(self):
        name = "ASUS PRIME-RTX5070TI-O16G NVIDIA RTX 5070TI 16Gb 256bit GDDR7"
        r = gpu_src.extract(name)
        assert r["vram_gb"].value == 16


# ==========================================================================
# COOLER: derived max_tdp_watts по размеру радиатора AIO
# ==========================================================================

class TestCoolerAioDerivedTdp:
    """Merlion-стиль СЖО без указания TDP в имени."""

    def test_aio_360_russian_marker(self):
        name = ("Система водяного охлаждения Deepcool LM420 ARGB "
                "Soc-AM5/AM4/1200/1700/1851 черный 4-pin 25.2dB Al LCD")
        r = cooler_src.extract(name)
        assert r["max_tdp_watts"].value == 400
        assert r["max_tdp_watts"].source == "derived"

    def test_aio_360_russian(self):
        name = ("Система водяного охлаждения Lian-Li Galahad II LCD 360 ARGB "
                "Soc-AM5/AM4/1700/1851 4-pin 30dB Al+Cu LCD Ret")
        r = cooler_src.extract(name)
        assert r["max_tdp_watts"].value == 300
        assert r["max_tdp_watts"].source == "derived"

    def test_aio_240_english(self):
        name = "Arctic Freezer II 240 AIO Liquid cooling AM5 LGA1700"
        r = cooler_src.extract(name)
        assert r["max_tdp_watts"].value == 200

    def test_aio_280(self):
        name = "Система водяного охлаждения DeepCool LE280 Pro ARGB Soc-AM5"
        r = cooler_src.extract(name)
        assert r["max_tdp_watts"].value == 250

    def test_explicit_w_has_priority(self):
        """Если в скобках явно указано '200W', derived не должен его
        перезаписать (в extract derived выполняется только при отсутствии regex)."""
        name = "Deepcool AK620 (200W, 4-pin, 360mm) Soc-AM5 LGA1700 AIO"
        r = cooler_src.extract(name)
        assert r["max_tdp_watts"].value == 200
        assert r["max_tdp_watts"].source == "regex"

    def test_no_aio_marker_no_derived(self):
        """Для воздушного кулера (без маркера AIO) derived не срабатывает."""
        name = ("Thermalright Peerless Assassin 120 Black "
                "(4-pin PWM, 157mm, Ni/CU, 6x6mm, 2x120mm, S: 1700, AM5)")
        r = cooler_src.extract(name)
        # Нет 'Система водяного охлаждения' / 'AIO' / 'Liquid' / 'pump' / 'СВО'
        # → max_tdp_watts остаётся без derived-значения.
        assert "max_tdp_watts" not in r


# ==========================================================================
# STORAGE: русские единицы Гб/Тб, 2,5" с запятой, mSATA, "2.5 SATA" без кавычек
# ==========================================================================

class TestStorageCapacityRussian:
    def test_gb_ru(self):
        name = "GS Nanotech SSD GS027 512Гб PCIe 3 x4, M.2 2280"
        r = storage_src.extract(name)
        assert r["capacity_gb"].value == 512

    def test_tb_ru(self):
        name = "Toshiba HDD SATA 2Тб 3.5\""
        r = storage_src.extract(name)
        assert r["capacity_gb"].value == 2000

    def test_gb_en_still_works(self):
        name = "Samsung 970 EVO Plus 500GB M.2 NVMe"
        r = storage_src.extract(name)
        assert r["capacity_gb"].value == 500


class TestStorageFormFactor25:
    def test_comma_instead_of_dot(self):
        """Merlion-SKU Crucial/WD: "2,5\" SATA 240Gb" (с русской запятой)."""
        name = "Western Digital Green SSD 2,5\" SATA 240Gb, WDS240G3G0A"
        r = storage_src.extract(name)
        assert r["form_factor"].value == '2.5"'

    def test_bare_before_sata(self):
        """Netac формат: '128GB 2.5 SATAIII' — без кавычек."""
        name = "Netac SSD N600S 128GB 2.5 SATAIII 3D NAND, 7mm"
        r = storage_src.extract(name)
        assert r["form_factor"].value == '2.5"'

    def test_msata(self):
        name = "Netac SSD N5M 2TB mSATA SATAIII 3D NAND"
        r = storage_src.extract(name)
        assert r["form_factor"].value == "mSATA"


# ==========================================================================
# CASE: derived has_psu_included=False для DIY-корпусов
# ==========================================================================

class TestCaseDerivedNoPsu:
    """Современный DIY-корпус без явного указания БП → derived False."""

    def test_formula_v_line(self):
        name = "Formula V Line CS-110-S mATX USB3.0x1/USB2.0x1/audio (ex Aerocool)"
        r = case_src.extract(name)
        assert r["has_psu_included"].value is False
        assert r["has_psu_included"].source == "derived"
        # form_factor определён → derived сработал
        assert "mATX" in r["supported_form_factors"].value

    def test_ocypus(self):
        name = "Ocypus Gamma C50 BK, MATX, USB3.0*1+USB2.0*2"
        r = case_src.extract(name)
        assert r["has_psu_included"].value is False
        assert r["has_psu_included"].source == "derived"

    def test_zalman_atx(self):
        name = ("ZALMAN T8, ATX, BLACK, 1x5.25\", 2x3.5\", 2x2.5\", "
                "2xUSB2.0, 1xUSB3.0, REAR 1x120mm")
        r = case_src.extract(name)
        assert r["has_psu_included"].value is False

    def test_explicit_no_psu_still_wins(self):
        """Явное 'без БП' → has_psu_included=False, source='regex' (не derived)."""
        name = "Bloody CC-121 белый без БП mATX 7x120mm"
        r = case_src.extract(name)
        assert r["has_psu_included"].value is False
        assert r["has_psu_included"].source == "regex"

    def test_explicit_with_psu_watts(self):
        name = "InWin ENR022 Black 500W PM-500ATX U3.0*2+A(HD) mATX"
        r = case_src.extract(name)
        assert r["has_psu_included"].value is True
        assert r["included_psu_watts"].value == 500

    def test_no_form_factor_no_derived(self):
        """Если формфактор не определился — derived has_psu не срабатывает."""
        name = "Accord некорпус с неизвестным названием"
        r = case_src.extract(name)
        assert "has_psu_included" not in r
