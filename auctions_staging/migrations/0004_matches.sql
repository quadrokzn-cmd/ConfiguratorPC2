-- 0004_matches.sql
-- Результат матчинга: для каждой (позиция лота, SKU) одна строка.
-- primary = лучший по марже; alternative = прочие подходящие. Правила сравнения атрибутов
-- фиксируются в коде (attribute_rules.py) в Волне 2, сюда попадают только hits.

CREATE TABLE IF NOT EXISTS matches (
    id               BIGSERIAL PRIMARY KEY,
    tender_item_id   BIGINT         NOT NULL REFERENCES tender_items (id) ON DELETE CASCADE,
    nomenclature_id  BIGINT         NOT NULL REFERENCES nomenclature (id) ON DELETE CASCADE,
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
