-- 0005_statuses.sql
-- Статусная машина лота + контрактные атрибуты для статуса 'won'.
-- Один лот — одна строка (tender_id PK). changed_at/changed_by — минимальный аудит.
-- Последнее состояние хранится в этой же строке, не в отдельной истории (достаточно для MVP).

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
