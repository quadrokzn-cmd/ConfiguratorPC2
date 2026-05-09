-- =============================================================
-- Migration 030: auctions tables (этап 5 слияния QuadroTech↔ConfiguratorPC2)
--
-- Что делает:
--   1. Создаёт 8 аукционных таблиц, перенесённых из БД `quadrotech`
--      (сейчас заморожена с Этапа 1 слияния).
--   2. Создаёт связанные индексы и базовые seed-данные (settings,
--      excluded_regions, ktru_watchlist).
--
-- Что НЕ переносит (это Этап 6):
--   - nomenclature (печатные SKU) → отдельной таблицей `printers_mfu`.
--   - supplier_prices QT (в C-PC2 уже есть своя одноимённая таблица).
--   - users / suppliers / price_uploads QT — у C-PC2 свои.
--
-- Источник DDL — `auctions_staging/migrations/0002..0009_*.sql` (QT).
-- На этом этапе:
--   - matches.nomenclature_id оставлен BIGINT NOT NULL БЕЗ FK; FK на
--     printers_mfu добавим Этапом 6, когда таблица появится.
--   - tender_items.tender_id, tender_status.tender_id, matches.tender_item_id
--     остаются с FK как в QT.
--
-- Идемпотентно: CREATE TABLE IF NOT EXISTS, ALTER ADD COLUMN IF NOT EXISTS,
-- INSERT ON CONFLICT DO NOTHING. Повторный прогон = NOOP.
-- =============================================================

-- 1. КТРУ-каталог (наполняется лениво при первом матчинге; на момент
--    переноса пуст в QT, count=0).
CREATE TABLE IF NOT EXISTS ktru_catalog (
    code                  TEXT PRIMARY KEY,
    name                  TEXT,
    category              TEXT,
    required_attrs_jsonb  JSONB NOT NULL DEFAULT '{}'::jsonb,
    optional_attrs_jsonb  JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- 2. KTRU watchlist: 4 базовых кода + 6 расширенных + display_name +
--    is_active=false для 8 узких кодов (зонтики оставляем активными).
CREATE TABLE IF NOT EXISTS ktru_watchlist (
    code       TEXT PRIMARY KEY,
    is_active  BOOLEAN     NOT NULL DEFAULT TRUE,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    note       TEXT
);

ALTER TABLE ktru_watchlist ADD COLUMN IF NOT EXISTS display_name TEXT;

-- Seed: 4 базовых кода (миграция 0002).
INSERT INTO ktru_watchlist (code, is_active, note) VALUES
    ('26.20.18.000-00000069', TRUE, 'МФУ ч/б'),
    ('26.20.18.000-00000068', TRUE, 'МФУ цветной'),
    ('26.20.16.120-00000013', TRUE, 'Принтер ч/б'),
    ('26.20.16.120-00000014', TRUE, 'Принтер цветной')
ON CONFLICT (code) DO NOTHING;

-- Seed: 6 расширенных кодов (миграция 0008).
INSERT INTO ktru_watchlist (code, is_active, note) VALUES
    ('26.20.18.000-00000001', TRUE, 'МФУ — общая позиция-зонтик'),
    ('26.20.18.000-00000067', TRUE, 'МФУ струйный A4-A0 ч/б — позиция исключена 04.09.2020, проверим, есть ли исторические/задержанные лоты; если за неделю пусто — снять флаг'),
    ('26.20.16.120-00000001', TRUE, 'Принтер — общая позиция-зонтик'),
    ('26.20.16.120-00000099', TRUE, 'Принтер A0 широкоформатный'),
    ('26.20.16.120-00000100', TRUE, 'Принтер'),
    ('26.20.16.120-00000101', TRUE, 'Принтер')
ON CONFLICT (code) DO NOTHING;

-- Решение собственника от 2026-05-07 (миграция 0009): только 2 зонтика
-- остаются активными. UPDATE-ы идемпотентны.
UPDATE ktru_watchlist
SET is_active = FALSE
WHERE code NOT IN ('26.20.18.000-00000001', '26.20.16.120-00000001');

UPDATE ktru_watchlist
SET display_name = 'Многофункциональное устройство (МФУ)'
WHERE code = '26.20.18.000-00000001';

UPDATE ktru_watchlist
SET display_name = 'Принтер'
WHERE code = '26.20.16.120-00000001';

-- 3. Стоп-лист регионов (логистика).
CREATE TABLE IF NOT EXISTS excluded_regions (
    region_code  TEXT         PRIMARY KEY,
    region_name  TEXT         NOT NULL,
    excluded     BOOLEAN      NOT NULL DEFAULT TRUE,
    reason       TEXT,
    changed_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    changed_by   TEXT
);

INSERT INTO excluded_regions (region_code, region_name, excluded, reason) VALUES
    ('primorsky',   'Приморский край',         TRUE, 'Логистика: слишком далеко'),
    ('sakhalin',    'Сахалинская область',     TRUE, 'Логистика: остров'),
    ('yakutia',     'Якутия',                  TRUE, 'Логистика: дорогостоящая доставка'),
    ('kamchatka',   'Камчатский край',         TRUE, 'Логистика: дорогостоящая доставка'),
    ('magadan',     'Магаданская область',     TRUE, 'Логистика: дорогостоящая доставка'),
    ('chukotka',    'Чукотский АО',            TRUE, 'Логистика: дорогостоящая доставка'),
    ('kaliningrad', 'Калининградская область', TRUE, 'Логистика: эксклав')
ON CONFLICT (region_code) DO NOTHING;

-- 4. Settings (пороги/фильтры аукционной платформы; редактируются из UI).
--    В C-PC2 на момент Этапа 5 нет одноимённой таблицы — конфликта нет,
--    оставляем имя `settings`.
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT         PRIMARY KEY,
    value       TEXT         NOT NULL,
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by  TEXT
);

