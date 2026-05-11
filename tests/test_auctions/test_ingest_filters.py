from __future__ import annotations

from decimal import Decimal

from portal.services.auctions.ingest.card_parser import TenderCard, TenderItem
from portal.services.auctions.ingest.filters import compute_flags
from portal.services.auctions.ingest.repository import PlatformSettings


def _settings(**overrides) -> PlatformSettings:
    base = dict(
        nmck_min_rub=30000.0,
        max_price_per_unit_rub=300000.0,
        excluded_region_names=frozenset({"Приморский край"}),
        # watchlist теперь — пары (код, display_name); фильтр работает по
        # префиксу, поэтому конкретный код тут второстепенен.
        ktru_watchlist=(
            ("26.20.18.000-00000001", "Многофункциональное устройство (МФУ)"),
            ("26.20.16.120-00000001", "Принтер"),
        ),
    )
    base.update(overrides)
    return PlatformSettings(**base)


def _card(**overrides) -> TenderCard:
    base = TenderCard(reg_number="r", url="u")
    base.customer_region = overrides.get("region", "Республика Татарстан")
    base.nmck_total = overrides.get("nmck", Decimal("100000"))
    base.ktru_codes = overrides.get("ktru", ["26.20.18.000-00000069"])
    base.items = overrides.get("items", [
        TenderItem(1, "26.20.18.000-00000069", "МФУ", Decimal("1"), "шт", Decimal("50000"), {}),
    ])
    return base


def test_flags_clean_lot():
    flags = compute_flags(_card(), _settings())
    assert flags == {}


def test_flags_excluded_region():
    flags = compute_flags(_card(region="Приморский край"), _settings())
    assert flags["excluded_by_region"] is True
    assert flags["excluded_region_name"] == "Приморский край"


def test_flags_below_nmck():
    flags = compute_flags(_card(nmck=Decimal("10000")), _settings())
    assert flags["below_nmck_min"] is True
    assert flags["nmck_total_rub"] == 10000.0


def test_flags_over_unit_price():
    item = TenderItem(1, "26.20.18.000-00000069", "МФУ", Decimal("1"), "шт", Decimal("400000"), {})
    flags = compute_flags(_card(items=[item]), _settings())
    assert flags["rejected_by_price_per_unit"] is True
    assert flags["max_position_price_rub"] == 400000.0


def test_flags_no_watchlist_ktru_when_outside_prefix():
    """Карточка не относится к нашим категориям — никаких префиксов МФУ/Принтера."""
    flags = compute_flags(_card(ktru=["99.99.99.999-00000099"]), _settings())
    assert flags["no_watchlist_ktru_in_card"] is True


def test_flags_no_watchlist_ktru_absent_for_printer_prefix():
    """Конкретный код принтера (отличный от зонтика) не должен ронять флаг — он ловится по префиксу."""
    flags = compute_flags(_card(ktru=["26.20.16.120-00000013"]), _settings())
    assert "no_watchlist_ktru_in_card" not in flags


def test_flags_no_watchlist_ktru_absent_for_mfu_prefix():
    """Конкретный код МФУ (отличный от зонтика) тоже принимается префиксной проверкой."""
    flags = compute_flags(_card(ktru=["26.20.18.000-00000069"]), _settings())
    assert "no_watchlist_ktru_in_card" not in flags


def test_flags_no_positions():
    flags = compute_flags(_card(items=[]), _settings())
    assert flags["no_positions_parsed"] is True
