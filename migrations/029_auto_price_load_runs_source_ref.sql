-- =============================================================
-- КВАДРО-ТЕХ: миграция 029 — source_ref для auto_price_load_runs
-- (этап 12.1, IMAP-канал автозагрузки прайсов: OCS и Merlion)
--
-- Что меняет:
--   1. Добавляет колонку source_ref TEXT в auto_price_load_runs.
--      Хранит идентификатор источника, по которому fetcher идемпотентен:
--        - для IMAP-канала — Message-ID письма (RFC 5322);
--        - для REST API — оставляем NULL, идемпотентность там обеспечена
--          самим API-вызовом (catalog/Get без срезов времени).
--   2. Создаёт частичный индекс по (supplier_slug, source_ref) для
--      быстрого поиска «было ли уже письмо с этим Message-ID за
--      последние 30 дней по этому поставщику».
--
-- Зачем:
--   IMAP-fetcher этапа 12.1 ищет в INBOX свежее письмо OCS/Merlion за
--   последние 14 дней. Чтобы не обрабатывать одно и то же письмо два
--   раза подряд (например, при ручном запуске сразу после планового
--   в 14:30), храним Message-ID каждого успешно обработанного письма.
--   Перед обработкой нового письма проверяем, не висит ли его
--   Message-ID в auto_price_load_runs.source_ref за последние 30 дней.
--
-- Обратная совместимость: NULL допустим (REST-канал так и пишет).
-- Идемпотентно: ADD COLUMN IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- =============================================================

ALTER TABLE auto_price_load_runs
    ADD COLUMN IF NOT EXISTS source_ref TEXT;

CREATE INDEX IF NOT EXISTS idx_auto_runs_source_ref
    ON auto_price_load_runs (supplier_slug, source_ref)
    WHERE source_ref IS NOT NULL;
