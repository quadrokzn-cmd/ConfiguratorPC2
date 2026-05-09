-- 0002_catalog.sql
-- Каталог: поставщики, КТРУ, наша номенклатура, прайсы поставщиков, история загрузок.
-- Денежные поля — NUMERIC(14,2). jsonb для атрибутов карточек — нет заранее известной схемы.
-- ktru_codes_array у номенклатуры — TEXT[]: одна SKU может подходить под несколько КТРУ.

-- Поставщики
CREATE TABLE IF NOT EXISTS suppliers (
    id             BIGSERIAL PRIMARY KEY,
    code           TEXT        NOT NULL UNIQUE,
    name           TEXT        NOT NULL,
    adapter_class  TEXT        NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO suppliers (code, name, adapter_class) VALUES
    ('merlion',      'Merlion',        'MerlionPriceLoader'),
    ('ocs',          'OCS',            'OcsPriceLoader'),
    ('treolan',      'Treolan',        'TreolanPriceLoader'),
    ('resursmedia',  'Ресурс-Медиа',   'ResursMediaPriceLoader'),
    ('asbis',        'ASBIS',          'AsbisPriceLoader'),
    ('sandisk',      'SanDisk',        'SanDiskPriceLoader'),
    ('marvel',       'Марвел',         'MarvelPriceLoader'),
    ('a1tis',        'А1Тис',          'A1TisPriceLoader')
ON CONFLICT (code) DO NOTHING;

-- КТРУ-каталог: наполняется лениво при первом матчинге.
CREATE TABLE IF NOT EXISTS ktru_catalog (
    code                  TEXT PRIMARY KEY,
    name                  TEXT,
    category              TEXT,
    required_attrs_jsonb  JSONB NOT NULL DEFAULT '{}'::jsonb,
    optional_attrs_jsonb  JSONB NOT NULL DEFAULT '{}'::jsonb
);

-- Watchlist активных КТРУ: чтение ингестом, редактирование из UI/INSERT без деплоя.
CREATE TABLE IF NOT EXISTS ktru_watchlist (
    code       TEXT PRIMARY KEY,
    is_active  BOOLEAN     NOT NULL DEFAULT TRUE,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    note       TEXT
);

INSERT INTO ktru_watchlist (code, is_active, note) VALUES
    ('26.20.18.000-00000069', TRUE, 'МФУ ч/б'),
    ('26.20.18.000-00000068', TRUE, 'МФУ цветной'),
    ('26.20.16.120-00000013', TRUE, 'Принтер ч/б'),
    ('26.20.16.120-00000014', TRUE, 'Принтер цветной')
ON CONFLICT (code) DO NOTHING;

-- Номенклатура — единая таблица SKU. attrs_jsonb заполняется парсером DNS.
-- cost_base_rub — кеш минимальной активной закупочной цены для формулы маржи.
CREATE TABLE IF NOT EXISTS nomenclature (
    id                 BIGSERIAL PRIMARY KEY,
    sku                TEXT        NOT NULL UNIQUE,
    mpn                TEXT,
    gtin               TEXT,
    brand              TEXT,
    name               TEXT        NOT NULL,
    category           TEXT,
    ktru_codes_array   TEXT[]      NOT NULL DEFAULT ARRAY[]::TEXT[],
    attrs_jsonb        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    cost_base_rub      NUMERIC(14, 2),
    margin_pct_target  NUMERIC(5, 2),
    price_updated_at   TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_nomenclature_mpn    ON nomenclature (mpn);
CREATE INDEX IF NOT EXISTS idx_nomenclature_brand  ON nomenclature (brand);
CREATE INDEX IF NOT EXISTS idx_nomenclature_ktru   ON nomenclature USING GIN (ktru_codes_array);

-- Цены поставщиков: 1 строка на пару (поставщик, SKU). UNIQUE гарантирует апсерт.
CREATE TABLE IF NOT EXISTS supplier_prices (
    id              BIGSERIAL PRIMARY KEY,
    supplier_id     BIGINT        NOT NULL REFERENCES suppliers (id) ON DELETE CASCADE,
    nomenclature_id BIGINT        NOT NULL REFERENCES nomenclature (id) ON DELETE CASCADE,
    supplier_sku    TEXT,
    price_rub       NUMERIC(14, 2) NOT NULL,
    stock_qty       INTEGER        NOT NULL DEFAULT 0,
    transit_qty     INTEGER        NOT NULL DEFAULT 0,
    updated_at      TIMESTAMPTZ    NOT NULL DEFAULT now(),
    UNIQUE (supplier_id, nomenclature_id)
);

CREATE INDEX IF NOT EXISTS idx_supplier_prices_supplier ON supplier_prices (supplier_id);
CREATE INDEX IF NOT EXISTS idx_supplier_prices_nomencl  ON supplier_prices (nomenclature_id);

-- История загрузок прайсов (для отчётов на /nomenclature/upload).
CREATE TABLE IF NOT EXISTS price_uploads (
    id               BIGSERIAL PRIMARY KEY,
    supplier_id      BIGINT       NOT NULL REFERENCES suppliers (id) ON DELETE CASCADE,
    filename         TEXT         NOT NULL,
    uploaded_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    uploaded_by      TEXT,
    rows_total       INTEGER      NOT NULL DEFAULT 0,
    rows_matched     INTEGER      NOT NULL DEFAULT 0,
    rows_unmatched   INTEGER      NOT NULL DEFAULT 0,
    status           TEXT         NOT NULL DEFAULT 'success',
    notes            TEXT
);
