# Unit-тесты для generate_auto_name (этап 6.2).
#
# Вариант подаётся уже в формате, который возвращает
# _prepare_variants + enrich_variants_with_specs: в каждом компоненте
# есть model и raw_specs с сырыми полями из БД.
# БД не нужна.

from __future__ import annotations

import pytest

from portal.services.configurator.spec_naming import generate_auto_name


# ---------------------------------------------------------------------
# Фабрика вариантов
# ---------------------------------------------------------------------

def _comp(cat: str, *, model: str | None = None, **raw) -> dict:
    return {
        "category":    cat,
        "model":       model,
        "raw_specs":   raw,
    }


def _variant(
    *,
    manufacturer: str = "Intel",
    cpu: dict | None = None,
    motherboard: dict | None = None,
    ram: dict | None = None,
    storages: list[dict] | None = None,
    gpu: dict | None = None,
    psu: dict | None = None,
    total_usd: float = 1000.0,
    total_rub: float = 90000.0,
) -> dict:
    comps: dict[str, dict] = {}
    if cpu:         comps["cpu"] = cpu
    if motherboard: comps["motherboard"] = motherboard
    if ram:         comps["ram"] = ram
    if gpu:         comps["gpu"] = gpu
    if psu:         comps["psu"] = psu
    storages = storages or []
    if storages:
        comps["storage"] = storages[0]
    return {
        "manufacturer":  manufacturer,
        "total_usd":     total_usd,
        "total_rub":     total_rub,
        "components":    comps,
        "storages_list": storages,
    }


# ---------------------------------------------------------------------
# Примеры из ТЗ
# ---------------------------------------------------------------------

def test_full_gaming_build_example_1():
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400F",
                  socket="LGA1700", base_clock_ghz=2.5, turbo_clock_ghz=4.4),
        motherboard=_comp("motherboard", model="ASUS PRIME H610M",
                          socket="LGA1700", form_factor="mATX", memory_type="DDR4"),
        ram=_comp("ram", model="Kingston Fury 16GB",
                  memory_type="DDR4", module_size_gb=16, modules_count=1),
        storages=[_comp("storage", model="Samsung 980 512GB",
                        capacity_gb=512, storage_type="SSD")],
        gpu=_comp("gpu", model="GIGABYTE GeForce RTX 3050 Eagle OC 8G"),
        psu=_comp("psu", model="Be Quiet 450W", power_watts=450),
    )
    assert generate_auto_name(v) == (
        "Системный блок Intel Core i5-12400F 2.5/4.4GHz / LGA1700 / "
        "16GB DDR4 / 512GB SSD / RTX 3050 / mATX / 450W"
    )


def test_office_without_gpu_example_2():
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400",
                  socket="LGA1700", base_clock_ghz=2.5, turbo_clock_ghz=4.4),
        motherboard=_comp("motherboard", model="ASUS PRIME H610M",
                          form_factor="mATX", memory_type="DDR4"),
        ram=_comp("ram", memory_type="DDR4", module_size_gb=16, modules_count=1),
        storages=[_comp("storage", capacity_gb=512, storage_type="SSD")],
        psu=_comp("psu", power_watts=450),
    )
    assert generate_auto_name(v) == (
        "Системный блок Intel Core i5-12400 2.5/4.4GHz / LGA1700 / "
        "16GB DDR4 / 512GB SSD / mATX / 450W"
    )


def test_two_storages_combined_with_plus_example_3():
    v = _variant(
        manufacturer="AMD",
        cpu=_comp("cpu", model="AMD Ryzen 5 7600",
                  socket="AM5", base_clock_ghz=3.8, turbo_clock_ghz=5.1),
        motherboard=_comp("motherboard", form_factor="ATX"),
        ram=_comp("ram", memory_type="DDR5", module_size_gb=16, modules_count=2),
        storages=[
            _comp("storage", capacity_gb=1000, storage_type="SSD"),
            _comp("storage", capacity_gb=2000, storage_type="HDD"),
        ],
        psu=_comp("psu", power_watts=650),
    )
    assert generate_auto_name(v) == (
        "Системный блок AMD Ryzen 5 7600 3.8/5.1GHz / AM5 / "
        "32GB DDR5 / 1TB SSD + 2TB HDD / ATX / 650W"
    )


