# Юнит-тесты модулей claude_code AI-обогащения (этап 2.5Б):
# - whitelist-проверка URL источников (validators._validate_source_url)
# - derive_needs_extra_power
# - normalize_video_outputs
# - валидация новых storage-полей

import pytest

from app.services.enrichment.claude_code.derive import (
    derive_needs_extra_power,
    normalize_video_outputs,
    has_power_connector_hint,
)
from app.services.enrichment.claude_code.validators import (
    ValidationError,
    _validate_source_url,
    validate_field,
)
from app.services.enrichment.claude_code.schema import OFFICIAL_DOMAINS


# ----------------------------------------------------------------------
# whitelist
# ----------------------------------------------------------------------
class TestWhitelist:
    def test_expanded_whitelist_contains_stage_2_5b_additions(self):
        added = {
            "ocypus.com", "maxsun.com", "maxsun.com.cn",
            "idcooling.com", "sapphiretech.com", "inno3d.com",
            "hpe.com", "netac.com", "fsp-group.com", "fsplifestyle.com",
            "seagate.com", "pccooler.com.cn", "apacer.com",
            "kingston.com", "in-win.com", "westerndigital.com",
        }
        assert added.issubset(OFFICIAL_DOMAINS), (
            f"Отсутствуют в OFFICIAL_DOMAINS: {added - OFFICIAL_DOMAINS}"
        )

    def test_whitelist_contains_stage_2_5v_additions(self):
        # Этап 2.5В: afox-corp.com и gamerstorm.com добавлены после
        # WebFetch-проверки (активные сайты с product catalogs).
        added = {"afox-corp.com", "gamerstorm.com"}
        assert added.issubset(OFFICIAL_DOMAINS)

    def test_whitelist_contains_stage_11_6_2_3_1_cooler_additions(self):
        # Этап 11.6.2.3.1: рассинхрон между cooler.md и schema.py починен.
        # Все эти домены — официальные производители кулеров/вентиляторов
        # и должны проходить валидатор.
        added = {
            "cooler-master.com", "be-quiet.net", "aerocool.com",
            "ekwb.com", "alphacool.com", "scythe-eu.com",
            "silverstonetek.com", "evga.com", "endorfy.com",
        }
        assert added.issubset(OFFICIAL_DOMAINS), (
            f"Отсутствуют в OFFICIAL_DOMAINS: {added - OFFICIAL_DOMAINS}"
        )

    @pytest.mark.parametrize("url", [
        "https://www.afox-corp.com/index.php?m=content&c=index&a=lists&catid=55",
        "https://www.gamerstorm.com/product/PowerSupply/2024-11/2153_15131.shtml",
    ])
    def test_new_2_5v_domains_pass(self, url):
        assert _validate_source_url(url) == url

    @pytest.mark.parametrize("url", [
        "https://www.cooler-master.com/catalog/coolers/cpu-air-coolers/",
        "https://www.be-quiet.net/en/cpu-cooler/dark-rock-pro-4",
        "https://www.aerocool.com/cases/peripherals/frost-12",
        "https://www.ekwb.com/shop/ek-aio-360-d-rgb",
        "https://www.alphacool.com/eisbaer-aurora-360",
        "https://www.scythe-eu.com/en/products/cpu-cooler/fuma-2",
        "https://www.silverstonetek.com/en/product/info/case-cooler/AR12-RGB/",
        "https://www.evga.com/products/product.aspx?pn=400-HY-CL24-V1",
        "https://www.endorfy.com/en/fortis-5-dual-fan-eg100007",
    ])
    def test_new_cooler_domains_pass(self, url):
        assert _validate_source_url(url) == url

    @pytest.mark.parametrize("url", [
        # Верхний регистр в наименовании bequiet и т.п. — host сравнивается
        # case-insensitive, поэтому это тоже должно проходить.
        "https://www.BeQuiet.com/products/dark-rock-pro-4",
        "https://WWW.COOLERMASTER.COM/catalog/coolers/",
    ])
    def test_cooler_domains_case_insensitive(self, url):
        assert _validate_source_url(url) == url

    def test_afox_shop_subdomain_rejected_if_not_in_whitelist(self):
        # shop.afox.ru — поддомен afox.ru, whitelist содержит afox.ru → проходит.
        assert _validate_source_url("https://shop.afox.ru/product") != ""
        # А вот afoxcn.ru.retailer.com — чужой хост, отклоняется.
        with pytest.raises(ValidationError):
            _validate_source_url("https://afoxcn.ru.retailer.com/catalog")

    @pytest.mark.parametrize("url", [
        "https://www.asus.com/ru/motherboards/p/x",
        "https://rog.asus.com/graphics-cards/rog-strix",
        "https://nvidia.com/en-us/geforce/graphics-cards/",
        "https://www.ocypus.com/product/gamma-c50",
        "https://maxsun.com.cn/product/12345",
        "https://www.hpe.com/psnow/doc/a00098961ru_ru",
    ])
    def test_valid_official_urls_pass(self, url):
        assert _validate_source_url(url) == url

    @pytest.mark.parametrize("url", [
        "https://www.dns-shop.ru/product/12345",
        "https://www.citilink.ru/product/gpu-asus",
        "https://www.ozon.ru/product/foo",
        "https://market.yandex.ru/product",
        "https://ru.wikipedia.org/wiki/RTX_5060",
        "https://www.ixbt.com/news/2026",
        "https://www.techpowerup.com/gpu-specs/rtx-5060",
        "https://www.videocardz.com/news",
        "https://webcache.googleusercontent.com/search?q=cache:asus.com/...",
    ])
    def test_retailer_and_review_urls_rejected(self, url):
        with pytest.raises(ValidationError) as excinfo:
            _validate_source_url(url)
        assert "bad_domain" in str(excinfo.value)

    def test_non_https_rejected(self):
        with pytest.raises(ValidationError):
            _validate_source_url("ftp://asus.com/driver")

    def test_empty_url_rejected(self):
        with pytest.raises(ValidationError):
            _validate_source_url("")
        with pytest.raises(ValidationError):
            _validate_source_url(None)

    def test_subdomain_of_whitelisted_domain_allowed(self):
        assert _validate_source_url("https://support.hp.com/us-en/product/123") != ""
        assert _validate_source_url("https://rog.asus.com/graphics-cards") != ""

    def test_lookalike_domain_rejected(self):
        # nvidia.com.evil.ru — НЕ nvidia.com
        with pytest.raises(ValidationError):
            _validate_source_url("https://nvidia.com.evil.ru/product")

    # --- Этап 11.6.2.5.0c ---
    def test_whitelist_contains_stage_11_6_2_5_0c_psu_additions(self):
        """5 PSU-доменов добавлены перед AI-обогащением 11.6.2.5.1."""
        added = {
            "exegate.ru", "crown-micro.com", "gamemaxpc.com",
            "formulav-line.com", "super-flower.com.tw",
        }
        assert added.issubset(OFFICIAL_DOMAINS), (
            f"Отсутствуют в OFFICIAL_DOMAINS: {added - OFFICIAL_DOMAINS}"
        )

    @pytest.mark.parametrize("url", [
        "https://www.exegate.ru/catalogue/psu/",
        "https://crown-micro.com/products/cm-ps500-superior",
        "https://gamemaxpc.com/psu/lion-core-1200p",
        "https://formulav-line.com/products/psu/",
        "https://www.super-flower.com.tw/en/products/leadex-titanium-1000w",
    ])
    def test_psu_5_0c_urls_pass(self, url):
        assert _validate_source_url(url) == url

    def test_url_host_case_insensitive(self):
        """Whitelist matching должен быть регистронезависимым по host:
        DEEPCOOL.COM, Deepcool.com, deepcool.com — все валидны.
        urllib.parse.urlparse даёт hostname в lowercase для большинства
        случаев, но дополнительно мы lower'им и host, и whitelist —
        страховка от регрессий, если кто-то добавит «Aerocool.com»
        с заглавной в schema.py.
        """
        for url in (
            "https://DEEPCOOL.COM/product/AS500",
            "https://Deepcool.com/product/AS500",
            "https://deepcool.com/product/AS500",
            "https://DeepCool.Com/product/AS500",
        ):
            assert _validate_source_url(url) == url

    def test_url_host_with_uppercase_subdomain(self):
        """Поддомены тоже case-insensitive."""
        assert _validate_source_url(
            "https://SUPPORT.HP.COM/us-en/product/123"
        ) != ""


