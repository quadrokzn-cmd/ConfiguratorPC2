# Валидаторы для ручного редактирования.
#
# Покрывают ВСЕ поля из manual_edit.schema (обязательные + опциональные).
# Для обязательных полей диапазоны согласованы с claude_code/validators.py,
# чтобы не разъезжаться между источниками.
#
# Базовые парсеры _as_int / _as_bool / _as_decimal / _as_enum / _as_str
# переиспользуются из модуля 2.5Б (claude_code.validators). Сам модуль
# 2.5Б не меняем — импорт приватных имён оставляет его нетронутым.

from __future__ import annotations

from decimal import Decimal
from typing import Any

from portal.services.configurator.enrichment.claude_code.validators import (
    ValidationError,
    _as_bool as as_bool,
    _as_decimal as as_decimal,
    _as_enum as as_enum,
    _as_int as as_int,
    _as_str as as_str,
)

# Реэкспортируем класс ошибок для удобства импорта из importer / editor.
__all__ = ["ValidationError", "validate_field", "is_known_field"]


# -----------------------------------------------------------------------------
# Наборы значений-справочников
# -----------------------------------------------------------------------------

# Сокеты CPU/материнских плат/кулеров. Список расширяемый — только для
# грубой проверки; точная совместимость проверяется в конфигураторе.
_CPU_SOCKETS = {
    "AM4", "AM5", "TR4", "STRX4", "SWRX8",
    "LGA1151", "LGA1151-V2", "LGA1200", "LGA1700", "LGA1851",
    "LGA2011", "LGA2011-3", "LGA2066",
    "LGA3647", "LGA4189", "LGA4677",
    "SP3", "SP5", "SP6",
}

_MEMORY_TYPES = {"DDR3", "DDR3L", "DDR4", "DDR5", "DDR4+DDR5"}
_RAM_FORM_FACTORS = {"DIMM", "SO-DIMM", "UDIMM", "RDIMM", "LRDIMM"}

_VRAM_TYPES = {
    "DDR3", "DDR4", "DDR5",
    "GDDR5", "GDDR5X", "GDDR6", "GDDR6X", "GDDR7",
    "HBM", "HBM2", "HBM2E", "HBM3",
}

_MB_FORM_FACTORS = {"E-ATX", "ATX", "MATX", "ITX", "XL-ATX", "SSI-EEB", "SSI-CEB"}
_MB_FORM_FACTOR_NORMALIZE = {
    "MICRO-ATX": "MATX", "MICROATX": "MATX", "M-ATX": "MATX",
    "MINI-ITX":  "ITX",  "MINIITX":  "ITX",
    "EATX":      "E-ATX",
}

_CASE_FORM_FACTORS = _MB_FORM_FACTORS  # совпадают по набору

_STORAGE_TYPES = {"SSD", "HDD"}
_STORAGE_FORM_FACTORS = {"M.2", "2.5", '2.5"', "3.5", '3.5"', "MSATA", "U.2"}
_STORAGE_FORM_NORMALIZE = {
    "2.5\"": "2.5", "3.5\"": "3.5",
}
_STORAGE_INTERFACES = {"NVME", "SATA", "PCIE", "SAS", "MSATA"}

_CPU_PACKAGE_TYPES = {"OEM", "BOX", "TRAY"}

_PSU_FORM_FACTORS = {"ATX", "SFX", "SFX-L", "TFX", "FLEX-ATX", "CFX", "ATX12VO"}
_PSU_EFFICIENCY = {
    "BRONZE", "SILVER", "GOLD", "PLATINUM", "TITANIUM",
    "80PLUS", "80+", "STANDARD",
}
_PSU_MODULARITY = {
    "MODULAR", "SEMI-MODULAR", "NON-MODULAR",
    "ПОЛНАЯ", "ПОЛУМОДУЛЬНАЯ", "НЕМОДУЛЬНАЯ",
}

_COOLER_TYPES = {"AIR", "LIQUID", "ВОЗДУШНЫЙ", "ЖИДКОСТНЫЙ", "AIO"}

_PCIE_VERSIONS = {"3.0", "4.0", "5.0", "6.0"}

_POWER_CONNECTORS = {
    "NONE", "6PIN", "8PIN", "6+8PIN", "2X8PIN", "3X8PIN",
    "12VHPWR", "16PIN", "12V-2X6",
}


def _v_int(lo: int, hi: int):
    return lambda v: as_int(v, lo=lo, hi=hi)


