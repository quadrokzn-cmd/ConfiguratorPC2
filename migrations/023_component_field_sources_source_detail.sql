-- =============================================================
-- КВАДРО-ТЕХ: миграция 023 — подметка источника в
-- component_field_sources (этап 11.6.1)
--
-- Что меняет:
--   Добавляет колонку source_detail (TEXT, NULL).
--   Это «уточнение» к колонке source. Нужна, чтобы отличить
--   regex-обогащение по полю model таблицы компонентов
--   (старый scripts/enrich_regex.py) от regex-обогащения по
--   supplier_prices.raw_name (новый scripts/enrich_regex_from_raw_names.py
--   из этапа 11.6.1). Допустимые значения для source='regex':
--     - NULL — старый прогон, source_detail неизвестен;
--     - 'from_model' — обогащение по полю name/model таблицы компонентов;
--     - 'from_raw_name' — обогащение по supplier_prices.raw_name (11.6.1).
--
--   Для других source ('claude_code', 'derived', 'derived_from_name',
--   'manual', 'openai') колонка тоже может использоваться, если
--   появится несколько подвидов источника в одном source.
--
-- Зачем:
--   После 11.6.1 у нас два regex-источника, которые нужно различать
--   в аналитике покрытия и при будущей очистке/перепрогоне.
--
-- Идемпотентно: ADD COLUMN IF NOT EXISTS — повторный накат на
-- prod не падает.
-- =============================================================

ALTER TABLE component_field_sources
    ADD COLUMN IF NOT EXISTS source_detail TEXT;