# ----------------------------------------------------------------------
# derive_needs_extra_power
# ----------------------------------------------------------------------
class TestDeriveNeedsExtraPower:
    def test_high_tdp_requires_power(self):
        assert derive_needs_extra_power(250, None) is True
        assert derive_needs_extra_power(75, None) is True

    def test_low_tdp_without_connector_no_power(self):
        assert derive_needs_extra_power(30, None) is False
        assert derive_needs_extra_power(74, None) is False

    def test_low_tdp_with_connector_still_true(self):
        # Редкий случай: TDP низкий, но разъём есть (например, overclocked variant)
        assert derive_needs_extra_power(60, "1x 6-pin") is True

    def test_unknown_tdp_with_connector_true(self):
        assert derive_needs_extra_power(None, "8-pin PCIe") is True
        assert derive_needs_extra_power(None, "12VHPWR") is True

    def test_unknown_everything_returns_none(self):
        assert derive_needs_extra_power(None, None) is None
        assert derive_needs_extra_power(None, "no power connector required") is None


class TestHasPowerConnectorHint:
    @pytest.mark.parametrize("text", [
        "6-pin",
        "8 pin",
        "1x 6+8pin",
        "12VHPWR",
        "12V HPWR",
        "12V-2x6",
        "2x 8-pin PCIe",
    ])
    def test_detects_connector_tokens(self, text):
        assert has_power_connector_hint(text) is True

    @pytest.mark.parametrize("text", [
        "",
        None,
        "slot power only",
        "No external power",
        "PCIe 4.0 x16",   # интерфейс, не разъём питания
    ])
    def test_no_false_positives(self, text):
        assert has_power_connector_hint(text) is False


