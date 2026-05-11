# Выбор поставщика и формирование предложений по компоненту.
#
# После того как candidates.py определил минимальную цену среди поставщиков,
# нам нужно выбрать конкретного поставщика и собрать список «также доступно у …»
# для вывода пользователю.
#
# Приводим все цены к USD: если поставщик продаёт в RUB — делим на курс.
# Курс передаётся один раз на запрос и фиксируется в верхнеуровневой функции.

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from portal.services.configurator.engine.schema import SupplierOffer


def _to_usd(price, currency: str, usd_rub: float) -> float:
    """Конвертирует цену в USD. RUB → делим на курс."""
    p = float(price)
    if (currency or "").upper() == "USD":
        return p
    # По умолчанию в supplier_prices.currency может быть RUB или что-то ещё;
    # для неизвестной валюты трактуем как RUB (безопасный fallback).
    return p / usd_rub


def fetch_offers(
    session,
    *,
    category: str,
    component_id: int,
    usd_rub: float,
    allow_transit: bool,
) -> list[SupplierOffer]:
    """Возвращает все предложения поставщиков по компоненту.

    Включает позиции с stock > 0 и, если allow_transit=True, позиции с
    transit > 0 (флаг in_transit=True). Список отсортирован по цене в USD
    по возрастанию.
    """
    # 9А.2: s.is_active = TRUE — деактивированный поставщик не участвует
    # в подборе (закрытие техдолга 8.3, теперь с UI-фильтром).
    query = text(
        """
        SELECT s.name           AS supplier,
               sp.supplier_sku  AS supplier_sku,
               sp.price         AS price,
               sp.currency      AS currency,
               sp.stock_qty     AS stock,
               sp.transit_qty   AS transit
        FROM supplier_prices sp
        JOIN suppliers s ON s.id = sp.supplier_id
        WHERE sp.category     = :cat
          AND sp.component_id = :cid
          AND s.is_active     = TRUE
          AND (sp.stock_qty > 0 OR (:allow_tr AND sp.transit_qty > 0))
        """
    )
    rows = session.execute(
        query,
        {"cat": category, "cid": component_id, "allow_tr": allow_transit},
    ).mappings().all()

    offers: list[SupplierOffer] = []
    for r in rows:
        in_stock = int(r["stock"]) > 0
        price_usd = _to_usd(r["price"], r["currency"], usd_rub)
        price_rub = price_usd * usd_rub
        offers.append(SupplierOffer(
            supplier=r["supplier"],
            supplier_sku=r["supplier_sku"],
            price_usd=round(price_usd, 2),
            price_rub=round(price_rub, 2),
            stock=int(r["stock"]) if in_stock else int(r["transit"]),
            in_transit=not in_stock,
        ))
    offers.sort(key=lambda o: o.price_usd)
    return offers


def choose_supplier(
    offers: list[SupplierOffer],
) -> tuple[SupplierOffer, list[SupplierOffer]]:
    """Из списка предложений выбирает самое дешёвое (chosen) и возвращает
    остальные как also_available_at. Среди chosen приоритет у stock перед transit.

    Порядок выбора:
      1) Самое дешёвое из тех, что в наличии (in_transit=False).
      2) Если таких нет — самое дешёвое из транзита.
    Остальные предложения возвращаются в отдельном списке в том же порядке цен.
    """
    if not offers:
        raise ValueError("Список предложений пуст")

    # 1) in-stock
    in_stock = [o for o in offers if not o.in_transit]
    if in_stock:
        chosen = in_stock[0]
    else:
        chosen = offers[0]

    others = [o for o in offers if o is not chosen]
    return chosen, others
