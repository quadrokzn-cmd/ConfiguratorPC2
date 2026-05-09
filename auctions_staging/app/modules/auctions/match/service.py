"""Оркестрация матчинга: подготовка БД → проход по всем позициям → агрегация."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable

from loguru import logger
from sqlalchemy import Engine

from app.modules.auctions.match.aggregator import (
    TenderSummary,
    aggregate_tender,
    margin_threshold_pct,
)
from app.modules.auctions.match.matcher import match_tender_item
from app.modules.auctions.match.repository import (
    clear_all_matches,
    derive_single_position_nmck,
    derive_sku_ktru_codes,
    load_candidates_for_ktru,
    load_tender_items,
    save_matches,
)


@dataclass
class MatchingStats:
    items_total: int = 0
    items_with_ktru: int = 0
    items_with_primary: int = 0
    items_no_candidates: int = 0
    items_no_nmck_per_unit: int = 0
    items_no_cost_or_dropped: int = 0
    matches_inserted: int = 0
    sku_ktru_filled: int = 0
    nmck_per_unit_derived: int = 0
    matched_tenders: int = 0
    tenders_passing_margin_threshold: int = 0
    margin_threshold_pct: Decimal = Decimal("15")
    margin_pct_distribution: dict[str, Decimal] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "items_total": self.items_total,
            "items_with_ktru": self.items_with_ktru,
            "items_with_primary": self.items_with_primary,
            "items_no_candidates": self.items_no_candidates,
            "items_no_nmck_per_unit": self.items_no_nmck_per_unit,
            "items_no_cost_or_dropped": self.items_no_cost_or_dropped,
            "matches_inserted": self.matches_inserted,
            "sku_ktru_filled": self.sku_ktru_filled,
            "nmck_per_unit_derived": self.nmck_per_unit_derived,
            "matched_tenders": self.matched_tenders,
            "tenders_passing_margin_threshold": self.tenders_passing_margin_threshold,
            "margin_threshold_pct": float(self.margin_threshold_pct),
            "margin_pct_distribution": {k: float(v) for k, v in self.margin_pct_distribution.items()},
        }


def run_matching(engine: Engine, full_recompute: bool = True) -> MatchingStats:
    """Полный прогон матчинга по всем релевантным позициям в БД.

    Идемпотентен: каждая позиция получает свежий набор matches (старые удаляются
    в `save_matches`). При `full_recompute=True` выполняется единичная очистка
    `matches` целиком, чтобы убрать осиротевшие записи.
    """
    stats = MatchingStats()

    # Подготовка: deriving ktru_codes_array у SKU и nmck_per_unit для одно-позиционных лотов.
    stats.sku_ktru_filled = derive_sku_ktru_codes(engine)
    stats.nmck_per_unit_derived = derive_single_position_nmck(engine)
    if stats.sku_ktru_filled or stats.nmck_per_unit_derived:
        logger.info(
            "match.derive: ktru_codes_array filled for {} SKU; nmck_per_unit derived for {} items",
            stats.sku_ktru_filled,
            stats.nmck_per_unit_derived,
        )

    if full_recompute:
        cleared = clear_all_matches(engine)
        if cleared:
            logger.info("match.clear: deleted {} existing match rows", cleared)

    items = load_tender_items(engine)
    stats.items_total = len(items)

    # Кеш кандидатов на KTRU-код (на запуск)
    candidate_cache: dict[str, list] = {}

    matched_tender_ids: set[str] = set()

    for item in items:
        if not item.ktru_code:
            continue
        stats.items_with_ktru += 1

        if item.nmck_per_unit is None:
            stats.items_no_nmck_per_unit += 1
            continue

        candidates = candidate_cache.get(item.ktru_code)
        if candidates is None:
            candidates = load_candidates_for_ktru(engine, item.ktru_code)
            candidate_cache[item.ktru_code] = candidates

        if not candidates:
            stats.items_no_candidates += 1
            continue

        matches = match_tender_item(item, candidates)
        if not matches:
            stats.items_no_cost_or_dropped += 1
            continue

        stats.items_with_primary += 1
        stats.matches_inserted += save_matches(engine, item.id, matches)
        matched_tender_ids.add(item.tender_id)

    # Агрегация по тендерам
    threshold = margin_threshold_pct(engine)
    stats.margin_threshold_pct = threshold
    stats.matched_tenders = len(matched_tender_ids)

    primary_pcts: list[Decimal] = []
    for tid in matched_tender_ids:
        summary = aggregate_tender(engine, tid)
        if summary.primary_margin_pct_avg is not None:
            primary_pcts.append(summary.primary_margin_pct_avg)
            if summary.primary_margin_pct_avg >= threshold:
                stats.tenders_passing_margin_threshold += 1

    stats.margin_pct_distribution = _distribution(primary_pcts)
    return stats


def aggregate_all(engine: Engine, tender_ids: Iterable[str]) -> list[TenderSummary]:
    return [aggregate_tender(engine, tid) for tid in tender_ids]


def _distribution(values: list[Decimal]) -> dict[str, Decimal]:
    """Минимум, p25, медиана, p75, максимум по списку Decimal."""
    if not values:
        return {}
    values_sorted = sorted(values)
    n = len(values_sorted)

    def q(p: float) -> Decimal:
        idx = max(0, min(n - 1, int(round(p * (n - 1)))))
        return values_sorted[idx]

    return {
        "count": Decimal(n),
        "min": values_sorted[0],
        "p25": q(0.25),
        "median": q(0.5),
        "p75": q(0.75),
        "max": values_sorted[-1],
    }