# ---------------------------------------------------------------------
# Пропуски полей
# ---------------------------------------------------------------------

def test_only_turbo_clock_shown_if_base_missing():
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400F",
                  socket="LGA1700", turbo_clock_ghz=4.4),
    )
    name = generate_auto_name(v)
    assert "Intel Core i5-12400F 4.4GHz" in name
    assert "2.5" not in name


def test_no_clocks_only_cpu_model():
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400F", socket="LGA1700"),
    )
    name = generate_auto_name(v)
    assert "Intel Core i5-12400F" in name
    assert "GHz" not in name


def test_missing_psu_skipped():
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400", socket="LGA1700",
                  base_clock_ghz=2.5, turbo_clock_ghz=4.4),
        ram=_comp("ram", memory_type="DDR4", module_size_gb=8, modules_count=1),
    )
    name = generate_auto_name(v)
    # Последний блок — RAM, никакого «450W».
    assert not name.rstrip().endswith("W")
    # Голова строки — CPU-блок вплотную к «Системный блок».
    assert name.startswith("Системный блок Intel Core i5-12400 2.5/4.4GHz / LGA1700 /")


def test_missing_ram_block_skipped():
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400", socket="LGA1700",
                  base_clock_ghz=2.5, turbo_clock_ghz=4.4),
    )
    name = generate_auto_name(v)
    assert "GB DDR" not in name
    # Только два блока: «Системный блок <CPU>» и «LGA1700».
    assert name == "Системный блок Intel Core i5-12400 2.5/4.4GHz / LGA1700"


# ---------------------------------------------------------------------
# Извлечение короткой модели
# ---------------------------------------------------------------------

def test_cpu_model_with_parentheses_and_oem_stripped():
    v = _variant(
        cpu=_comp("cpu",
                  model="Процессор LGA1700 Intel Core i5-12400F "
                        "(Alder Lake, 6C/12T, 2.5/4.4GHz, 12MB) OEM",
                  socket="LGA1700",
                  base_clock_ghz=2.5, turbo_clock_ghz=4.4),
    )
    name = generate_auto_name(v)
    assert "Intel Core i5-12400F 2.5/4.4GHz" in name
    assert "Alder Lake" not in name
    assert "OEM" not in name


def test_cpu_model_ultra_marker():
    v = _variant(
        cpu=_comp("cpu",
                  model="Intel Core Ultra 7 265KF",
                  socket="LGA1851",
                  base_clock_ghz=3.9, turbo_clock_ghz=5.5),
    )
    assert "Intel Core Ultra 7 265KF" in generate_auto_name(v)


def test_gpu_model_extracted_rtx():
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400",
                  base_clock_ghz=2.5, turbo_clock_ghz=4.4),
        gpu=_comp("gpu", model="GIGABYTE GeForce RTX 4060 Gaming OC 8G"),
    )
    # Первый кусок теперь — «Системный блок <CPU>», поэтому по « / »
    # он не разбивается и в parts не попадает. GPU-блок ищем в хвосте.
    parts = generate_auto_name(v).split(" / ")
    assert "RTX 4060" in parts


def test_gpu_model_extracted_radeon_rx():
    v = _variant(
        cpu=_comp("cpu", model="AMD Ryzen 5 7600",
                  base_clock_ghz=3.8, turbo_clock_ghz=5.1),
        gpu=_comp("gpu", model="Sapphire Radeon RX 7600 Pulse 8G"),
    )
    parts = generate_auto_name(v).split(" / ")
    assert "Radeon RX 7600" in parts


def test_gpu_model_unknown_marker_returns_raw_tail():
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400",
                  base_clock_ghz=2.5, turbo_clock_ghz=4.4),
        gpu=_comp("gpu", model="Некая видеокарта Model ABC"),
    )
    name = generate_auto_name(v)
    # Если маркер не найден — берём как есть (по ТЗ).
    assert "Некая видеокарта Model ABC" in name


