from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy import Engine

from app.services.auctions.ingest.card_parser import parse_card
from app.services.auctions.ingest.filters import compute_flags
from app.services.auctions.ingest.http_client import ZakupkiBlockedError, ZakupkiClient
from app.services.auctions.ingest.repository import (
    PlatformSettings,
    load_settings,
    upsert_tender,
)
from app.services.auctions.ingest.search import collect_hits


@dataclass
class IngestStats:
    cards_seen: int = 0
    cards_parsed: int = 0
    cards_failed: int = 0
    inserted: int = 0
    updated: int = 0
    flagged_excluded_region: int = 0
    flagged_below_nmck: int = 0
    flagged_over_unit_price: int = 0
    flagged_no_watchlist_ktru: int = 0
    flagged_no_positions: int = 0
    ktru_codes_used: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "cards_seen": self.cards_seen,
            "cards_parsed": self.cards_parsed,
            "cards_failed": self.cards_failed,
            "inserted": self.inserted,
            "updated": self.updated,
            "flagged_excluded_region": self.flagged_excluded_region,
            "flagged_below_nmck": self.flagged_below_nmck,
            "flagged_over_unit_price": self.flagged_over_unit_price,
            "flagged_no_watchlist_ktru": self.flagged_no_watchlist_ktru,
            "flagged_no_positions": self.flagged_no_positions,
            "ktru_codes_used": list(self.ktru_codes_used),
        }


def run_ingest_once(engine: Engine) -> IngestStats:
    settings = load_settings(engine)
    if not settings.ktru_watchlist:
        logger.warning("ingest skipped: ktru_watchlist is empty (no active codes)")
        return IngestStats()

    codes_used = tuple(code for code, _ in settings.ktru_watchlist)
    stats = IngestStats(ktru_codes_used=codes_used)

    with ZakupkiClient() as client:
        try:
            hits = collect_hits(client, watchlist=settings.ktru_watchlist)
        except ZakupkiBlockedError as exc:
            logger.error("ingest aborted: search blocked by zakupki — {}", exc)
            return stats

        stats.cards_seen = len(hits)
        logger.info("ingest plan: {} unique reg_numbers to fetch", stats.cards_seen)

        for hit in hits.values():
            try:
                html = client.get_html(hit.url)
                card = parse_card(hit.reg_number, hit.url, html)
            except ZakupkiBlockedError as exc:
                stats.cards_failed += 1
                logger.warning("card {} fetch blocked: {}", hit.reg_number, exc)
                continue
            except Exception as exc:
                stats.cards_failed += 1
                logger.exception("card {} parse failed: {}", hit.reg_number, exc)
                continue

            flags = compute_flags(card, settings)
            _bump_flag_counters(flags, stats)

            try:
                result = upsert_tender(engine, card, flags)
            except Exception as exc:
                stats.cards_failed += 1
                logger.exception("card {} upsert failed: {}", hit.reg_number, exc)
                continue

            stats.cards_parsed += 1
            if result.inserted:
                stats.inserted += 1
            else:
                stats.updated += 1

    logger.info("ingest done: {}", stats.as_dict())
    return stats


def _bump_flag_counters(flags: dict[str, Any], stats: IngestStats) -> None:
    if flags.get("excluded_by_region"):
        stats.flagged_excluded_region += 1
    if flags.get("below_nmck_min"):
        stats.flagged_below_nmck += 1
    if flags.get("rejected_by_price_per_unit"):
        stats.flagged_over_unit_price += 1
    if flags.get("no_watchlist_ktru_in_card"):
        stats.flagged_no_watchlist_ktru += 1
    if flags.get("no_positions_parsed"):
        stats.flagged_no_positions += 1


__all__ = ["IngestStats", "PlatformSettings", "run_ingest_once"]
