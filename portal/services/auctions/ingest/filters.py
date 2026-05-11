from __future__ import annotations

from typing import Any

from portal.services.auctions.ingest.card_parser import TenderCard
from portal.services.auctions.ingest.repository import PlatformSettings

# После перехода на 2 KTRU-зонтика (миграция 0009) watchlist почти никогда не
# пересекается с конкретными кодами карточки (карточки содержат точные позиции
# вида `26.20.18.000-00000069`). Поэтому проверяем по префиксу: лот относится
# к нашей категории, если хотя бы один код карточки начинается с одного из
# префиксов МФУ или Принтер.
RELEVANT_PREFIXES: tuple[str, ...] = (
    "26.20.18.000-",
    "26.20.16.120-",
)


def compute_flags(card: TenderCard, settings: PlatformSettings) -> dict[str, Any]:
    """Mark a lot with soft flags so dashboard can hide/show by toggling thresholds.
    No record is dropped — every parsed lot lands in tenders. Per principle 9 + п.7
    of the plan: filtering is a UI concern, not a record-existence concern.
    """
    flags: dict[str, Any] = {}

    if card.customer_region and card.customer_region in settings.excluded_region_names:
        flags["excluded_by_region"] = True
        flags["excluded_region_name"] = card.customer_region

    if card.nmck_total is not None and float(card.nmck_total) < settings.nmck_min_rub:
        flags["below_nmck_min"] = True
        flags["nmck_total_rub"] = float(card.nmck_total)

    over_limit = [
        i for i in card.items
        if i.nmck_per_unit is not None and float(i.nmck_per_unit) > settings.max_price_per_unit_rub
    ]
    if over_limit:
        flags["rejected_by_price_per_unit"] = True
        flags["max_position_price_rub"] = max(float(i.nmck_per_unit) for i in over_limit)

    has_relevant_ktru = any(
        code.startswith(prefix)
        for code in card.ktru_codes
        for prefix in RELEVANT_PREFIXES
    )
    if not has_relevant_ktru:
        flags["no_watchlist_ktru_in_card"] = True

    if not card.items:
        flags["no_positions_parsed"] = True

    return flags
