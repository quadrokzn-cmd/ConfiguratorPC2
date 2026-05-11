# Унифицированная модель строки прайса.
#
# PriceRow — промежуточное представление между адаптером конкретного
# поставщика (ocs.py, merlion.py, treolan.py, в будущем api-дистрибьютор)
# и общим orchestrator'ом. Адаптер читает свой Excel/API и отдаёт
# Iterator[PriceRow]; orchestrator делает сопоставление и запись в БД.
#
# Поля перечислены так, чтобы любой из трёх адаптеров мог их заполнить
# (и 4-й — API-дистрибьютор — тоже, через свой транспорт).

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class PriceRow:
    # Внутренний код товара у поставщика, по которому он принимает заказ.
    # У OCS это «1000xxx»; у Merlion — «Номер» из колонки E;
    # у Treolan собственного кода нет, поэтому supplier_sku = mpn.
    supplier_sku: str

    # MPN (Manufacturer Part Number) — каталожный номер производителя.
    # У Treolan — всегда равен supplier_sku (колонка A «Артикул»).
    # Может быть None только для позиций без партномера (редкий кейс).
    mpn: str | None

    # GTIN/EAN — глобальный штрихкод товара. Пригождается, когда MPN
    # не совпадает между поставщиками (классический пример — Intel CPU:
    # OCS хранит Order Code, Treolan — S-Spec, а GTIN одинаков везде).
    gtin: str | None

    # Бренд/производитель (для новых компонентов пишется в manufacturer).
    brand: str | None

    # Путь категории от поставщика как есть, без парсинга — для логов
    # и для таблицы unmapped_supplier_items.
    raw_category: str

    # Наша категория: cpu / motherboard / ram / gpu / storage / case /
    # psu / cooler. None означает «эта строка не относится к ПК и её
    # нужно пропустить» (периферия, софт, мебель и т. п.).
    our_category: str | None

    name: str

    price: Decimal
    # 'USD' | 'RUB' (строго 3 символа, чтобы влезть в supplier_prices.currency).
    currency: str

    stock: int
    transit: int

    # Номер строки в Excel — нужен для информативных сообщений об
    # ошибках в логе. Не хранится в БД.
    row_number: int | None = None
