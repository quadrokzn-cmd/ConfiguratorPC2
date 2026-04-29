# Фикстуры для тестов пакета export (этап 8).
#
# DB-инфраструктура (db_engine, db_session, миграции 001..018) живёт
# в корневом `tests/conftest.py` — этот файл оставляет только autouse-
# чистку таблиц, в которые тесты экспорта пишут.

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _clean_tables_export(db_engine):
    """Чистые таблицы перед каждым тестом пакета export.

    Компоненты, поставщики и цены тоже — потому что тесты email_composer
    создают своих поставщиков под именами "A-Sup"/"MPN-Sup" и т.п., и
    было бы неудобно ловить конфликты из предыдущих запусков.
    """
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE sent_emails, specification_items, queries, "
            "projects, daily_budget_log, users, api_usage_log, "
            "supplier_prices, unmapped_supplier_items, "
            "cpus, motherboards, rams, gpus, storages, cases, psus, coolers "
            "RESTART IDENTITY CASCADE"
        ))
        # suppliers чистим отдельно: миграция 009 заливает Merlion/Treolan,
        # нам в тестах это не мешает, но удобнее иметь пустую таблицу, чтобы
        # _insert_supplier(name='A-Sup') не натыкался на чужие строки.
        conn.execute(text(
            "TRUNCATE TABLE suppliers RESTART IDENTITY CASCADE"
        ))
    yield
