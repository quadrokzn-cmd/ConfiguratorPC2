# Интеграционный тест обогащения: прогоняем 20 реальных наименований
# скелетов Merlion/Treolan (срез из рабочей БД) через соответствующие
# regex-экстракторы и проверяем, что набор извлечённых полей
# и их значения выглядят разумно.
#
# Тест не ходит в БД — строки скопированы из рабочего прайса и
# захардкожены здесь как фикстуры.

from app.services.enrichment.regex_sources import (
    case as case_src,
    cooler as cooler_src,
    gpu as gpu_src,
    psu as psu_src,
    storage as storage_src,
)


# ----- По 4 реальных примера на 5 категорий-скелетов = 20 позиций -----

PSU_SAMPLES = [
    ("Блок питания Formula V Line FV-400LT, 400W, APFC, 12cm Fan", 400),
    ("Блок питания Bloody ATX 500W BD-PS500W 80 PLUS WHITE", 500),
    ("Блок питания Seasonic ATX 1200W Vertex GX-1200 80+ gold", 1200),
    ("Блок питания Ocypus Delta P850S, 850W, APFC, 80+ Bronze", 850),
]

STORAGE_SAMPLES = [
    # (name, expected storage_type, form_factor, interface, capacity_gb)
    (
        "Жесткий диск Toshiba SATA-III 10TB MG10ADA10TE (7200rpm) 512Mb 3.5\"",
        "HDD", "3.5\"", "SATA", 10000,
    ),
    (
        "Накопитель SSD PC Pet PCIe 4.0 x4 4TB PCPS004T4 M.2 2280 OEM",
        "SSD", "M.2", "NVMe", 4000,
    ),
    (
        "Твердотельный накопитель Western Digital Green SSD 2,5\" SATA 1Tb, WDS100T5G0A",
        "SSD", "2.5\"", "SATA", 1000,
    ),
    (
        "SSD Накопитель Netac SSD N600S 128GB 2.5 SATAIII 3D NAND, 7mm",
        "SSD", "2.5\"", "SATA", 128,
    ),
]

COOLER_SAMPLES = [
    # (name, expect sockets contains list, expect max_tdp_watts)
    (
        "Кулер для процессора ID-COOLING SE-214-XT V2 BLACK LGA1851/1700/1200/115X/AM5/AM4 "
        "(16шт/кор, TDP 200W, PWM, 4 тепл.трубки прямого контакта, FAN 120mm) RET",
        {"LGA1851", "LGA1700", "LGA1200", "AM5", "AM4"},
        200,
    ),
    (
        "Система водяного охлаждения Thermaltake MAGFloe 420 Ultra Snow ARGB "
        "Soc-AM5/AM4/1200/1700/1851 белый 4-pin 34.5-38.8dB Al LCD 360W Ret",
        {"AM5", "AM4", "LGA1200", "LGA1700", "LGA1851"},
        360,  # явное "360W" > derived по 420мм
    ),
    (
        "Система водяного охлаждения Lian-Li Galahad II LCD 360 ARGB "
        "Soc-AM5/AM4/1700/1851 4-pin 30dB Al+Cu LCD Ret",
        {"AM5", "AM4", "LGA1700", "LGA1851"},
        300,  # derived по размеру радиатора 360мм
    ),
    (
        "Устройство охлаждения(кулер) Deepcool Assassin 4S Soc-AM5/AM4/1151/1200/2066/1700 "
        "белый 4-pin 22.6-29.3dB Al+Cu 250W 1380gr Ret",
        {"AM5", "AM4", "LGA1151", "LGA1200", "LGA2066", "LGA1700"},
        250,
    ),
]

