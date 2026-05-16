-- =============================================================
-- Migration 0040: расширение прав `ingest_writer` под smart-ingest
--   (мини-этап «2026-05-16 — deploy smart-ingest на офисный
--    worker», hotfix к миграции 0035).
--
-- Контекст:
--   Миграция 0035 (этап 9e.1) выдала ingest_writer'у права только
--   под старый ingest-цикл (tenders/tender_items/tender_status). С
--   момента появления smart-ingest (миграция 0039, commit 532238f)
--   orchestrator после каждого INSERT/UPDATE лота вызывает
--   `match_single_tender(engine, reg_number)`, который:
--     • SELECT'ит кандидатов из `printers_mfu`,
--     • DELETE + INSERT новые matches per item_id.
--   Без GRANT'ов офисный worker падает на первом же тике с
--   `permission denied for table matches` / `printers_mfu`.
--
-- Что делает миграция:
--   1. GRANT SELECT на `printers_mfu` (read-only — каталог SKU
--      управляется конфигуратором, ingest не должен его менять).
--   2. GRANT SELECT, INSERT, DELETE на `matches` (UPDATE не нужен —
--      save_matches() работает по идемпотентной схеме DELETE+INSERT
--      per tender_item_id).
--   3. GRANT USAGE, SELECT на `matches_id_seq` (для BIGSERIAL
--      nextval при INSERT в matches).
--
-- НЕ выдаём:
--   • UPDATE на matches — save_matches() этим не пользуется,
--     минимум прав строже.
--   • UPDATE на printers_mfu — derive_sku_ktru_codes() это
--     full-run preparation, smart-ingest её не вызывает; каталог
--     SKU — зона ответственности конфигуратора.
--   • Права на схему/таблицу `schema_migrations` — ingest_writer
--     их не читает.
--
-- Идемпотентно: GRANT-ы безопасно повторяются (PostgreSQL не
-- ошибается на дубликате).
-- =============================================================

GRANT SELECT ON TABLE printers_mfu TO ingest_writer;

GRANT SELECT, INSERT, DELETE ON TABLE matches TO ingest_writer;

GRANT USAGE, SELECT ON SEQUENCE matches_id_seq TO ingest_writer;
