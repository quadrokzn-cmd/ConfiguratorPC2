# Описание редактируемых полей для каждой категории и параметры CSV.
#
# Одно место правды для ручного редактирования:
#   - какой набор полей показывать в CSV по каждой категории;
#   - какие поля обязательны для бизнес-ассистента (именно они попадают
#     в --only-null);
#   - какие поля — массивы, и как сериализовать их в CSV.
#
# Список полей строго соответствует таблицам в migrations/001_init.sql.
# Поля id / category / model / manufacturer / sku считаются системными
# и не редактируются через CSV (см. SYSTEM_COLS).

from __future__ import annotations

# Системные колонки CSV — выводятся для идентификации, но при импорте
# игнорируются. Редактирование model / manufacturer / sku делается через
# scripts/edit_component.py --update.
SYSTEM_COLS: tuple[str, ...] = ("id", "category", "model", "manufacturer", "sku")

# Разделитель колонок CSV. Для Excel на русской Windows по умолчанию — ';'.
CSV_DELIMITER: str = ";"

# Разделитель элементов массива внутри одной ячейки. Используется для полей
# TEXT[] (supported_form_factors, supported_sockets).
ARRAY_CELL_SEP: str = "|"

# Специальное значение ячейки: «обнулить поле в БД» (записать NULL).
CLEAR_TOKEN: str = "__CLEAR__"

# Обязательные поля по категориям — должны соответствовать
# app/services/enrichment/runner.py::REQUIRED_FIELDS. Дублируем здесь
# осознанно: модуль 2.5А трогать нельзя, а нам нужна независимая схема.
REQUIRED_FIELDS: dict[str, list[str]] = {
    "cpu": [
        "socket", "cores", "threads",
        "base_clock_ghz", "turbo_clock_ghz",
        "tdp_watts", "has_integrated_graphics",
        "memory_type", "package_type",
    ],
    "motherboard": [
        "socket", "chipset", "form_factor", "memory_type", "has_m2_slot",
    ],
    "ram": [
        "memory_type", "form_factor",
        "module_size_gb", "modules_count", "frequency_mhz",
    ],
    "gpu": [
        "vram_gb", "vram_type", "tdp_watts", "needs_extra_power",
        "video_outputs", "core_clock_mhz", "memory_clock_mhz",
    ],
    "storage": [
        "storage_type", "form_factor", "interface", "capacity_gb",
    ],
    "case": [
        "supported_form_factors", "has_psu_included", "included_psu_watts",
    ],
    "psu": [
        "power_watts",
    ],
    "cooler": [
        "supported_sockets", "max_tdp_watts",
    ],
}

# Опциональные поля по категориям. Выводятся в CSV после обязательных,
# редактируются ассистентом так же.
OPTIONAL_FIELDS: dict[str, list[str]] = {
    "cpu": [
        "process_nm", "l3_cache_mb", "max_memory_freq", "release_year",
    ],
    "motherboard": [
        "memory_slots", "max_memory_gb", "max_memory_freq",
        "sata_ports", "m2_slots",
        "has_wifi", "has_bluetooth",
        "pcie_version", "pcie_x16_slots", "usb_ports",
    ],
    "ram": [
        "cl_timing", "voltage", "has_heatsink", "has_rgb",
    ],
    "gpu": [
        "gpu_chip", "recommended_psu_watts",
        "length_mm", "height_mm",
        "power_connectors", "fans_count",
    ],
    "storage": [
        "read_speed_mb", "write_speed_mb", "tbw", "rpm", "cache_mb",
    ],
    "case": [
        "max_gpu_length_mm", "max_cooler_height_mm",
        "psu_form_factor", "color", "material",
        "drive_bays", "fans_included",
        "has_glass_panel", "has_rgb",
    ],
    "psu": [
        "form_factor", "efficiency_rating", "modularity",
        "has_12vhpwr", "sata_connectors",
        "main_cable_length_mm", "warranty_years",
    ],
    "cooler": [
        "cooler_type", "height_mm", "radiator_size_mm",
        "fans_count", "noise_db", "has_rgb",
    ],
}

# Поля типа массивов — сериализуются в CSV как "A|B|C".
ARRAY_FIELDS: dict[str, set[str]] = {
    "case":   {"supported_form_factors"},
    "cooler": {"supported_sockets"},
}


def all_fields(category: str) -> list[str]:
    """Полный список полей категории в порядке вывода CSV: обязательные, затем опциональные."""
    return REQUIRED_FIELDS.get(category, []) + OPTIONAL_FIELDS.get(category, [])


def csv_header(category: str) -> list[str]:
    """Колонки CSV для категории: системные + все поля."""
    return list(SYSTEM_COLS) + all_fields(category)


def is_array_field(category: str, field_name: str) -> bool:
    return field_name in ARRAY_FIELDS.get(category, set())


# Категории в порядке, удобном для CLI --all.
ALL_CATEGORIES: list[str] = [
    "cpu", "motherboard", "ram", "gpu",
    "storage", "case", "psu", "cooler",
]

# Источник, под которым пишутся правки в component_field_sources.
SOURCE_MANUAL: str = "manual"
