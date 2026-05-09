from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

OurCategory = Literal["printer", "mfu", "ignore"]


@dataclass
class PriceRow:
    supplier_sku: str
    mpn: str | None
    gtin: str | None
    brand: str | None
    raw_category: str
    name: str
    price: Decimal
    currency: str
    stock: int
    transit: int
    our_category: OurCategory = "ignore"
    row_number: int | None = None
