# Сборка BuildRequest из ParsedRequest + дефолтного профиля + найденных моделей.
#
# Логика:
#   1. Берём базовый словарь из профилей.PROFILES[parsed.purpose] (или {}).
#   2. Накатываем сверху parsed.overrides (переопределяют профиль).
#   3. Кладём cpu_manufacturer и budget_usd.
#   4. Конвертируем плоский словарь в BuildRequest через request_from_dict.
#   5. Применяем resolved-mentions: для каждой найденной модели ставим
#      FixedRef(id=resolved.found_id) в соответствующий блок.

from __future__ import annotations

from typing import Any

from app.services.configurator.schema import (
    BuildRequest,
    FixedRef,
    request_from_dict,
)
from app.services.nlu.profiles import get_profile
from app.services.nlu.schema import ParsedRequest, ResolvedMention


def _to_request_dict(merged: dict[str, Any]) -> dict[str, Any]:
    """Превращает плоский словарь (как в profiles + overrides) в формат,
    ожидаемый request_from_dict (с вложенными блоками cpu/ram/gpu/storage)."""
    out: dict[str, Any] = {}

    if merged.get("budget_usd") is not None:
        out["budget_usd"] = float(merged["budget_usd"])

    cpu: dict[str, Any] = {}
    if merged.get("cpu_min_cores") is not None:
        cpu["min_cores"] = int(merged["cpu_min_cores"])
    if merged.get("cpu_min_threads") is not None:
        cpu["min_threads"] = int(merged["cpu_min_threads"])
    if merged.get("cpu_min_base_ghz") is not None:
        cpu["min_base_ghz"] = float(merged["cpu_min_base_ghz"])
    if merged.get("cpu_manufacturer"):
        # CPURequirements.manufacturer — строка, селектор сравнивает в lowercase
        cpu["manufacturer"] = str(merged["cpu_manufacturer"]).lower()
    if cpu:
        out["cpu"] = cpu

    ram: dict[str, Any] = {}
    if merged.get("ram_min_gb") is not None:
        ram["min_gb"] = int(merged["ram_min_gb"])
    if merged.get("ram_min_freq_mhz") is not None:
        ram["min_frequency_mhz"] = int(merged["ram_min_freq_mhz"])
    if merged.get("ram_memory_type"):
        ram["memory_type"] = str(merged["ram_memory_type"])
    if ram:
        out["ram"] = ram

    gpu: dict[str, Any] = {}
    if merged.get("gpu_required") is not None:
        gpu["required"] = bool(merged["gpu_required"])
    if merged.get("gpu_min_vram_gb") is not None:
        gpu["min_vram_gb"] = int(merged["gpu_min_vram_gb"])
    if gpu:
        out["gpu"] = gpu

    storage: dict[str, Any] = {}
    if merged.get("storage_min_gb") is not None:
        storage["min_gb"] = int(merged["storage_min_gb"])
    if merged.get("storage_type"):
        storage["preferred_type"] = str(merged["storage_type"])
    if storage:
        out["storage"] = storage

    return out


def _apply_resolved(req: BuildRequest, resolved: list[ResolvedMention]) -> None:
    """Устанавливает FixedRef в соответствующих блоках BuildRequest для
    найденных моделей. Не найденные mentions игнорирует."""
    for r in resolved:
        if r.found_id is None:
            continue
        cat = r.mention.category
        ref = FixedRef(id=int(r.found_id), sku=r.found_sku)
        if cat == "cpu":
            req.cpu.fixed = ref
        elif cat == "gpu":
            req.gpu.fixed = ref
            # Если зафиксирована конкретная GPU — она обязательна в сборке
            req.gpu.required = True
        elif cat == "motherboard":
            req.motherboard = ref
        elif cat == "case":
            req.case = ref
        elif cat == "psu":
            req.psu = ref
        elif cat == "cooler":
            req.cooler = ref
        # ram и storage в BuildRequest не имеют FixedRef — их менеджер
        # обычно не фиксирует точечно. Если упомянули — игнорируем фикс,
        # но требования из профиля/overrides уже учтены.


def build(
    parsed: ParsedRequest,
    resolved: list[ResolvedMention] | None = None,
) -> BuildRequest:
    """Собирает финальный BuildRequest для подачи в build_config."""
    profile = get_profile(parsed.purpose)
    merged: dict[str, Any] = dict(profile)

    # overrides всегда перекрывают профиль (даже если значение явно null —
    # но parser отдаёт только заполненные ключи, так что .update безопасен).
    merged.update(parsed.overrides or {})

    # Бюджет и manufacturer не входят в overrides — это отдельные поля.
    if parsed.budget_usd is not None:
        merged["budget_usd"] = float(parsed.budget_usd)
    if parsed.cpu_manufacturer:
        merged["cpu_manufacturer"] = parsed.cpu_manufacturer

    req = request_from_dict(_to_request_dict(merged))

    if resolved:
        _apply_resolved(req, resolved)

    return req
