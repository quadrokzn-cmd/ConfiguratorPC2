# Базовые типы модуля обогащения характеристик компонентов.

from dataclasses import dataclass
from typing import Any


@dataclass
class ExtractedField:
    """Одно извлечённое значение характеристики.

    source:     откуда получено значение — 'regex' / 'derived' / 'ai' / 'manual' / ...
    confidence: уверенность 0..1 (для regex и derived обычно 1.0).
    """
    value: Any
    source: str
    confidence: float = 1.0


# Соответствие внешней категории (значение колонки category в supplier_prices
# и в component_field_sources) и имени таблицы компонентов в БД.
CATEGORY_TO_TABLE = {
    "cpu":         "cpus",
    "motherboard": "motherboards",
    "ram":         "rams",
    "gpu":         "gpus",
    "storage":     "storages",
    "case":        "cases",
    "psu":         "psus",
    "cooler":      "coolers",
}

# Белый список таблиц для безопасной подстановки в SQL
ALLOWED_TABLES = frozenset(CATEGORY_TO_TABLE.values())
