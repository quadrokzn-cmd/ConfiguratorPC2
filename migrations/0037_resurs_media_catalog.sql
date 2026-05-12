-- =============================================================
-- Migration 0037: таблица resurs_media_catalog
--                 (мини-этап 2026-05-12 «Resurs Media GetMaterialData
--                  инкрементальная дельта»).
--
-- Контекст. По spec API_РМ_v7.5 раздел «Методические требования к
-- работе с данными» (стр. 4-5) рекомендует: один раз GetMaterialData
-- по всему интересующему каталогу → сохранить локально; регулярно
-- GetPrices → сверять MaterialID с локальной таблицей → по новинкам
-- звать GetMaterialData → дописать в локальную таблицу.
--
-- До этой миграции мы вызывали GetMaterialData по всему списку
-- MaterialID из GetPrices при каждой загрузке (~25 729 позиций на
-- test-стенде), что создавало лишнее давление на rate-limit РМ и
-- противоречило обещанию Сергею (email 2026-05-12).
--
-- Схема:
--   material_id     — идентификатор позиции от РМ (TEXT, PK).
--                     Длина не нормируется spec'ом — у нас в каталоге
--                     встречаются «К104», «Z999-10001» и т.п.
--   part_num        — внутренний код Resurs Media с префиксом
--                     производителя (для будущего матчинга, если
--                     понадобится).
--   material_text   — наименование позиции (UI и поиск).
--   material_group  — group_id, тот же что в _CATEGORY_GROUP_MAP
--                     fetcher'а; нужен для быстрой выборки «всё SSD».
--   vendor          — производитель (бренд).
--   vendor_part     — MPN, ключ матчинга с нашим каталогом.
--   unit_of_measurement, multiplicity — единица измерения и кратность.
--   weight, volume, width, length, height — габариты для логистики.
--   vat             — ставка НДС (Decimal, например 20.00).
--   web_description — расширенное описание (для будущих сценариев
--                     enrichment).
--   raw_jsonb       — ПОЛНЫЙ ответ GetMaterialData по позиции
--                     (включая BarCodes, MaterialCharacteristics,
--                     Images, Certificate, KitParts). Чтобы расширять
--                     pipeline без новой миграции.
--   synced_at       — когда последний раз обновили данные по
--                     MaterialID. Используется для stale-проверки
--                     (порог 30 дней — см. resurs_media_catalog.py).
--
-- Индексы:
--   ix_rmc_synced_at      — для запроса «MaterialID, которые stale».
--   ix_rmc_material_group — для аналитики «сколько позиций в каждой
--                           нашей категории».
-- =============================================================

CREATE TABLE IF NOT EXISTS resurs_media_catalog (
    material_id          TEXT          PRIMARY KEY,
    part_num             TEXT,
    material_text        TEXT,
    material_group       TEXT,
    vendor               TEXT,
    vendor_part          TEXT,
    unit_of_measurement  TEXT,
    multiplicity         NUMERIC,
    weight               NUMERIC,
    volume               NUMERIC,
    width                NUMERIC,
    length               NUMERIC,
    height               NUMERIC,
    vat                  NUMERIC,
    web_description      TEXT,
    raw_jsonb            JSONB         NOT NULL,
    synced_at            TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_rmc_synced_at
    ON resurs_media_catalog (synced_at);

CREATE INDEX IF NOT EXISTS ix_rmc_material_group
    ON resurs_media_catalog (material_group);
