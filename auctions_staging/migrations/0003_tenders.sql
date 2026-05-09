-- 0003_tenders.sql
-- Аукционы 44-ФЗ: одна строка = один лот (reg_number), идемпотентность по первичному ключу.
-- customer_contacts_jsonb хранит ФИО/email/телефон контрактной службы — данные публичные
-- в силу 44-ФЗ, согласие не требуется (см. п.2 pre-work в плане).
-- flags_jsonb — контейнер для служебных флагов (rejected_by_price_per_unit и т.п.).

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

-- Позиции лота. Одна строка = один товар внутри лота.
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
