-- =============================================================
-- Migration 031: printers_mfu (этап 6 слияния, 9-я таблица каталога
-- рядом с cpus / motherboards / rams / gpus / storages / cases / psus / coolers).
--
-- Контекст: Этап 5 перенёс 8 аукционных таблиц в kvadro_tech (миграция 030).
-- matches.nomenclature_id остался BIGINT NOT NULL БЕЗ FK — таблица для
-- ссылки появлялась только сейчас. Этот скрипт создаёт printers_mfu по
-- C-PC2-стилю (см. cpus / motherboards / 013_components_is_hidden.sql),
-- унаследовав поля из QT-овской nomenclature (ktru_codes_array,
-- attrs_jsonb, attrs_source, cost_base_rub, margin_pct_target,
-- price_updated_at, is_hidden).
--
-- FK matches.nomenclature_id → printers_mfu(id) подключается ниже,
-- но УЖЕ ПОСЛЕ переноса данных через
-- scripts/migrate_qt_data_to_printers_mfu.py. Чтобы миграция не упала
-- на пустой printers_mfu при первом прогоне раннера, ALTER FK
-- закомментирован — он включается отдельной миграцией 032
-- (создаётся скриптом миграции данных и применяется только когда
-- matches.nomenclature_id сослался на реальные printers_mfu(id)).
--
-- Идемпотентно: CREATE TABLE IF NOT EXISTS, CREATE INDEX IF NOT EXISTS.
-- Повторный прогон = NOOP.
-- =============================================================

-- 1. printers_mfu — единая таблица SKU для категорий 'printer' и 'mfu'.
--    Унаследовано из QT nomenclature: ktru_codes_array, attrs_jsonb,
--    attrs_source, attrs_updated_at, cost_base_rub, margin_pct_target,
--    price_updated_at. C-PC2-стиль: id BIGSERIAL, is_hidden BOOLEAN
--    (как в миграции 013).
CREATE TABLE IF NOT EXISTS printers_mfu (
    id                  BIGSERIAL PRIMARY KEY,
    sku                 TEXT        NOT NULL UNIQUE,
    mpn                 TEXT,
    gtin                TEXT,
    brand               TEXT        NOT NULL,
    name                TEXT        NOT NULL,
    category            TEXT        NOT NULL CHECK (category IN ('printer', 'mfu')),
    ktru_codes_array    TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
    attrs_jsonb         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    attrs_source        TEXT,
    attrs_updated_at    TIMESTAMPTZ,
    cost_base_rub       NUMERIC(12, 2),
    margin_pct_target   NUMERIC(5, 2),
    price_updated_at    TIMESTAMPTZ,
    is_hidden           BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 2. Индексы по аналогии с QT nomenclature + C-PC2-стиль (BTREE для
--    фильтров /admin/components, GIN для ktru-поиска и attrs-поиска).
CREATE INDEX IF NOT EXISTS idx_printers_mfu_brand
    ON printers_mfu (brand);
CREATE INDEX IF NOT EXISTS idx_printers_mfu_category
    ON printers_mfu (category);
CREATE INDEX IF NOT EXISTS idx_printers_mfu_mpn
    ON printers_mfu (mpn);
CREATE INDEX IF NOT EXISTS idx_printers_mfu_ktru
    ON printers_mfu USING GIN (ktru_codes_array);
CREATE INDEX IF NOT EXISTS idx_printers_mfu_attrs
    ON printers_mfu USING GIN (attrs_jsonb);
CREATE INDEX IF NOT EXISTS idx_printers_mfu_attrs_source
    ON printers_mfu (attrs_source);

-- 3. FK matches.nomenclature_id → printers_mfu(id) подключается отдельно
--    после миграции данных QT → printers_mfu. См. migrations/032_matches_fk.sql.
