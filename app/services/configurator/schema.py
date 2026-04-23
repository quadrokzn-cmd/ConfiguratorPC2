# Dataclass-ы входного запроса и выходного результата подбора конфигурации.
#
# Используем только stdlib-dataclass (как во всём проекте — без pydantic
# на уровне сервис-слоя). Функция request_from_dict принимает обычный
# dict (например, распаршенный JSON) и возвращает типизированный запрос.

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


# -----------------------------------------------------------------------------
# Входной запрос
# -----------------------------------------------------------------------------

@dataclass
class FixedRef:
    """Ссылка на зафиксированный пользователем компонент: по id или SKU."""
    id: int | None = None
    sku: str | None = None

    def is_set(self) -> bool:
        return self.id is not None or (self.sku is not None and self.sku.strip() != "")


@dataclass
class CPURequirements:
    fixed: FixedRef | None = None
    min_cores: int | None = None
    min_threads: int | None = None
    min_base_ghz: float | None = None
    # Строка "Intel" / "AMD" — если заданo, подбираем только этого производителя.
    manufacturer: str | None = None


@dataclass
class RAMRequirements:
    min_gb: int | None = None
    min_frequency_mhz: int | None = None
    # "DDR4" / "DDR5" — если не задано, берём тот, что поддерживает MB.
    memory_type: str | None = None


@dataclass
class GPURequirements:
    fixed: FixedRef | None = None
    # True => GPU обязательна в сборке. Если False и нет минимумов — GPU не
    # обязательна, работает ветка «экономия» (пути A и B).
    required: bool = False
    min_vram_gb: int | None = None


@dataclass
class StorageRequirements:
    min_gb: int | None = None
    preferred_type: str | None = None    # "SSD" / "HDD"


@dataclass
class BuildRequest:
    budget_usd: float | None = None
    cpu: CPURequirements = field(default_factory=CPURequirements)
    ram: RAMRequirements = field(default_factory=RAMRequirements)
    gpu: GPURequirements = field(default_factory=GPURequirements)
    storage: StorageRequirements = field(default_factory=StorageRequirements)
    motherboard: FixedRef | None = None
    case: FixedRef | None = None
    psu: FixedRef | None = None
    cooler: FixedRef | None = None
    # Управляется модулем автоматически: если без транзита сборка не вышла —
    # selector сам повторяет подбор с allow_transit=True.
    allow_transit: bool = False

    def is_empty(self) -> bool:
        """Запрос считается пустым, если не задано ни одного требования."""
        if self.budget_usd is not None:
            return False
        if self.cpu.min_cores or self.cpu.min_threads or self.cpu.min_base_ghz \
                or self.cpu.manufacturer or (self.cpu.fixed and self.cpu.fixed.is_set()):
            return False
        if self.ram.min_gb or self.ram.min_frequency_mhz or self.ram.memory_type:
            return False
        if self.gpu.required or self.gpu.min_vram_gb \
                or (self.gpu.fixed and self.gpu.fixed.is_set()):
            return False
        if self.storage.min_gb or self.storage.preferred_type:
            return False
        for ref in (self.motherboard, self.case, self.psu, self.cooler):
            if ref and ref.is_set():
                return False
        return True


# -----------------------------------------------------------------------------
# Выходной результат
# -----------------------------------------------------------------------------

@dataclass
class SupplierOffer:
    """Предложение одного поставщика по конкретному компоненту."""
    supplier: str
    price_usd: float
    price_rub: float
    stock: int
    in_transit: bool = False   # True, если позиция взята из transit_qty
    # Номенклатурный номер компонента у конкретного поставщика
    # (supplier_prices.supplier_sku). Нужен менеджеру для оформления
    # заказа у поставщика. None, если поставщик его не указал.
    supplier_sku: str | None = None


@dataclass
class ComponentChoice:
    """Выбранный компонент в сборке."""
    category: str             # cpu|motherboard|ram|gpu|storage|case|psu|cooler
    component_id: int
    model: str
    sku: str | None
    manufacturer: str          # как в БД ("Intel Corporation", "AMD", ...)
    chosen: SupplierOffer
    also_available_at: list[SupplierOffer] = field(default_factory=list)
    quantity: int = 1          # для RAM — число модулей; для остальных = 1


@dataclass
class Variant:
    """Один завершённый вариант сборки (Intel или AMD)."""
    manufacturer: str          # нормализованное "Intel" / "AMD"
    components: list[ComponentChoice]
    total_usd: float
    total_rub: float
    warnings: list[str] = field(default_factory=list)
    used_transit: bool = False
    # Аналитика: какой путь дал минимум.
    #   'default' — GPU была обязательна (или зафиксирована),
    #   'A'       — путь экономии на GPU (CPU с iGPU, без дискретной),
    #   'B'       — путь экономии на CPU (любой CPU + дешёвая дискретная).
    path_used: str = "default"


