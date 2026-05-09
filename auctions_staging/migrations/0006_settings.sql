-- 0006_settings.sql
-- Пороги/фильтры платформы (все редактируются из UI, не из .env) + стоп-лист регионов.
-- settings.value — TEXT: простой key/value-стор; типизация прикладного слоя.
-- region_code — короткий транслит-код; region_name — человекочитаемое имя для UI.

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

CREATE TABLE IF NOT EXISTS excluded_regions (
    region_code  TEXT         PRIMARY KEY,
    region_name  TEXT         NOT NULL,
    excluded     BOOLEAN      NOT NULL DEFAULT TRUE,
    reason       TEXT,
    changed_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    changed_by   TEXT
);

INSERT INTO excluded_regions (region_code, region_name, excluded, reason) VALUES
    ('primorsky',   'Приморский край',      TRUE, 'Логистика: слишком далеко'),
    ('sakhalin',    'Сахалинская область',  TRUE, 'Логистика: остров'),
    ('yakutia',     'Якутия',               TRUE, 'Логистика: дорогостоящая доставка'),
    ('kamchatka',   'Камчатский край',      TRUE, 'Логистика: дорогостоящая доставка'),
    ('magadan',     'Магаданская область',  TRUE, 'Логистика: дорогостоящая доставка'),
    ('chukotka',    'Чукотский АО',         TRUE, 'Логистика: дорогостоящая доставка'),
    ('kaliningrad', 'Калининградская область', TRUE, 'Логистика: эксклав')
ON CONFLICT (region_code) DO NOTHING;