def test_gpu_strips_category_prefix_when_no_marker():
    """Префикс «Видеокарта/» из прайса OCS должен отрезаться,
    если маркеры RTX/GTX/RX/Arc не найдены."""
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i3-12100F",
                  base_clock_ghz=3.3, turbo_clock_ghz=4.3),
        gpu=_comp("gpu", model="Видеокарта/ GT710-SL-2GD5-BRK-EVO"),
    )
    parts = generate_auto_name(v).split(" / ")
    assert "GT710-SL-2GD5-BRK-EVO" in parts
    # Префикс не просочился.
    assert not any("Видеокарта" in p for p in parts)


def test_gpu_prefers_marker_over_prefix_stripping():
    """Если маркер RTX/RX всё же найден — префикс не играет роли,
    выдаём короткое бытовое название."""
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400",
                  base_clock_ghz=2.5, turbo_clock_ghz=4.4),
        gpu=_comp("gpu", model="Видеокарта/ GeForce RTX 3050 LP E 6G OC"),
    )
    parts = generate_auto_name(v).split(" / ")
    assert "RTX 3050" in parts
    assert not any("Видеокарта" in p for p in parts)


# ---------------------------------------------------------------------
# Fallback
# ---------------------------------------------------------------------

def test_fallback_when_variant_empty():
    v = _variant(manufacturer="Intel")
    assert generate_auto_name(v, fallback_id=42) == "Конфигурация #42 · Intel"


def test_fallback_when_only_model_no_freq_returns_cpu_block_alone():
    # Тут CPU блок есть — он должен оказаться в имени, а не уйти в fallback.
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i3-12100F"),
    )
    name = generate_auto_name(v, fallback_id=7)
    # CPU клеится к префиксу через пробел, без слэша.
    assert name == "Системный блок Intel Core i3-12100F"
    assert "Конфигурация" not in name


def test_name_starts_with_prefix_space_cpu_not_slash():
    """Контрольный тест новой склейки: префикс и CPU — через пробел."""
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400F", socket="LGA1700",
                  base_clock_ghz=2.5, turbo_clock_ghz=4.4),
        ram=_comp("ram", memory_type="DDR4", module_size_gb=16, modules_count=1),
    )
    name = generate_auto_name(v)
    assert name.startswith("Системный блок Intel Core i5-12400F 2.5/4.4GHz / ")
    # После префикса перед CPU не должно быть слэша.
    assert not name.startswith("Системный блок /")


def test_prefix_uses_slash_when_cpu_block_missing():
    """Если CPU нет, но RAM есть — «Системный блок / 16GB DDR4»."""
    v = _variant(
        ram=_comp("ram", memory_type="DDR4", module_size_gb=16, modules_count=1),
    )
    name = generate_auto_name(v)
    assert name == "Системный блок / 16GB DDR4"


# ---------------------------------------------------------------------
# Socket берётся с материнки, если у CPU не задан
# ---------------------------------------------------------------------

def test_socket_fallback_to_motherboard():
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400",
                  base_clock_ghz=2.5, turbo_clock_ghz=4.4),
        motherboard=_comp("motherboard", socket="LGA1700", form_factor="ATX"),
    )
    parts = generate_auto_name(v).split(" / ")
    assert "LGA1700" in parts


# ---------------------------------------------------------------------
# RAM: суммарный объём × modules_count
# ---------------------------------------------------------------------

def test_ram_total_is_size_times_count():
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400",
                  base_clock_ghz=2.5, turbo_clock_ghz=4.4),
        ram=_comp("ram", memory_type="DDR5", module_size_gb=16, modules_count=2),
    )
    parts = generate_auto_name(v).split(" / ")
    assert "32GB DDR5" in parts


def test_ram_default_count_is_one():
    v = _variant(
        cpu=_comp("cpu", model="Intel Core i5-12400",
                  base_clock_ghz=2.5, turbo_clock_ghz=4.4),
        ram=_comp("ram", memory_type="DDR4", module_size_gb=16),  # count не задан
    )
    parts = generate_auto_name(v).split(" / ")
    assert "16GB DDR4" in parts
