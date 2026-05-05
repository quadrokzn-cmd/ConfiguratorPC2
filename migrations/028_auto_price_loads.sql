-- =============================================================
-- КВАДРО-ТЕХ: миграция 028 — каркас автозагрузки прайсов
-- (этап 12.3, первый подэтап блока 12.x)
--
-- Что меняет:
--   1. Создаёт таблицу auto_price_loads — состояние подключения по
--      каждому из 6 поставщиков (один канал автозагрузки = одна
--      строка). Хранит флаг enabled, последний статус, ссылку на
--      последний price_uploads и краткое описание ошибки.
--   2. Создаёт таблицу auto_price_load_runs — журнал каждого запуска
--      (ручного или планового). Связь с price_uploads — slug-агностик,
--      через FK с ON DELETE SET NULL: даже если запись price_uploads
--      позже удалится при чистке, журнал автозагрузок не сломается.
--   3. Делает seed по 6 поставщикам (enabled=FALSE по умолчанию).
--
-- Зачем:
--   Этап 12.3 запускает ежедневную авто-подгрузку прайсов APScheduler-ом
--   в 04:00 МСК (после бекапа БД в 03:00). Этот каркас общий для всех
--   каналов: REST API (Treolan на 12.3), IMAP-почта (12.1/12.2),
--   прямые URL (12.4). Новый канал = новый класс-наследник
--   BaseAutoFetcher; миграции на него не нужны.
--
-- Идемпотентно: CREATE TABLE IF NOT EXISTS, INSERT ON CONFLICT.
-- =============================================================

CREATE TABLE IF NOT EXISTS auto_price_loads (
    id                   SERIAL PRIMARY KEY,
    supplier_slug        VARCHAR(64) NOT NULL UNIQUE,
    enabled              BOOLEAN NOT NULL DEFAULT FALSE,
    status               VARCHAR(32) NOT NULL DEFAULT 'idle',
    last_run_at          TIMESTAMPTZ,
    last_success_at      TIMESTAMPTZ,
    last_error_at        TIMESTAMPTZ,
    last_error_message   TEXT,
    last_price_upload_id INTEGER REFERENCES price_uploads(id) ON DELETE SET NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS auto_price_load_runs (
    id               SERIAL PRIMARY KEY,
    supplier_slug    VARCHAR(64) NOT NULL,
    started_at       TIMESTAMPTZ NOT NULL,
    finished_at      TIMESTAMPTZ,
    status           VARCHAR(32) NOT NULL,  -- 'running' / 'success' / 'error'
    error_message    TEXT,
    price_upload_id  INTEGER REFERENCES price_uploads(id) ON DELETE SET NULL,
    triggered_by     VARCHAR(32) NOT NULL,  -- 'scheduled' / 'manual'
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_auto_runs_slug_started
    ON auto_price_load_runs(supplier_slug, started_at DESC);

-- Seed по 6 поставщикам, enabled=FALSE.
INSERT INTO auto_price_loads (supplier_slug, enabled) VALUES
    ('treolan',      FALSE),
    ('ocs',          FALSE),
    ('merlion',      FALSE),
    ('netlab',       FALSE),
    ('resurs_media', FALSE),
    ('green_place',  FALSE)
ON CONFLICT (supplier_slug) DO NOTHING;
