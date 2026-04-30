-- =============================================================
-- КВАДРО-ТЕХ: миграция 021 — детальный JSON-отчёт по загрузке
-- прайса (этап 11.2)
--
-- Что меняет:
--   Добавляет в price_uploads колонку report_json (JSONB) для
--   полного отчёта orchestrator'а (processed/added/updated/skipped/
--   errors/by_source/duration/error_message и т.п.).
--
-- Зачем:
--   В UI /admin/price-uploads нужна кнопка «Подробности», которая
--   показывает полный итог последней загрузки. До этого мы хранили
--   только короткое текстовое summary в notes — не структурировано
--   и неудобно для парсинга.
--
-- Идемпотентно: ADD COLUMN IF NOT EXISTS, чтобы повторный накат на
-- prod не падал.
-- =============================================================

ALTER TABLE price_uploads
    ADD COLUMN IF NOT EXISTS report_json JSONB;
