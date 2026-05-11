# Авто-хук для запуска обогащения после price_loader.
#
# Схема:
#   - price_loader завершает загрузку и коммитит записи;
#   - если в .env OPENAI_ENRICH_AUTO_HOOK=true и был добавлен хотя бы один
#     новый SKU — вызывается auto_enrich_new_skus;
#   - она считает число кандидатов через _list_new_sku_candidates (те же SKU,
#     что увидит --new-only);
#   - если candidates <= AUTO_LIMIT — запускает runner в non-interactive
#     режиме; иначе просто пишет в лог «требуется ручной запуск».
#
# Тихий отказ: любые исключения внутри хука логируются, но НЕ пробрасываются
# наружу — price_loader уже закоммитил, и хук не должен падать из-за
# сетевых проблем или отсутствия ключа.

from __future__ import annotations

import logging
import os

from shared.db import SessionLocal

logger = logging.getLogger(__name__)


def _auto_hook_enabled() -> bool:
    return os.getenv("OPENAI_ENRICH_AUTO_HOOK", "false").strip().lower() in {
        "true", "1", "yes", "y", "да",
    }


def _auto_limit() -> int:
    raw = os.getenv("OPENAI_ENRICH_AUTO_LIMIT", "20")
    try:
        v = int(raw)
        return max(0, v)
    except ValueError:
        return 20


def _count_new_sku_candidates() -> int:
    # Локальный импорт, чтобы не тащить runner и openai client при старте.
    from portal.services.configurator.enrichment.openai_search.runner import (
        _list_new_sku_candidates,
    )
    session = SessionLocal()
    try:
        return len(_list_new_sku_candidates(session))
    finally:
        session.close()


def auto_enrich_new_skus(*, added_new: int) -> None:
    """Запускается после завершения price_loader.

    added_new — сколько новых компонентов создала эта загрузка
    (значение counters['added'] из price_loader).
    """
    if not _auto_hook_enabled():
        logger.info("auto-hook OpenAI выключен (OPENAI_ENRICH_AUTO_HOOK=false)")
        return
    if added_new <= 0:
        logger.info("auto-hook: новых SKU нет, ничего не делаем")
        return

    try:
        candidates = _count_new_sku_candidates()
    except Exception as exc:
        logger.warning("auto-hook: не удалось посчитать кандидатов: %s", exc)
        return

    if candidates == 0:
        logger.info("auto-hook: среди новых SKU нет полей для обогащения")
        return

    limit = _auto_limit()
    if candidates > limit:
        logger.warning(
            "auto-hook: добавлено новых SKU (кандидатов на обогащение %d) > "
            "AUTO_LIMIT %d. Требуется ручной запуск: "
            "python scripts/enrich_openai.py --new-only",
            candidates, limit,
        )
        return

    logger.info(
        "auto-hook: запускаем обогащение %d новых SKU в non-interactive режиме",
        candidates,
    )
    try:
        from portal.services.configurator.enrichment.openai_search.runner import format_report, run
        stats = run(mode="new_only", non_interactive=True, dry_run=False)
        logger.info("auto-hook завершён:\n%s", format_report(stats))
    except Exception as exc:
        logger.warning("auto-hook: ошибка при обогащении, продолжаем без него: %s", exc)
