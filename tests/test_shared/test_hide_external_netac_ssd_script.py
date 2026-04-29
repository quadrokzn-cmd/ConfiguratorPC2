# Smoke-тесты scripts/hide_external_netac_ssd.py (этап 9Г.1).
#
# Проверяем, что:
#   - модуль импортируется без ошибок;
#   - функция find_candidates возвращает только Netac+USB и только видимые;
#   - apply_hide идемпотентна и возвращает правильный rowcount.
#
# DB-фикстура — db_engine из tests/test_shared/conftest.py (там уже
# применена миграция 013_components_is_hidden.sql).

from __future__ import annotations

import importlib

import pytest
from sqlalchemy import text


def test_script_imports():
    """Сам модуль импортируется и экспортирует ожидаемые функции."""
    mod = importlib.import_module("scripts.hide_external_netac_ssd")
    assert callable(mod.find_candidates)
    assert callable(mod.apply_hide)


@pytest.fixture()
def _seed_storages(db_engine):
    """Сид: 4 внешних Netac USB-C SSD + 1 внутренний Netac M.2 NVMe (контроль).

    Колонки storages — динамические, поэтому INSERT'ы пишем явно через
    минимально необходимые поля. is_hidden по умолчанию FALSE.
    """
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE storages RESTART IDENTITY CASCADE"))
        # Минимальный набор NOT NULL: model, manufacturer, storage_type,
        # form_factor, interface, capacity_gb. Внешние Netac технически не
        # подходят под enum form_factor — для smoke-теста сложим как 2.5".
        conn.execute(text(
            "INSERT INTO storages "
            "  (manufacturer, model, sku, storage_type, form_factor, "
            "   interface, capacity_gb) "
            "VALUES "
            "('Netac', 'Netac NT01Z9-001T-32BK Z9 1.8\" 1TB USB-C', 'NTC-Z9-1T', "
            "   'SSD', '2.5\"', 'SATA', 1000),"
            "('Netac', 'Netac NT01ZSLIM-001T-32BK Z Slim 1.8\" 1TB USB-C', 'NTC-ZSLIM-1T', "
            "   'SSD', '2.5\"', 'SATA', 1000),"
            "('Netac', 'Netac NT01ZSLIM-002T-32BK Z Slim 1.8\" 2TB USB-C', 'NTC-ZSLIM-2T', "
            "   'SSD', '2.5\"', 'SATA', 2000),"
            "('Netac', 'Netac NT01Z9-002T-32BK Z9 1.8\" 2TB USB-C', 'NTC-Z9-2T', "
            "   'SSD', '2.5\"', 'SATA', 2000),"
            "('Netac', 'Netac NV5000 1TB M.2 NVMe', 'NTC-NV5000', "
            "   'SSD', 'M.2', 'NVMe', 1000)"
        ))
    yield
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE storages RESTART IDENTITY CASCADE"))


def test_find_candidates_returns_only_external(db_engine, _seed_storages):
    mod = importlib.import_module("scripts.hide_external_netac_ssd")
    rows = mod.find_candidates(db_engine)
    models = {r.model for r in rows}
    assert len(rows) == 4
    assert all("USB" in m for m in models)
    # Внутренний M.2 NVMe не попал в кандидатов.
    assert not any("NV5000" in m for m in models)


def test_apply_hide_is_idempotent(db_engine, _seed_storages):
    mod = importlib.import_module("scripts.hide_external_netac_ssd")
    rows = mod.find_candidates(db_engine)
    ids = [int(r.id) for r in rows]

    # Первый вызов скрывает все 4.
    n1 = mod.apply_hide(db_engine, ids)
    assert n1 == 4

    # Повторный вызов уже ничего не меняет (is_hidden=TRUE отфильтрует их).
    n2 = mod.apply_hide(db_engine, ids)
    assert n2 == 0

    # Внутренний накопитель остался видимым.
    with db_engine.connect() as conn:
        visible = conn.execute(
            text("SELECT model FROM storages WHERE is_hidden = FALSE")
        ).scalars().all()
    assert any("NV5000" in m for m in visible)
    assert not any("USB" in m for m in visible)