# ----------------------------------------------------------------------
# normalize_video_outputs
# ----------------------------------------------------------------------
class TestNormalizeVideoOutputs:
    @pytest.mark.parametrize("raw,expected", [
        ("1x HDMI 2.1 + 3x DP 1.4", "1xHDMI2.1+3xDP1.4"),
        ("HDMI*1, DP*3", "1xHDMI+3xDP"),
        ("1 HDMI + 3 DisplayPort", "1xHDMI+3xDP"),
        ("HDMI 2.1a, 3x DP 1.4a", "1xHDMI2.1+3xDP1.4"),
        ("1xHDMI+1xDVI-D+1xD-Sub", "1xHDMI+1xDVI-D+1xVGA"),
        ("3 x DisplayPort 2.1, 1 x HDMI 2.1", "3xDP2.1+1xHDMI2.1"),
    ])
    def test_normalize(self, raw, expected):
        assert normalize_video_outputs(raw) == expected

    @pytest.mark.parametrize("raw", [
        "", None, "nothing useful here",
    ])
    def test_empty_or_invalid_returns_none(self, raw):
        assert normalize_video_outputs(raw) is None


# ----------------------------------------------------------------------
# Валидация storage
# ----------------------------------------------------------------------
class TestStorageValidators:
    def test_storage_type_accepts_ssd_and_hdd(self):
        vf = validate_field("storage", "storage_type",
                            {"value": "SSD", "source_url": "https://kingston.com/datasheets/foo.pdf"})
        assert vf.value == "SSD"
        vf = validate_field("storage", "storage_type",
                            {"value": "HDD", "source_url": "https://seagate.com/internal/foo"})
        assert vf.value == "HDD"

    def test_storage_type_nvme_maps_to_ssd(self):
        vf = validate_field("storage", "storage_type",
                            {"value": "NVMe", "source_url": "https://apacer.com/en/product/ssd/as2280p4"})
        assert vf.value == "SSD"

    def test_storage_form_factor_25_inch(self):
        vf = validate_field("storage", "form_factor",
                            {"value": "2.5\"", "source_url": "https://seagate.com/internal/foo"})
        assert vf.value == "2.5\""

    def test_storage_form_factor_m2(self):
        vf = validate_field("storage", "form_factor",
                            {"value": "M.2", "source_url": "https://kingston.com/datasheets/foo.pdf"})
        assert vf.value == "M.2"

    def test_storage_interface_nvme(self):
        vf = validate_field("storage", "interface",
                            {"value": "NVMe", "source_url": "https://apacer.com/product"})
        assert vf.value == "NVMe"

    def test_storage_interface_sata3_maps_to_sata(self):
        vf = validate_field("storage", "interface",
                            {"value": "SATA-III", "source_url": "https://seagate.com/internal/foo"})
        assert vf.value == "SATA"

    def test_storage_capacity_gb(self):
        vf = validate_field("storage", "capacity_gb",
                            {"value": 1000, "source_url": "https://seagate.com/internal/foo"})
        assert vf.value == 1000

    def test_storage_rejects_url_from_retailer(self):
        with pytest.raises(ValidationError) as excinfo:
            validate_field("storage", "storage_type",
                           {"value": "SSD", "source_url": "https://dns-shop.ru/p/123"})
        assert "bad_domain" in str(excinfo.value)
