-- =============================================================
-- Migration 0039: smart ingest аукционов
--   (мини-этап «2026-05-16 — умный ingest аукционов»,
--    блокер Волны 3 — Telegram/Max-уведомления).
--
-- Контекст:
--   До этой миграции каждые 2 часа cron `auctions_ingest` делал
--   безусловный `DELETE FROM tender_items` по reg_number при каждом
--   upsert. FK matches.tender_item_id -> tender_items (CASCADE)
--   каскадно убивал ВСЕ matches каждые 2 часа, даже когда лот не
--   менялся. Из-за этого:
--     • уведомления спамили дубликатами на одну и ту же позицию,
--     • до следующего ручного run_matching менеджер видел пустую
--       сводку margin (matching validation 2026-05-15: x11.7 matches
--       появились ТОЛЬКО после ручного перематчинга).
--
-- Что делает миграция:
--   1. Добавляет в `tenders`:
--        - content_hash TEXT — SHA-256 от business-полей TenderCard;
--          NULL для уже-сохранённых лотов, заполнится на первом
--          ingest-тике после миграции.
--        - last_modified_at TIMESTAMPTZ — момент, когда content_hash
--          реально менялся; для уже-сохранённых = updated_at.
--   2. Меняет FK с ON DELETE CASCADE на ON DELETE NO ACTION:
--        - tender_items.tender_id -> tenders(reg_number)
--        - tender_status.tender_id -> tenders(reg_number)
--        - matches.tender_item_id -> tender_items(id)
--      FK matches.nomenclature_id -> printers_mfu (fk_matches_nomenclature_id,
--      ON DELETE CASCADE из миграции 032) НЕ трогаем — это управляется
--      каталогом SKU, а не ingest-ом.
--
-- Идемпотентно: ALTER ADD COLUMN IF NOT EXISTS, FK переустанавливаются
-- через DO-блок с проверкой текущего ON DELETE действия (если уже
-- NO ACTION — пропуск).
--
-- Замечания по синтаксису:
--   - `to_regclass('name')` вместо `'name'-двоеточие-двоеточие-regclass`,
--     потому что SQLAlchemy text() парсер интерпретирует одиночное
--     двоеточие как bind-параметр. to_regclass — семантически идентично.
--   - quote_ident + конкатенация вместо `format('...%I',...)` — символ
--     процента является paramstyle-маркером psycopg2 (pyformat) и ломает
--     test-runner (tests/conftest.py применяет миграции через text()).
--   - У каждой из трёх таблиц (tender_items, tender_status, matches)
--     ровно одна FK на target-таблицу, см. миграцию 030. Поэтому
--     conkey-сравнение по конкретному столбцу не нужно — фильтрации по
--     (conrelid, confrelid, contype='f') достаточно.
-- =============================================================

-- 1. Колонки content_hash + last_modified_at в tenders.
ALTER TABLE tenders ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE tenders ADD COLUMN IF NOT EXISTS last_modified_at TIMESTAMPTZ;

-- Заполняем last_modified_at для уже сохранённых лотов значением updated_at,
-- чтобы аудит-поле имело осмысленное значение с самого начала.
UPDATE tenders
SET last_modified_at = COALESCE(updated_at, ingested_at, now())
WHERE last_modified_at IS NULL;

ALTER TABLE tenders
    ALTER COLUMN last_modified_at SET NOT NULL,
    ALTER COLUMN last_modified_at SET DEFAULT now();

-- content_hash оставляем NULL для существующих строк — на первом ingest-тике
-- после миграции хэш будет вычислен и сохранён. Это однократно даст для всех
-- уже-сохранённых лотов сценарий UPDATE (NULL разный с computed_hash), что
-- приведёт к разовому пересчёту matches per-tender. Это допустимо: matches
-- сейчас и так пусты после каждого ingest-а — миграция фактически
-- ВОССТАНАВЛИВАЕТ их за один проход.

-- Индекс на last_modified_at — полезен для аудита и диагностики (SQL вида
-- «какие лоты менялись за последние N часов»). Cheap.
CREATE INDEX IF NOT EXISTS idx_tenders_last_modified_at
    ON tenders (last_modified_at);


-- 2. Переустанавливаем FK без ON DELETE CASCADE.

-- 2a. tender_items.tender_id -> tenders(reg_number)
DO $$
DECLARE
    fk_name TEXT;
    fk_delete_action CHAR;
BEGIN
    SELECT conname, confdeltype
    INTO fk_name, fk_delete_action
    FROM pg_constraint
    WHERE conrelid = to_regclass('tender_items')
      AND contype = 'f'
      AND confrelid = to_regclass('tenders')
    LIMIT 1;

    IF fk_name IS NULL THEN
        RAISE EXCEPTION 'FK tender_items -> tenders not found';
    END IF;

    -- confdeltype: 'a' = NO ACTION, 'r' = RESTRICT, 'c' = CASCADE,
    -- 'n' = SET NULL, 'd' = SET DEFAULT.
    IF fk_delete_action <> 'a' THEN
        EXECUTE 'ALTER TABLE tender_items DROP CONSTRAINT ' || quote_ident(fk_name);
        EXECUTE 'ALTER TABLE tender_items ADD CONSTRAINT '
            || quote_ident(fk_name)
            || ' FOREIGN KEY (tender_id) REFERENCES tenders(reg_number)'
            || ' ON DELETE NO ACTION';
    END IF;
END$$;

-- 2b. tender_status.tender_id -> tenders(reg_number)
DO $$
DECLARE
    fk_name TEXT;
    fk_delete_action CHAR;
BEGIN
    SELECT conname, confdeltype
    INTO fk_name, fk_delete_action
    FROM pg_constraint
    WHERE conrelid = to_regclass('tender_status')
      AND contype = 'f'
      AND confrelid = to_regclass('tenders')
    LIMIT 1;

    IF fk_name IS NULL THEN
        RAISE EXCEPTION 'FK tender_status -> tenders not found';
    END IF;

    IF fk_delete_action <> 'a' THEN
        EXECUTE 'ALTER TABLE tender_status DROP CONSTRAINT ' || quote_ident(fk_name);
        EXECUTE 'ALTER TABLE tender_status ADD CONSTRAINT '
            || quote_ident(fk_name)
            || ' FOREIGN KEY (tender_id) REFERENCES tenders(reg_number)'
            || ' ON DELETE NO ACTION';
    END IF;
END$$;

-- 2c. matches.tender_item_id -> tender_items(id)
-- ВНИМАНИЕ: у matches ДВА FK — на tender_items и на printers_mfu
-- (миграция 032, fk_matches_nomenclature_id). Фильтр confrelid=tender_items
-- точно попадает в нужный FK.
DO $$
DECLARE
    fk_name TEXT;
    fk_delete_action CHAR;
BEGIN
    SELECT conname, confdeltype
    INTO fk_name, fk_delete_action
    FROM pg_constraint
    WHERE conrelid = to_regclass('matches')
      AND contype = 'f'
      AND confrelid = to_regclass('tender_items')
    LIMIT 1;

    IF fk_name IS NULL THEN
        RAISE EXCEPTION 'FK matches -> tender_items not found';
    END IF;

    IF fk_delete_action <> 'a' THEN
        EXECUTE 'ALTER TABLE matches DROP CONSTRAINT ' || quote_ident(fk_name);
        EXECUTE 'ALTER TABLE matches ADD CONSTRAINT '
            || quote_ident(fk_name)
            || ' FOREIGN KEY (tender_item_id) REFERENCES tender_items(id)'
            || ' ON DELETE NO ACTION';
    END IF;
END$$;
