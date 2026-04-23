# Дефолтные профили — базовые требования, если менеджер указал назначение,
# но не задал конкретные характеристики.
#
# Если менеджер укажет что-то явно (например, "офисный ПК с 16 ГБ") — это
# перекроет соответствующее поле профиля (см. request_builder).
#
# Профили хранятся как обычные словари — легко править и расширять.
#
# ВАЖНО про OFFICE: требование "интегрированная графика" в BuildRequest
# напрямую не выражается. Селектор сам разрулит: при gpu.required=False
# он попробует путь A (CPU с iGPU, без дискретной GPU) и путь B (любой CPU
# + дешёвая дискретная) и возьмёт минимум. Для офисных запросов путь A
# почти всегда дешевле, поэтому сборка получится с iGPU.

from __future__ import annotations

from typing import Any


# Каждое поле — то же имя, что используют overrides в ParsedRequest и
# что потом попадёт в BuildRequest. См. request_builder.py.

OFFICE: dict[str, Any] = {
    "cpu_min_cores":    2,
    "ram_min_gb":       8,
    "ram_min_freq_mhz": 2666,
    "gpu_required":     False,
    "storage_min_gb":   240,
    "storage_type":     "SSD",
}

HOME: dict[str, Any] = {
    "cpu_min_cores":    4,
    "ram_min_gb":       16,
    "ram_min_freq_mhz": 3000,
    "gpu_required":     False,
    "storage_min_gb":   500,
    "storage_type":     "SSD",
}

GAMING: dict[str, Any] = {
    "cpu_min_cores":    6,
    "cpu_min_threads":  12,
    "ram_min_gb":       16,
    "ram_min_freq_mhz": 3200,
    "gpu_required":     True,
    "gpu_min_vram_gb":  8,
    "storage_min_gb":   500,
    "storage_type":     "SSD",
}

WORKSTATION: dict[str, Any] = {
    "cpu_min_cores":    8,
    "cpu_min_threads":  16,
    "ram_min_gb":       32,
    "ram_min_freq_mhz": 3200,
    "gpu_required":     True,
    "gpu_min_vram_gb":  8,
    "storage_min_gb":   1000,
    "storage_type":     "SSD",
}


PROFILES: dict[str, dict[str, Any]] = {
    "office":      OFFICE,
    "home":        HOME,
    "gaming":      GAMING,
    "workstation": WORKSTATION,
}


# Человекочитаемые названия — для сообщений менеджеру.
PROFILE_LABELS: dict[str, str] = {
    "office":      "офисный",
    "home":        "домашний",
    "gaming":      "игровой",
    "workstation": "рабочая станция",
}


def get_profile(purpose: str | None) -> dict[str, Any]:
    """Возвращает копию словаря профиля. Если purpose=None или неизвестен —
    возвращает пустой словарь (никаких дефолтов, всё задаётся overrides)."""
    if not purpose:
        return {}
    return dict(PROFILES.get(purpose, {}))