GPU_SAMPLES = [
    # (name, expect vram_gb, expect vram_type)
    (
        "Видеокарта MSI RTX 5060 Ti 16G INSPIRE 2X OC//RTX 5060Ti, HDMI, DP*3, 16G , D7",
        16, "GDDR7",
    ),
    (
        "Видеокарта MSI PCI-E 5.0 RTX 5070 TI 16G VENTUS 3X OC NVIDIA GeForce RTX 5070TI "
        "16Gb 256bit GDDR7 2482/28000 HDMIx1 DPx3 HDCP Ret",
        16, "GDDR7",
    ),
    (
        "Видеокарта Asrock PCI-E 4.0 B570 CL 10GO INTEL ARC B570 10Gb 160bit GDDR6 "
        "2600/19000 HDMIx1 DPx3 HDCP Ret",
        10, "GDDR6",
    ),
    (
        "Видеокарта MAXSUN MS-GTX1650 TR 4GD6//GTX1650 HDMI, DP, DVI, 4G, D6",
        4, "GDDR6",
    ),
]

CASE_SAMPLES = [
    # (name, expect has_psu, expect supported_form_factors superset)
    (
        "Корпус Bloody CC-121 белый без БП mATX 7x120mm 1xUSB2.0 1xUSB3.0 audio",
        False, {"mATX"},
    ),
    (
        "корпус Mini Tower InWin ENR022 Black 500W PM-500ATX U3.0*2+A(HD) mATX",
        True, {"mATX"},
    ),
    (
        "Корпус Ocypus Gamma C50 BK, MATX, USB3.0*1+USB2.0*2",
        False, {"mATX"},
    ),
    (
        "Корпус Formula V Line Mana Stone Black AR, ATX, TYPE-C*1, USB2.0*1, USB3.0*1, "
        "FRONT 3x120mm ARGB, REAR 1x120mm ARGB",
        False, {"ATX"},
    ),
]


# -----------------------------------------------------------------------------

def test_psu_integration():
    for name, expected_watts in PSU_SAMPLES:
        r = psu_src.extract(name)
        assert "power_watts" in r, f"PSU not extracted: {name!r}"
        assert r["power_watts"].value == expected_watts, (
            f"expected {expected_watts}W, got {r['power_watts'].value}W "
            f"for {name!r}"
        )


def test_storage_integration():
    for name, st_type, ff, iface, cap_gb in STORAGE_SAMPLES:
        r = storage_src.extract(name)
        assert r["storage_type"].value == st_type, f"{name!r}: storage_type"
        assert r["form_factor"].value == ff, f"{name!r}: form_factor got {r['form_factor'].value}"
        assert r["interface"].value == iface, f"{name!r}: interface got {r['interface'].value}"
        assert r["capacity_gb"].value == cap_gb, f"{name!r}: capacity_gb got {r['capacity_gb'].value}"


def test_cooler_integration():
    for name, must_include_sockets, expect_tdp in COOLER_SAMPLES:
        r = cooler_src.extract(name)
        assert "supported_sockets" in r, f"{name!r}: no sockets"
        got_sockets = set(r["supported_sockets"].value)
        assert must_include_sockets.issubset(got_sockets), (
            f"{name!r}: expected sockets superset {must_include_sockets}, got {got_sockets}"
        )
        assert "max_tdp_watts" in r, f"{name!r}: no max_tdp_watts"
        assert r["max_tdp_watts"].value == expect_tdp, (
            f"{name!r}: expected {expect_tdp}W, got {r['max_tdp_watts'].value}W"
        )


def test_gpu_integration():
    for name, vram_gb, vram_type in GPU_SAMPLES:
        r = gpu_src.extract(name)
        assert r["vram_gb"].value == vram_gb, f"{name!r}: vram_gb got {r['vram_gb'].value}"
        assert r["vram_type"].value == vram_type, f"{name!r}: vram_type got {r['vram_type'].value}"


def test_case_integration():
    for name, expect_has_psu, must_include_ff in CASE_SAMPLES:
        r = case_src.extract(name)
        assert "supported_form_factors" in r, f"{name!r}: no form_factors"
        got_ff = set(r["supported_form_factors"].value)
        assert must_include_ff.issubset(got_ff), (
            f"{name!r}: expected ff superset {must_include_ff}, got {got_ff}"
        )
        assert "has_psu_included" in r, f"{name!r}: no has_psu_included"
        assert r["has_psu_included"].value is expect_has_psu, (
            f"{name!r}: expected has_psu={expect_has_psu}, got {r['has_psu_included'].value}"
        )
