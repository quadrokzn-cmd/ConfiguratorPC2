# Тесты детектора is_likely_non_storage (этап 11.6.2.6.0b).
#
# По итогам аудита 6.0a в категории storages обнаружились ровно 2 явно
# мусорных строки: id=782 Kingston SNA-BR2/35 и id=1133 Digma DGBRT2535
# (рамки 2.5"→3.5"). Детектор должен ловить их (и синтетику card-reader/
# USB-hub) и при этом НЕ задевать ни один из 1187 видимых настоящих
# накопителей топ-брендов.
#
# Тесты построены на реальных raw_name из БД (id указаны рядом) +
# синтетика на профилактические маркеры.

from __future__ import annotations

import pytest

from shared.component_filters import is_likely_non_storage


class TestIsLikelyNonStoragePositive:
    """Кейсы, которые ОБЯЗАНЫ помечаться как не-накопитель."""

    def test_id_782_kingston_sna_br2_35_bracket(self):
        """id=782 Kingston SNA-BR2/35 — рамка-переходник 2.5"→3.5"
        для крепления SSD в отсек 3.5". Триггер «крепления для (SSD)»."""
        assert is_likely_non_storage(
            "Крепления для твердотельного диска (SSD) Kingston SNA-BR2/35 "
            "для установки 2.5'' SSD в отсек 3.5'' Retail",
            "Kingston",
        ) is True

    def test_id_1133_digma_dgbrt2535_bracket(self):
        """id=1133 Digma DGBRT2535 — рамка-переходник 2.5"→3.5".
        Триггер «крепление для HDD/SSD» + размерное преобразование."""
        assert is_likely_non_storage(
            "Крепление для HDD/SSD Digma DGBRT2535, 2.5\" -> 3.5\", "
            "сталь, чёрный",
            "Digma",
        ) is True

    def test_synthetic_25_to_35_adapter(self):
        """Синтетика: «адаптер 2.5"->3.5"» — типовая рамка-переходник,
        ловится по правилу размерного преобразования."""
        assert is_likely_non_storage(
            "Адаптер 2.5\" -> 3.5\" для установки SATA-устройств",
        ) is True

    def test_synthetic_card_reader(self):
        """Синтетика: card-reader попал в storage."""
        assert is_likely_non_storage(
            "Transcend RDF5 USB 3.1 Card Reader for SD/SDHC/SDXC microSD",
            "Transcend",
        ) is True

    def test_synthetic_kardrider_russian(self):
        """Синтетика: «Кардридер» по-русски."""
        assert is_likely_non_storage(
            "Кардридер внешний USB-C для SD/microSD карт",
        ) is True

    def test_synthetic_usb_hub(self):
        """Синтетика: USB-hub в storage."""
        assert is_likely_non_storage(
            "USB-hub Orient JK-330 на 4 порта USB 3.0",
            "Orient",
        ) is True

    def test_synthetic_usb_kontsentrator_russian(self):
        """Синтетика: «USB-концентратор» (русский синоним hub)."""
        assert is_likely_non_storage(
            "USB концентратор 4 порта с блоком питания",
        ) is True

    def test_synthetic_perehodnik_25_no_gb(self):
        """Синтетика: «переходник 2.5» без GB — рамка для накопителя."""
        assert is_likely_non_storage(
            "Переходник 2.5\" для жесткого диска в отсек 5.25\"",
        ) is True

    def test_synthetic_kreplenie_dlya_hdd(self):
        """Синтетика: «крепления для HDD» — другая форма триггера."""
        assert is_likely_non_storage(
            "Крепления для HDD универсальные, набор 4 штуки",
        ) is True