INSERT INTO settings (key, value) VALUES
    ('margin_threshold_pct',    '15'),
    ('nmck_min_rub',            '30000'),
    ('max_price_per_unit_rub',  '300000'),
    ('contract_reminder_days',  '3'),
    ('deadline_alert_hours',    '24')
ON CONFLICT (key) DO NOTHING;

-- 5. Лоты 44-ФЗ (один лот = один reg_number).
CREATE TABLE IF NOT EXISTS tenders (
    reg_number                TEXT        PRIMARY KEY,
    customer                  TEXT,
    customer_region           TEXT,
    customer_contacts_jsonb   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    nmck_total                NUMERIC(14, 2),
    publish_date              TIMESTAMPTZ,
    submit_deadline           TIMESTAMPTZ,
    delivery_deadline         TIMESTAMPTZ,
    ktru_codes_array          TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
    url                       TEXT,
    raw_html                  TEXT,
    flags_jsonb               JSONB       NOT NULL DEFAULT '{}'::jsonb,
    ingested_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenders_submit_deadline ON tenders (submit_deadline);
CREATE INDEX IF NOT EXISTS idx_tenders_region          ON tenders (customer_region);
CREATE INDEX IF NOT EXISTS idx_tenders_ktru            ON tenders USING GIN (ktru_codes_array);

-- 6. Позиции лота (один товар внутри лота).
CREATE TABLE IF NOT EXISTS tender_items (
    id                   BIGSERIAL PRIMARY KEY,
    tender_id            TEXT           NOT NULL REFERENCES tenders (reg_number) ON DELETE CASCADE,
    position_num         INTEGER        NOT NULL,
    ktru_code            TEXT,
    name                 TEXT,
    qty                  NUMERIC(14, 3) NOT NULL DEFAULT 1,
    unit                 TEXT,
    required_attrs_jsonb JSONB          NOT NULL DEFAULT '{}'::jsonb,
    nmck_per_unit        NUMERIC(14, 2),
    UNIQUE (tender_id, position_num)
);

CREATE INDEX IF NOT EXISTS idx_tender_items_ktru ON tender_items (ktru_code);

-- 7. Статусная машина лота + контрактные атрибуты.
CREATE TABLE IF NOT EXISTS tender_status (
    tender_id                  TEXT         PRIMARY KEY REFERENCES tenders (reg_number) ON DELETE CASCADE,
    status                     TEXT         NOT NULL DEFAULT 'new'
                                   CHECK (status IN ('new', 'in_review', 'will_bid', 'submitted', 'won', 'skipped')),
    assigned_to                TEXT,
    changed_at                 TIMESTAMPTZ  NOT NULL DEFAULT now(),
    changed_by                 TEXT,
    note                       TEXT,
    contract_registry_number   TEXT,
    contract_key_dates_jsonb   JSONB        NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_tender_status_status ON tender_status (status);

-- 8. Результаты матчинга (для каждой пары позиция×SKU — одна строка).
--    nomenclature_id — BIGINT NOT NULL БЕЗ FK; FK to printers_mfu(id)
--    добавим Этапом 6, когда появится таблица printers_mfu.
CREATE TABLE IF NOT EXISTS matches (
    id               BIGSERIAL PRIMARY KEY,
    tender_item_id   BIGINT         NOT NULL REFERENCES tender_items (id) ON DELETE CASCADE,
    nomenclature_id  BIGINT         NOT NULL,
    match_type       TEXT           NOT NULL CHECK (match_type IN ('primary', 'alternative')),
    rule_hits_jsonb  JSONB          NOT NULL DEFAULT '{}'::jsonb,
    price_total_rub  NUMERIC(14, 2),
    margin_rub       NUMERIC(14, 2),
    margin_pct       NUMERIC(7, 2),
    created_at       TIMESTAMPTZ    NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_matches_tender_item  ON matches (tender_item_id);
CREATE INDEX IF NOT EXISTS idx_matches_nomenclature ON matches (nomenclature_id);
CREATE INDEX IF NOT EXISTS idx_matches_type         ON matches (match_type);
