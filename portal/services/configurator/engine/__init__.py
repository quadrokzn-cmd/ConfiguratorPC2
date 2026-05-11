# Модуль подбора конфигурации ПК (этап 3).
#
# Точка входа: selector.build_config(request) -> BuildResult.
#
# Состав:
#   - schema.py      — dataclass-ы входного запроса и выходного результата;
#   - candidates.py  — SQL-поиск компонентов-кандидатов в БД;
#   - prices.py      — выбор поставщика, конвертация RUB→USD через fx;
#   - builder.py     — сборка одной конфигурации вокруг конкретного CPU;
#   - selector.py    — управляющая логика: Intel/AMD, пути A/B, транзит;
#   - warnings.py    — генерация предупреждений к готовой сборке;
#   - pretty.py      — форматированный вывод в консоль для CLI.

from portal.services.configurator.engine.schema import (
    BuildRequest,
    BuildResult,
    ComponentChoice,
    CPURequirements,
    FixedRef,
    GPURequirements,
    RAMRequirements,
    StorageRequirements,
    SupplierOffer,
    Variant,
    request_from_dict,
    result_to_dict,
)
from portal.services.configurator.engine.selector import build_config

__all__ = [
    "BuildRequest",
    "BuildResult",
    "ComponentChoice",
    "CPURequirements",
    "FixedRef",
    "GPURequirements",
    "RAMRequirements",
    "StorageRequirements",
    "SupplierOffer",
    "Variant",
    "build_config",
    "request_from_dict",
    "result_to_dict",
]
