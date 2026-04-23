# Общий интерфейс загрузчика прайс-листа поставщика.
#
# Каждый наследник отвечает только за превращение «сырого» источника
# (Excel-файл, JSON-ответ API, CSV-выгрузка) в поток унифицированных
# PriceRow. Запись в БД, сопоставление и все побочные эффекты делает
# orchestrator — так его поведение одинаково для всех поставщиков.

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator

from app.services.price_loaders.models import PriceRow


class BasePriceLoader(ABC):
    # Имя поставщика в точности как в таблице suppliers (регистр важен).
    supplier_name: str = ""

    @classmethod
    @abstractmethod
    def detect(cls, filename: str) -> bool:
        """Возвращает True, если по имени файла видно, что прайс от этого
        поставщика. Используется CLI, когда --supplier не указан."""

    @abstractmethod
    def iter_rows(self, filepath: str) -> Iterator[PriceRow]:
        """Читает источник и отдаёт PriceRow один за другим.
        Строки, которые не относятся к ПК-компонентам (our_category=None),
        можно либо не возвращать, либо возвращать с None — orchestrator
        сам корректно обработает оба случая.
        """
