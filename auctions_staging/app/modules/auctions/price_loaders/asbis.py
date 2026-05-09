from __future__ import annotations

from typing import Iterator

from app.modules.auctions.catalog.brand_normalizer import canonical_brand  # noqa: F401
from app.modules.auctions.price_loaders.base import BasePriceLoader
from app.modules.auctions.price_loaders.models import PriceRow


class AsbisPriceLoader(BasePriceLoader):
    supplier_code = "asbis"

    @classmethod
    def detect(cls, filename: str) -> bool:
        return False

    def iter_rows(self, filepath: str) -> Iterator[PriceRow]:
        # При реализации: PriceRow.brand формировать через canonical_brand(raw),
        # иначе ловим дубли по капитализации (HP/HP Inc., Pantum/PANTUM и т.д.).
        raise NotImplementedError("прайс ещё не получен")
