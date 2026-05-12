# Локальный conftest для тестов модуля Resurs Media Notification.
#
# Чистит таблицу resurs_media_notifications перед каждым тестом, чтобы
# тесты не зависели от порядка прогона.

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _truncate_notifications(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE resurs_media_notifications "
            "RESTART IDENTITY CASCADE"
        ))
    yield
