# Локальный conftest для tests/test_shared.
#
# DB-инфраструктура (db_engine, db_session, миграции 001..018) — в
# корневом `tests/conftest.py`. Здесь только чистка audit_log перед
# каждым тестом — этот conftest изолирован от test_portal/test_web,
# но трогает ту же БД.

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _clean_audit_log(db_engine):
    """Чистим audit_log перед каждым тестом, остальные таблицы трогать
    не нужно — этот conftest ориентирован на shared/audit и подобные
    низкоуровневые тесты."""
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE audit_log RESTART IDENTITY"))
    yield
