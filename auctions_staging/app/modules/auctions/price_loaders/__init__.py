from __future__ import annotations

from pathlib import Path

from app.modules.auctions.price_loaders.a1tis import A1TisPriceLoader
from app.modules.auctions.price_loaders.asbis import AsbisPriceLoader
from app.modules.auctions.price_loaders.base import BasePriceLoader
from app.modules.auctions.price_loaders.marvel import MarvelPriceLoader
from app.modules.auctions.price_loaders.merlion import MerlionPriceLoader
from app.modules.auctions.price_loaders.ocs import OcsPriceLoader
from app.modules.auctions.price_loaders.resursmedia import ResursMediaPriceLoader
from app.modules.auctions.price_loaders.sandisk import SanDiskPriceLoader
from app.modules.auctions.price_loaders.treolan import TreolanPriceLoader


LOADERS: dict[str, type[BasePriceLoader]] = {
    "merlion": MerlionPriceLoader,
    "ocs": OcsPriceLoader,
    "treolan": TreolanPriceLoader,
    "resursmedia": ResursMediaPriceLoader,
    "asbis": AsbisPriceLoader,
    "sandisk": SanDiskPriceLoader,
    "marvel": MarvelPriceLoader,
    "a1tis": A1TisPriceLoader,
}


def get_loader(supplier_code: str) -> BasePriceLoader:
    key = (supplier_code or "").strip().lower()
    if key not in LOADERS:
        known = ", ".join(sorted(LOADERS.keys()))
        raise ValueError(
            f"Неизвестный поставщик «{supplier_code}». Поддерживаются: {known}."
        )
    return LOADERS[key]()


def detect_loader(filepath: str) -> BasePriceLoader | None:
    name = Path(filepath).name
    for cls in LOADERS.values():
        if cls.detect(name):
            return cls()
    return None


__all__ = [
    "BasePriceLoader",
    "LOADERS",
    "get_loader",
    "detect_loader",
]
