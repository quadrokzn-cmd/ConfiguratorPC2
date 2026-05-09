# Базовые типы модуля обогащения характеристик компонентов.

from dataclasses import dataclass
from typing import Any


@dataclass
class ExtractedField:
    """Одно извлечённое значение характеристики.

    source:     откуда получено значение — 'regex' / 'derived' / 'ai' / 'manual' /
                'claude_code' / ...
    confidence: уверенность 0..1 (для regex и derived обычно 1.0).
    source_url: URL официальной страницы источника (для 'claude_code' / 'ai').
                Для regex/derived/manual — None.
    """
    value: Any
    source: str
    confidence: float = 1.0
    source_url: str | None = None


# Соответствие внешней категории (значение колонки category в supplier_prices
# и в component_field_sources) и имени таблицы компонентов в БД.
#
# Этап 6 слияния (2026-05-08): добавлены 'printer' и 'mfu' → 'printers_mfu'.
# Это единая 9-я таблица каталога, унаследованная из QT-овской nomenclature
# (см. migrations/031_printers_mfu.sql). category в supplier_prices при
# записи остаётся 'printer' / 'mfu' (как в адаптерах), но реальная запись
# идёт в одну таблицу printers_mfu.
CATEGORY_TO_TABLE = {
    "cpu":         "cpus",
    "motherboard": "motherboards",
    "ram":         "rams",
    "gpu":         "gpus",
    "storage":     "storages",
    "case":        "cases",
    "psu":         "psus",
    "cooler":      "coolers",
    "printer":     "printers_mfu",
    "mfu":         "printers_mfu",
}

# Белый список таблиц для безопасной подстановки в SQL.
# printers_mfu добавлена через CATEGORY_TO_TABLE.values() — см. комментарий выше.
ALLOWED_TABLES = frozenset(CATEGORY_TO_TABLE.values())
