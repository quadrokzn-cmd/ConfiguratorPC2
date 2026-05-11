# Тонкая обёртка для обратной совместимости CLI и существующих импортов.
#
# До этапа 7 здесь был монолит, работающий только с OCS. После этапа 7
# вся логика перенесена в price_loaders/ (пакет с отдельным адаптером на
# каждого поставщика + общий orchestrator). UI-4 (Путь B, 2026-05-11):
# пакет переехал в portal/services/configurator/price_loaders/. Этот файл
# оставлен ТОЛЬКО чтобы не ломать:
#   - старые импорты load_ocs_price из сторонних скриптов/тестов;
#   - ранний CLI-контракт scripts/load_price.py --supplier ocs.
#
# Новый код должен импортировать load_price из
# portal.services.configurator.price_loaders или использовать orchestrator
# напрямую.

from __future__ import annotations

from portal.services.configurator.price_loaders.orchestrator import load_price


def load_ocs_price(filepath: str) -> dict:
    """Загружает прайс OCS через общий orchestrator.

    Контракт результата совместим со старым (total_rows, processed,
    updated, added, skipped, errors, status, upload_id); дополнительно
    orchestrator кладёт туда поля supplier, unmapped_*, by_source —
    старый CLI их просто не читает.
    """
    return load_price(filepath, supplier_key="ocs")


__all__ = ["load_ocs_price"]
