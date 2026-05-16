from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy import Engine, text

from portal.services.auctions.ingest.card_parser import parse_card
from portal.services.auctions.ingest.filters import compute_flags
from portal.services.auctions.ingest.http_client import ZakupkiBlockedError, ZakupkiClient
from portal.services.auctions.ingest.repository import (
    PlatformSettings,
    load_settings,
    upsert_tender,
)
from portal.services.auctions.ingest.search import collect_hits
from portal.services.auctions.match.service import match_single_tender


# pg_advisory_lock id для auctions_ingest. Уникален в рамках проекта;
# защищает от concurrent ingest'ов между процессами:
#   - portal FastAPI cron + /admin/run-ingest{,-blocking};
#   - внешний офисный ingest-worker (scripts/run_auctions_ingest.py);
#   - локальные ad-hoc CLI-запуски на dev-машине.
# В одном процессе FastAPI cron и UI-эндпойнты уже синхронизированы
# через portal.services.auctions.ingest.single_flight.ingest_lock
# (threading.Lock) — advisory_lock покрывает межпроцессный case.
_AUCTIONS_INGEST_ADVISORY_LOCK_ID = 91234567


@dataclass
class IngestStats:
    cards_seen: int = 0
    cards_parsed: int = 0
    cards_failed: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    matches_inserted: int = 0
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
            "skipped": self.skipped,
            "matches_inserted": self.matches_inserted,
            "flagged_excluded_region": self.flagged_excluded_region,
            "flagged_below_nmck": self.flagged_below_nmck,
            "flagged_over_unit_price": self.flagged_over_unit_price,
            "flagged_no_watchlist_ktru": self.flagged_no_watchlist_ktru,
            "flagged_no_positions": self.flagged_no_positions,
            "ktru_codes_used": list(self.ktru_codes_used),
        }


def run_ingest_once(engine: Engine) -> IngestStats:
    """Smart-ingest аукционов с zakupki за один тик cron'а.

    Поведение по reg_number (мини-этап 2026-05-16):
      - reg_number нет в БД → INSERT + match_single_tender → matches.
      - reg_number есть, content_hash совпал → SKIP (tender_items не
        трогаем, matches живы — не убиваются каскадно как раньше).
      - reg_number есть, content_hash отличается → UPDATE + match_single_tender
        ТОЛЬКО для этого reg_number (matches остальных лотов нетронуты).

    DELETE из таблицы tenders не выполняется ни в одной ветке. Удаление
    аукциона остаётся ручной операцией (UI / DBA), и FK NO ACTION
    (миграция 0039) ловит попытки нарушить целостность.

    Защита от concurrent запусков:
      - threading.Lock (single_flight.ingest_lock) — в одном процессе
        между cron и /admin/run-ingest{,-blocking}.
      - pg_advisory_lock внутри этой функции — между разными процессами
        (portal FastAPI / офисный ingest-worker / CLI). Если lock занят
        другим процессом — возвращаем пустые IngestStats() с WARN-логом
        и не запускаем тик.
    """
    with engine.connect() as lock_conn:
        got_lock = lock_conn.execute(
            text("SELECT pg_try_advisory_lock(:lock_id) AS got"),
            {"lock_id": _AUCTIONS_INGEST_ADVISORY_LOCK_ID},
        ).scalar()
        if not got_lock:
            logger.warning(
                "ingest: pg_advisory_lock {} занят другим процессом — пропуск тика",
                _AUCTIONS_INGEST_ADVISORY_LOCK_ID,
            )
            return IngestStats()
        try:
            return _run_ingest_locked(engine)
        finally:
            lock_conn.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": _AUCTIONS_INGEST_ADVISORY_LOCK_ID},
            )


def _run_ingest_locked(engine: Engine) -> IngestStats:
    """Тело ingest-тика. Вызывается из run_ingest_once под pg_advisory_lock."""
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
            elif result.updated:
                stats.updated += 1
            else:
                stats.skipped += 1

            if result.inserted or result.updated:
                try:
                    stats.matches_inserted += match_single_tender(engine, card.reg_number)
                except Exception as exc:
                    # Матчинг конкретного лота упал — лот в БД, но без matches.
                    # Не валим весь ingest, в следующий тик (или ручной run_matching)
                    # лот будет ре-матчен. Логируем как warning.
                    logger.warning(
                        "match_single_tender for {} failed: {}",
                        hit.reg_number,
                        exc,
                    )

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
