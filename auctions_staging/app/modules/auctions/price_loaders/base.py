from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from app.modules.auctions.price_loaders.models import PriceRow


class BasePriceLoader(ABC):
    supplier_code: str = ""

    @classmethod
    @abstractmethod
    def detect(cls, filename: str) -> bool:
        ...

    @abstractmethod
    def iter_rows(self, filepath: str) -> Iterator[PriceRow]:
        ...
