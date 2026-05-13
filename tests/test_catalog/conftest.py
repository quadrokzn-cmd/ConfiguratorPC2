# Фикстуры для тестов пакета catalog (мини-этап 2026-05-14, Фаза 2 Excel-export).
#
# DB-инфраструктура (db_engine, db_session, миграции) живёт в корневом
# tests/conftest.py — здесь только autouse-чистка таблиц, в которые
# тесты excel_export пишут (компоненты, supplier_prices, suppliers,
# exchange_rates, printers_mfu).

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _clean_tables_catalog(db_engine):
    """Чистые таблицы перед каждым тестом пакета catalog.

    suppliers — TRUNCATE отдельно (миграция 009 заливает Merlion/Treolan;
    тестам удобнее иметь известный набор без сюрпризов).
    """
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE supplier_prices, exchange_rates, "
            "cpus, motherboards, rams, gpus, storages, cases, psus, coolers, "
            "printers_mfu "
            "RESTART IDENTITY CASCADE"
        ))
        conn.execute(text(
            "TRUNCATE TABLE suppliers RESTART IDENTITY CASCADE"
        ))
    yield
