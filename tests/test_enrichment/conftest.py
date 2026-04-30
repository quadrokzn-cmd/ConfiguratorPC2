# Чистка таблиц для тестов test_enrichment/.
# DB-инфраструктура (db_engine/db_session) приходит из tests/conftest.py.

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _clean_tables(db_engine):
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE TABLE component_field_sources, supplier_prices, suppliers, "
                "cpus, motherboards, rams, gpus, storages, cases, psus, coolers "
                "RESTART IDENTITY CASCADE"
            )
        )
    yield
