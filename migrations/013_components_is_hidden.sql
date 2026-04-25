-- =============================================================
-- КВАДРО-ТЕХ: миграция 013 — флаг is_hidden у компонентов (этап 9А.2)
--
-- Что меняет:
--   Добавляет is_hidden BOOLEAN DEFAULT FALSE во все 8 таблиц компонентов.
--   Скрытый компонент не появляется в подборе (configurator) и в
--   fuzzy-поиске моделей по запросу менеджера (NLU). Используется для
--   ручного скрытия скелетов и неподходящих по схеме товаров (например,
--   4 Netac USB-C SSD, у которых из ассортимента нет реальных параметров).
--
-- Идемпотентно: ADD COLUMN IF NOT EXISTS. Существующие строки получат
-- DEFAULT FALSE автоматически (Postgres 11+ не делает rewrite таблицы
-- при добавлении NOT NULL DEFAULT для существующих строк).
-- =============================================================

ALTER TABLE cpus         ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE motherboards ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE rams         ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE gpus         ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE storages     ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE cases        ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE psus         ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE coolers      ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE;
