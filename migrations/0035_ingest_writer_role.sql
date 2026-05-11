-- =============================================================
-- Migration 0035: ограниченная PG-роль `ingest_writer`
--                 для офисного ingest-worker'а (этап 9e.1).
--
-- Контекст: путь A архитектуры production-ingest (этап 9e).
-- Офисный сервер в РФ со статическим IP запускает ingest и пишет
-- напрямую в Railway-PG через DATABASE_PUBLIC_URL. zakupki.gov.ru
-- с офисного IP не блокируется (smoke 2026-05-11: HTTP 200 на
-- главной/поиске/карточке).
--
-- Что делает миграция:
--   1. Создаёт роль `ingest_writer` с `NOLOGIN` (пароль выставляется
--      отдельно через `ALTER ROLE ... WITH LOGIN PASSWORD '...'`
--      ВНЕ git — см. docs/preprod-deploy.md, шаг Л).
--   2. Выдаёт минимально необходимые права для ingest-цикла
--      (см. app/services/auctions/ingest/repository.py):
--        - SELECT на 3 таблицы-источника фильтров (settings,
--          excluded_regions, ktru_watchlist);
--        - SELECT/INSERT/UPDATE/DELETE на 3 таблицы-цели
--          (tenders, tender_items, tender_status — full upsert
--          + status init);
--        - USAGE на schema public (без него SELECT не сработает);
--        - USAGE+SELECT на sequence tender_items_id_seq
--          (для BIGSERIAL nextval при INSERT в tender_items).
--   3. НЕ выдаёт права на: users, audit_log, printers_mfu, ПК-таблицы
--      (cpus/gpus/motherboards/rams/storages/cases/psus/coolers),
--      supplier_prices, suppliers, unmapped_supplier_items,
--      auto_price_loads, auto_price_load_runs, exchange_rates,
--      matches, ktru_catalog, schema_migrations и прочие.
--      Smoke-проверки (docs/preprod-deploy.md, шаг Л) подтверждают
--      permission denied на этих таблицах.
--
-- Идемпотентно: DO-блок с проверкой pg_roles, GRANT-ы безопасно
-- повторяются.
--
-- Применяется отдельно на pre-prod (9e.1) и prod (9e.4).
-- =============================================================

-- 1. Создать роль NOLOGIN (без пароля), идемпотентно.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'ingest_writer') THEN
        CREATE ROLE ingest_writer NOLOGIN;
    END IF;
END$$;

-- 2. Базовый доступ: подключение к БД + использование схемы public.
GRANT CONNECT ON DATABASE railway TO ingest_writer;
GRANT USAGE ON SCHEMA public TO ingest_writer;

-- 3. Чтение фильтров платформы (load_settings()).
GRANT SELECT ON TABLE settings          TO ingest_writer;
GRANT SELECT ON TABLE excluded_regions  TO ingest_writer;
GRANT SELECT ON TABLE ktru_watchlist    TO ingest_writer;

-- 4. Запись лотов (upsert_tender()).
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE tenders         TO ingest_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE tender_items    TO ingest_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE tender_status   TO ingest_writer;

-- 5. Sequence для tender_items.id (BIGSERIAL).
GRANT USAGE, SELECT ON SEQUENCE tender_items_id_seq TO ingest_writer;