class TestIsLikelyNonStorageNegativeRealDrives:
    """Реальные SSD/HDD топ-брендов из БД и синтетика — НЕ должны
    помечаться. Проверяет защиту по форм-фактору и характеристикам."""

    def test_samsung_980_pro_nvme(self):
        """Samsung 980 Pro — NVMe M.2 2280 SSD. Защита по NVMe/M.2/2280."""
        assert is_likely_non_storage(
            "Samsung 980 PRO 1TB NVMe M.2 2280 SSD MZ-V8P1T0BW",
            "Samsung",
        ) is False

    def test_wd_blue_sata(self):
        """WD Blue 1TB SATA SSD — защита по capacity_gb=1000."""
        assert is_likely_non_storage(
            "WD Blue 3D NAND SATA SSD 1TB WDS100T2B0A",
            "Western Digital",
            capacity_gb=1000,
        ) is False

    def test_kingston_a2000_nvme_m2(self):
        """Kingston A2000 — NVMe M.2 2280 PCIe SSD. Защита M.2/2280."""
        assert is_likely_non_storage(
            "Kingston A2000 SSD 500GB NVMe PCIe Gen3 x4 M.2 2280 SA2000M8/500G",
            "Kingston",
        ) is False

    def test_crucial_mx500_with_storage_type(self):
        """Crucial MX500 — SATA SSD. Защита через storage_type='ssd'."""
        assert is_likely_non_storage(
            "Crucial MX500 1TB CT1000MX500SSD1",
            "Crucial",
            storage_type="ssd",
        ) is False

    def test_exegate_next_m2(self):
        """ExeGate Next 256GB — M.2 SATA SSD. Защита по M.2."""
        assert is_likely_non_storage(
            "Накопитель SSD ExeGate Next M.2 2280 256GB EX282321RUS",
            "ExeGate",
        ) is False

    def test_exegate_nextpro_plus(self):
        """ExeGate NextPro+ — реальный SSD. Защита по форм-фактору."""
        assert is_likely_non_storage(
            "ExeGate NextPro+ UV500NTS256 256GB 2.5\" SATA SSD",
            "ExeGate",
            capacity_gb=256,
        ) is False

    def test_toshiba_mq04_hdd(self):
        """Toshiba MQ04 — HDD 2.5". Защита через capacity_gb=2000.
        Имя содержит «2.5\"» но БЕЗ «GB» — формально мог бы матчить
        триггер «переходник/адаптер 2.5», но триггер требует слова
        переходник/адаптер/рамка/кронштейн ПЕРЕД «2.5», его тут нет."""
        assert is_likely_non_storage(
            "Toshiba MQ04ABF200 2TB 2.5\" 5400rpm SATA-III HDD",
            "Toshiba",
            capacity_gb=2000,
        ) is False

    def test_seagate_barracuda_capacity_only(self):
        """Seagate BarraCuda 2TB HDD — защита по одному только
        capacity_gb. Имя без NVMe/M.2/2280, но и без триггера мусора."""
        assert is_likely_non_storage(
            "Seagate BarraCuda 2TB ST2000DM008",
            "Seagate",
            capacity_gb=2000,
        ) is False

    def test_netac_n600s_nvme_25gb_cache_text(self):
        """Netac N600S 256GB. Имя случайно содержит «2.5 GB DRAM кэш» —
        НЕ должно матчить триггер «переходник 2.5» (lookahead на GB)."""
        assert is_likely_non_storage(
            "Netac N600S 256GB SATA SSD 2.5 inch (2.5 GB DRAM cache)",
            "Netac",
        ) is False

    def test_msata_real_ssd(self):
        """Реальный mSATA SSD — защита по mSATA."""
        assert is_likely_non_storage(
            "Transcend MSA452T 256GB mSATA SSD",
            "Transcend",
        ) is False


class TestIsLikelyNonStorageDefenseLayers:
    """Юнит-проверки защитных слоёв в изоляции."""

    def test_capacity_gb_threshold_blocks_positive_trigger(self):
        """Имя с триггером «крепления для SSD», но capacity_gb=500 —
        защита 1 (capacity≥32) блокирует. Это маловероятный сценарий
        (рамки не имеют ёмкости), но защита должна работать строго."""
        assert is_likely_non_storage(
            "Крепления для SSD Samsung",
            capacity_gb=500,
        ) is False

    def test_storage_type_blocks_positive_trigger(self):
        """Если storage_type уже заполнен — даже триггер не помечает."""
        assert is_likely_non_storage(
            "Крепления для HDD Kingston",
            storage_type="ssd",
        ) is False

    def test_capacity_below_threshold_does_not_block(self):
        """capacity_gb<32 (например, 8) — защита 1 НЕ срабатывает."""
        assert is_likely_non_storage(
            "Крепления для HDD Kingston",
            capacity_gb=8,
        ) is True

    def test_capacity_none_does_not_block(self):
        """capacity_gb=None — защита 1 пропускает, дальше работает имя."""
        assert is_likely_non_storage(
            "Крепления для SSD Samsung",
            capacity_gb=None,
        ) is True

    def test_storage_type_empty_does_not_block(self):
        """storage_type='' / whitespace — защита 2 пропускает."""
        assert is_likely_non_storage(
            "Крепления для HDD",
            storage_type="",
        ) is True
        assert is_likely_non_storage(
            "Крепления для HDD",
            storage_type="   ",
        ) is True

    def test_empty_input_returns_false(self):
        """Пустое имя → защитное False (нет позитивной находки)."""
        assert is_likely_non_storage(None) is False
        assert is_likely_non_storage("") is False
        assert is_likely_non_storage("   ") is False

    def test_no_trigger_no_match(self):
        """Чистое имя без триггеров — False даже без характеристик."""
        assert is_likely_non_storage(
            "Какой-то непонятный текст без маркеров",
        ) is False
