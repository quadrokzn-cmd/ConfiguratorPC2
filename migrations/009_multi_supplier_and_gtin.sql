-- =============================================================
-- КВАДРО-ТЕХ: миграция 009 — несколько поставщиков и GTIN (этап 7)
--
-- Что меняет:
--   1. Добавляет GTIN (штрихкод) во все 8 таблиц компонентов и
--      частичный индекс для быстрого сопоставления по нему.
--   2. Делает suppliers.name UNIQUE, чтобы INSERT ... ON CONFLICT
--      работал и новые поставщики добавлялись идемпотентно.
--   3. Заводит поставщиков Merlion и Treolan.
--   4. Создаёт таблицу unmapped_supplier_items — очередь ручного
--      сопоставления для товаров, которые загрузчик не смог
--      однозначно привязать к существующему компоненту.
-- =============================================================

-- 1. GTIN в таблицах компонентов ---------------------------------

ALTER TABLE cpus         ADD COLUMN IF NOT EXISTS gtin VARCHAR(20);
ALTER TABLE motherboards ADD COLUMN IF NOT EXISTS gtin VARCHAR(20);
ALTER TABLE rams         ADD COLUMN IF NOT EXISTS gtin VARCHAR(20);
ALTER TABLE gpus         ADD COLUMN IF NOT EXISTS gtin VARCHAR(20);
ALTER TABLE storages     ADD COLUMN IF NOT EXISTS gtin VARCHAR(20);
ALTER TABLE cases        ADD COLUMN IF NOT EXISTS gtin VARCHAR(20);
ALTER TABLE psus         ADD COLUMN IF NOT EXISTS gtin VARCHAR(20);
ALTER TABLE coolers      ADD COLUMN IF NOT EXISTS gtin VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_cpus_gtin         ON cpus(gtin)         WHERE gtin IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_motherboards_gtin ON motherboards(gtin) WHERE gtin IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rams_gtin         ON rams(gtin)         WHERE gtin IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_gpus_gtin         ON gpus(gtin)         WHERE gtin IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_storages_gtin     ON storages(gtin)     WHERE gtin IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_cases_gtin        ON cases(gtin)        WHERE gtin IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_psus_gtin         ON psus(gtin)         WHERE gtin IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_coolers_gtin      ON coolers(gtin)      WHERE gtin IS NOT NULL;

-- 2. UNIQUE(name) у поставщиков ----------------------------------
-- В миграции 001 suppliers.name создано без UNIQUE; добавляем сейчас,
-- чтобы ON CONFLICT ниже сработал и гарантировалась уникальность.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'suppliers_name_unique'
    ) THEN
        ALTER TABLE suppliers ADD CONSTRAINT suppliers_name_unique UNIQUE (name);
    END IF;
END$$;

-- 3. Новые поставщики --------------------------------------------
INSERT INTO suppliers (name, is_active) VALUES
    ('Merlion', TRUE),
    ('Treolan', TRUE)
ON CONFLICT (name) DO NOTHING;

-- 4. Таблица ручного сопоставления -------------------------------
-- Строка попадает сюда в трёх случаях:
--   а) автосопоставление нашло несколько кандидатов (ambiguous_mpn/gtin):
--      строка привязана к первому по id, но админ должен подтвердить/сменить;
--   б) автосопоставление ничего не нашло и orchestrator создал новый
--      «скелет» компонента — статус 'created_new', в resolved_component_id
--      ссылка на созданный скелет;
--   в) (зарезервировано) другие подозрительные кейсы (несовпадение бренда,
--      подозрительно короткий MPN и т. п.) — текущий этап их не делает.
CREATE TABLE unmapped_supplier_items (
    id                    SERIAL PRIMARY KEY,
    supplier_id           INT           NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    supplier_sku          VARCHAR(100)  NOT NULL,
    -- Путь категории от поставщика как есть, без обработки
    -- (пример Merlion: «Комплектующие для компьютеров | Материнские Платы | Socket-1700»;
    --  пример Treolan: «Комплектующие->Процессоры»).
    raw_category          VARCHAR(500)  NOT NULL,
    -- Наша категория, если удалось смапить (cpu/motherboard/ram/...),
    -- иначе NULL — такие строки вообще не попадают в orchestrator,
    -- но поле оставлено на будущее.
    guessed_category      VARCHAR(30),
    brand                 VARCHAR(100),
    mpn                   VARCHAR(200),
    gtin                  VARCHAR(20),
    raw_name              TEXT          NOT NULL,
    price                 NUMERIC(12,2),
    currency              VARCHAR(3),
    stock                 INT DEFAULT 0,
    transit               INT DEFAULT 0,
    -- Служебные заметки от orchestrator: причина попадания в очередь,
    -- например «AMBIGUOUS_MPN: matched 3 candidates, picked id=17».
    notes                 TEXT,
    -- Состояния перехода:
    --   'pending'       — ambiguous, ждёт ручного подтверждения;
    --   'created_new'   — скелет создан автоматически, можно объединить;
    --   'merged'        — админ объединил с существующим компонентом;
    --   'confirmed_new' — админ подтвердил, что это отдельный товар.
    status                VARCHAR(20)   NOT NULL DEFAULT 'pending',
    resolved_component_id INT,
    resolved_at           TIMESTAMP,
    resolved_by           INT REFERENCES users(id),
    created_at            TIMESTAMP     NOT NULL DEFAULT NOW(),
    -- Одна строка поставщика = одна запись в очереди. Повторная загрузка
    -- того же прайса не плодит дубликаты.
    UNIQUE (supplier_id, supplier_sku)
);

CREATE INDEX idx_unmapped_status   ON unmapped_supplier_items(status);
CREATE INDEX idx_unmapped_supplier ON unmapped_supplier_items(supplier_id, status);
