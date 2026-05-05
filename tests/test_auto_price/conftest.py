# Фикстуры для тестов пакета auto_price (этап 12.3).
#
# Корневой tests/conftest.py поднимает БД и накатывает миграции
# (включая 028_auto_price_loads.sql). Здесь:
#   - чистим auto_price_loads / auto_price_load_runs / price_uploads и
#     связанные таблицы перед каждым тестом;
#   - monkeypatch'им TREOLAN_API_* env-переменные, чтобы fetcher запустился;
#   - сбрасываем кеш JWT-токена в TreolanFetcher между тестами.

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _clean_auto_price_tables(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE "
            "  auto_price_load_runs, auto_price_loads, "
            "  unmapped_supplier_items, supplier_prices, price_uploads, "
            "  suppliers, "
            "  cpus, motherboards, rams, gpus, storages, cases, psus, coolers, "
            "  exchange_rates "
            "RESTART IDENTITY CASCADE"
        ))
    yield


@pytest.fixture()
def treolan_env(monkeypatch):
    """Гарантирует, что TreolanFetcher.__init__ не упадёт на проверке
    обязательных env'ов. Конкретные тесты могут перезаписать."""
    monkeypatch.setenv("TREOLAN_API_BASE_URL", "https://api.treolan.test/api")
    monkeypatch.setenv("TREOLAN_API_LOGIN", "test_login")
    monkeypatch.setenv("TREOLAN_API_PASSWORD", "test_password")


@pytest.fixture(autouse=True)
def _reset_treolan_token_cache():
    """Сбрасываем process-level кеш токена между тестами, иначе
    test_get_token_caches_within_ttl сломает следующие тесты."""
    from app.services.auto_price.fetchers.treolan import _reset_token_cache_for_tests
    _reset_token_cache_for_tests()
    yield
    _reset_token_cache_for_tests()
