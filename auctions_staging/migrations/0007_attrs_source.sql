-- 0007_attrs_source.sql
-- Источник атрибутов модели для аудита: 'claude_code' | 'manual' | NULL.
-- NULL — атрибуты ещё не заполнены (созданы пустым {} при загрузке прайса).

ALTER TABLE nomenclature
    ADD COLUMN IF NOT EXISTS attrs_source TEXT;

ALTER TABLE nomenclature
    ADD COLUMN IF NOT EXISTS attrs_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_nomenclature_attrs_source ON nomenclature (attrs_source);