@dataclass
class BuildResult:
    """Итоговый ответ подбора."""
    status: str                           # "ok" | "partial" | "failed"
    variants: list[Variant]
    refusal_reason: dict[str, str] | None  # {"intel": "...", "amd": "..."} или None
    usd_rub_rate: float
    fx_source: str                        # "cbr" | "cache" | "fallback"


# -----------------------------------------------------------------------------
# JSON ↔ dataclass
# -----------------------------------------------------------------------------

def _as_fixed_ref(raw: Any) -> FixedRef | None:
    """Достаёт FixedRef из произвольного dict. Принимает и старые имена
    fixed_id / fixed_sku, и вложенный dict 'fixed'."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    # вариант 1: вложенный {"fixed": {"id": ..., "sku": ...}}
    fixed = raw.get("fixed")
    if isinstance(fixed, dict):
        return FixedRef(
            id=fixed.get("id"),
            sku=fixed.get("sku"),
        )
    # вариант 2: плоские fixed_id / fixed_sku прямо на верхнем уровне блока
    fid = raw.get("fixed_id")
    fsku = raw.get("fixed_sku")
    if fid is not None or fsku is not None:
        return FixedRef(id=fid, sku=fsku)
    return None


def _as_fixed_block(raw: Any) -> FixedRef | None:
    """Для блоков motherboard/case/psu/cooler: принимает {"fixed_id": ...}
    или {"id": ..., "sku": ...}."""
    if not isinstance(raw, dict):
        return None
    fid = raw.get("fixed_id", raw.get("id"))
    fsku = raw.get("fixed_sku", raw.get("sku"))
    if fid is None and not fsku:
        return None
    return FixedRef(id=fid, sku=fsku)


def request_from_dict(data: dict) -> BuildRequest:
    """Строит BuildRequest из обычного словаря (распаршенный JSON)."""
    cpu_raw = data.get("cpu") or {}
    ram_raw = data.get("ram") or {}
    gpu_raw = data.get("gpu") or {}
    st_raw = data.get("storage") or {}

    cpu = CPURequirements(
        fixed=_as_fixed_ref(cpu_raw),
        min_cores=cpu_raw.get("min_cores"),
        min_threads=cpu_raw.get("min_threads"),
        min_base_ghz=cpu_raw.get("min_base_ghz"),
        manufacturer=cpu_raw.get("manufacturer"),
    )
    ram = RAMRequirements(
        min_gb=ram_raw.get("min_gb"),
        min_frequency_mhz=ram_raw.get("min_frequency_mhz"),
        memory_type=ram_raw.get("memory_type"),
    )
    gpu = GPURequirements(
        fixed=_as_fixed_ref(gpu_raw),
        required=bool(gpu_raw.get("required", False)),
        min_vram_gb=gpu_raw.get("min_vram_gb"),
    )
    storage = StorageRequirements(
        min_gb=st_raw.get("min_gb"),
        preferred_type=st_raw.get("preferred_type"),
    )

    return BuildRequest(
        budget_usd=data.get("budget_usd"),
        cpu=cpu,
        ram=ram,
        gpu=gpu,
        storage=storage,
        motherboard=_as_fixed_block(data.get("motherboard")),
        case=_as_fixed_block(data.get("case")),
        psu=_as_fixed_block(data.get("psu")),
        cooler=_as_fixed_block(data.get("cooler")),
        allow_transit=bool(data.get("allow_transit", False)),
    )


def result_to_dict(result: BuildResult) -> dict:
    """Плоская JSON-представление результата."""
    return {
        "status": result.status,
        "variants": [
            {
                "manufacturer": v.manufacturer,
                "path_used": v.path_used,
                "used_transit": v.used_transit,
                "total_usd": round(v.total_usd, 2),
                "total_rub": round(v.total_rub, 2),
                "components": [
                    {
                        "category": c.category,
                        "component_id": c.component_id,
                        "model": c.model,
                        "sku": c.sku,
                        "manufacturer": c.manufacturer,
                        "quantity": c.quantity,
                        "supplier": c.chosen.supplier,
                        "supplier_sku": c.chosen.supplier_sku,
                        "price_usd": round(c.chosen.price_usd, 2),
                        "price_rub": round(c.chosen.price_rub, 2),
                        "stock": c.chosen.stock,
                        "in_transit": c.chosen.in_transit,
                        "also_available_at": [asdict(o) for o in c.also_available_at],
                    }
                    for c in v.components
                ],
                "warnings": list(v.warnings),
            }
            for v in result.variants
        ],
        "refusal_reason": result.refusal_reason,
        "usd_rub_rate": round(result.usd_rub_rate, 4),
        "fx_source": result.fx_source,
    }