def _v_decimal(lo: float, hi: float):
    return lambda v: as_decimal(v, lo=lo, hi=hi)


def _v_str(min_len: int, max_len: int):
    return lambda v: as_str(v, min_len=min_len, max_len=max_len)


def _v_enum(allowed: set[str], normalize: dict[str, str] | None = None):
    return lambda v: as_enum(v, allowed=allowed, normalize_map=normalize)


def _v_array(allowed: set[str], normalize: dict[str, str] | None = None):
    """Валидация массива значений (TEXT[]): список, каждый элемент — enum.

    На вход — list[str] (после парсинга CSV-ячейки через ARRAY_CELL_SEP).
    Возвращает list[str] с удалёнными дублями, в исходном порядке.
    """
    def _check(value: Any) -> list[str]:
        if not isinstance(value, list) or not value:
            raise ValidationError(
                f"wrong_type:not_nonempty_list({type(value).__name__})"
            )
        out: list[str] = []
        seen: set[str] = set()
        for item in value:
            ff = as_enum(item, allowed=allowed, normalize_map=normalize)
            if ff not in seen:
                seen.add(ff)
                out.append(ff)
        return out
    return _check


# -----------------------------------------------------------------------------
# Регистр валидаторов: (категория, поле) -> функция проверки.
# Диапазоны по обязательным полям согласованы с claude_code/validators.py.
# -----------------------------------------------------------------------------
_VALIDATORS: dict[tuple[str, str], Any] = {
    # CPU — обязательные
    ("cpu", "socket"):          _v_enum(_CPU_SOCKETS),
    ("cpu", "cores"):           _v_int(1, 256),
    ("cpu", "threads"):         _v_int(1, 512),
    ("cpu", "base_clock_ghz"):  _v_decimal(0.5, 6.0),
    ("cpu", "turbo_clock_ghz"): _v_decimal(0.5, 7.0),
    ("cpu", "tdp_watts"):       _v_int(1, 500),
    ("cpu", "has_integrated_graphics"): as_bool,
    ("cpu", "memory_type"):     _v_enum(_MEMORY_TYPES),
    ("cpu", "package_type"):    _v_enum(_CPU_PACKAGE_TYPES),
    # CPU — опциональные
    ("cpu", "process_nm"):      _v_int(1, 200),
    ("cpu", "l3_cache_mb"):     _v_int(0, 1024),
    ("cpu", "max_memory_freq"): _v_int(800, 12000),
    ("cpu", "release_year"):    _v_int(1990, 2100),

    # Motherboard — обязательные
    ("motherboard", "socket"):      _v_enum(_CPU_SOCKETS),
    ("motherboard", "chipset"):     _v_str(2, 50),
    ("motherboard", "form_factor"): _v_enum(_MB_FORM_FACTORS, _MB_FORM_FACTOR_NORMALIZE),
    ("motherboard", "memory_type"): _v_enum(_MEMORY_TYPES),
    ("motherboard", "has_m2_slot"): as_bool,
    # Motherboard — опциональные
    ("motherboard", "memory_slots"):     _v_int(1, 16),
    ("motherboard", "max_memory_gb"):    _v_int(1, 8192),
    ("motherboard", "max_memory_freq"):  _v_int(800, 12000),
    ("motherboard", "sata_ports"):       _v_int(0, 16),
    ("motherboard", "m2_slots"):         _v_int(0, 12),
    ("motherboard", "has_wifi"):         as_bool,
    ("motherboard", "has_bluetooth"):    as_bool,
    ("motherboard", "pcie_version"):     _v_enum(_PCIE_VERSIONS),
    ("motherboard", "pcie_x16_slots"):   _v_int(0, 8),
    ("motherboard", "usb_ports"):        _v_int(0, 32),

    # RAM — обязательные
    ("ram", "memory_type"):    _v_enum(_MEMORY_TYPES),
    ("ram", "form_factor"):    _v_enum(_RAM_FORM_FACTORS),
    ("ram", "module_size_gb"): _v_int(1, 512),
    ("ram", "modules_count"):  _v_int(1, 16),
    ("ram", "frequency_mhz"):  _v_int(800, 12000),
    # RAM — опциональные
    ("ram", "cl_timing"):   _v_int(5, 80),
    ("ram", "voltage"):     _v_decimal(0.5, 2.5),
    ("ram", "has_heatsink"): as_bool,
    ("ram", "has_rgb"):     as_bool,

    # GPU — обязательные
    ("gpu", "vram_gb"):          _v_int(1, 128),
    ("gpu", "vram_type"):        _v_enum(_VRAM_TYPES),
    ("gpu", "tdp_watts"):        _v_int(10, 600),
    ("gpu", "needs_extra_power"): as_bool,
    ("gpu", "video_outputs"):    _v_str(3, 200),
    ("gpu", "core_clock_mhz"):   _v_int(100, 4000),
    ("gpu", "memory_clock_mhz"): _v_int(500, 40000),
    # GPU — опциональные
    ("gpu", "gpu_chip"):               _v_str(1, 100),
    ("gpu", "recommended_psu_watts"):  _v_int(100, 2000),
    ("gpu", "length_mm"):              _v_int(50, 500),
    ("gpu", "height_mm"):              _v_int(20, 200),
    ("gpu", "power_connectors"):       _v_enum(_POWER_CONNECTORS),
    ("gpu", "fans_count"):             _v_int(0, 5),

    # Storage — обязательные
    ("storage", "storage_type"): _v_enum(_STORAGE_TYPES),
    ("storage", "form_factor"):  _v_enum(_STORAGE_FORM_FACTORS, _STORAGE_FORM_NORMALIZE),
    ("storage", "interface"):    _v_enum(_STORAGE_INTERFACES),
    ("storage", "capacity_gb"):  _v_int(1, 1048576),
    # Storage — опциональные
    ("storage", "read_speed_mb"):  _v_int(10, 20000),
    ("storage", "write_speed_mb"): _v_int(10, 20000),
    ("storage", "tbw"):            _v_int(10, 30000),
    ("storage", "rpm"):            _v_int(3000, 15000),
    ("storage", "cache_mb"):       _v_int(0, 8192),

    # Case — обязательные
    ("case", "supported_form_factors"):
        _v_array(_CASE_FORM_FACTORS, _MB_FORM_FACTOR_NORMALIZE),
    ("case", "has_psu_included"):   as_bool,
    ("case", "included_psu_watts"): _v_int(100, 2000),
    # Case — опциональные
    ("case", "max_gpu_length_mm"):    _v_int(100, 600),
    ("case", "max_cooler_height_mm"): _v_int(30, 300),
    ("case", "psu_form_factor"):      _v_enum(_PSU_FORM_FACTORS),
    ("case", "color"):                _v_str(1, 50),
    ("case", "material"):             _v_str(1, 50),
    ("case", "drive_bays"):           _v_int(0, 20),
    ("case", "fans_included"):        _v_int(0, 20),
    ("case", "has_glass_panel"):      as_bool,
    ("case", "has_rgb"):              as_bool,

    # PSU — обязательные
    ("psu", "power_watts"):           _v_int(5, 3000),
    # PSU — опциональные
    ("psu", "form_factor"):           _v_enum(_PSU_FORM_FACTORS),
    ("psu", "efficiency_rating"):     _v_enum(_PSU_EFFICIENCY),
    ("psu", "modularity"):            _v_enum(_PSU_MODULARITY),
    ("psu", "has_12vhpwr"):           as_bool,
    ("psu", "sata_connectors"):       _v_int(0, 20),
    ("psu", "main_cable_length_mm"):  _v_int(100, 2000),
    ("psu", "warranty_years"):        _v_int(1, 20),

    # Cooler — обязательные
    ("cooler", "supported_sockets"):
        _v_array(_CPU_SOCKETS),
    ("cooler", "max_tdp_watts"):      _v_int(30, 500),
    # Cooler — опциональные
    ("cooler", "cooler_type"):        _v_enum(_COOLER_TYPES),
    ("cooler", "height_mm"):          _v_int(30, 200),
    ("cooler", "radiator_size_mm"):   _v_int(0, 420),
    ("cooler", "fans_count"):         _v_int(1, 5),
    ("cooler", "noise_db"):           _v_decimal(0.0, 80.0),
    ("cooler", "has_rgb"):            as_bool,
}


def is_known_field(category: str, field_name: str) -> bool:
    """True, если поле описано в нашей схеме ручного редактирования."""
    return (category, field_name) in _VALIDATORS


def validate_field(category: str, field_name: str, raw: Any) -> Any:
    """Валидирует одно поле. Возвращает нормализованное значение.

    Бросает ValidationError с коротким кодом причины.
    Для поля типа массив на вход ожидается list[str] (см. csv_io.parse_cell).
    """
    key = (category, field_name)
    if key not in _VALIDATORS:
        raise ValidationError(f"unknown_field:{category}.{field_name}")
    return _VALIDATORS[key](raw)
