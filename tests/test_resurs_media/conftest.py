# Локальный conftest для тестов модуля Resurs Media.
#
# Чистит таблицы resurs_media_notifications и resurs_media_catalog
# перед каждым тестом, чтобы тесты не зависели от порядка прогона.

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _truncate_resurs_media_tables(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE "
            "  resurs_media_notifications, "
            "  resurs_media_catalog "
            "RESTART IDENTITY CASCADE"
        ))
    yield
