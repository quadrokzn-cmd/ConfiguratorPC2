# Пакет загрузчиков прайс-листов поставщиков (этап 7).
#
# Структура:
#   models.py       — унифицированный PriceRow
#   base.py         — интерфейс BasePriceLoader
#   ocs.py          — адаптер OCS (Excel, лист «Наличие и цены»)
#   merlion.py      — адаптер Merlion (Excel, лист «Price List», заголовки со строки 11)
#   treolan.py      — адаптер Treolan (Excel, лист «Каталог», категории через «->»)
#   matching.py     — автосопоставление по MPN/GTIN и supplier_prices
#   orchestrator.py — общий раннер: loader → matching → supplier_prices / unmapped
#   candidates.py   — подбор «похожих» кандидатов для /admin/mapping
#
# Фабрика get_loader / detect_loader — единственная точка, где имена
# поставщиков (строки 'ocs', 'merlion', 'treolan') сопоставляются классам.

from __future__ import annotations

from pathlib import Path

from app.services.price_loaders.base import BasePriceLoader
from app.services.price_loaders.merlion import MerlionLoader
from app.services.price_loaders.ocs import OcsLoader
from app.services.price_loaders.treolan import TreolanLoader


# Единый источник истины: ключ CLI/API → класс адаптера.
LOADERS: dict[str, type[BasePriceLoader]] = {
    "ocs":     OcsLoader,
    "merlion": MerlionLoader,
    "treolan": TreolanLoader,
}


def get_loader(supplier_key: str) -> BasePriceLoader:
    """Возвращает готовый экземпляр загрузчика по ключу CLI."""
    key = (supplier_key or "").strip().lower()
    if key not in LOADERS:
        known = ", ".join(sorted(LOADERS.keys()))
        raise ValueError(
            f"Неизвестный поставщик «{supplier_key}». "
            f"Поддерживаются: {known}."
        )
    return LOADERS[key]()


def detect_loader(filepath: str) -> BasePriceLoader | None:
    """Пытается определить поставщика по имени файла.

    Используется в CLI, когда --supplier не указан. Возвращает None,
    если имя файла ни под один загрузчик не подошло — тогда CLI должен
    попросить явный --supplier.
    """
    name = Path(filepath).name
    for cls in LOADERS.values():
        if cls.detect(name):
            return cls()
    return None


__all__ = ["BasePriceLoader", "LOADERS", "get_loader", "detect_loader"]
