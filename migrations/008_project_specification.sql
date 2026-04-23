-- =============================================================
-- КВАДРО-ТЕХ: миграция 008 — спецификация проекта (этап 6.2)
--
-- На этапе 5 проект = один запрос (projects 1:1 queries).
-- На этапе 6.2 проект становится группой конфигураций: менеджер
-- собирает в один проект несколько запросов и отмечает галочками
-- варианты, которые идут в коммерческое предложение.
--
-- Выбранные варианты хранятся в specification_items.
-- Таблица — «снимок на момент выбора»: auto_name и цены копируются
-- сюда, чтобы последующая загрузка нового прайса не меняла уже
-- сформированную спецификацию.
-- =============================================================

CREATE TABLE specification_items (
    id                    SERIAL        PRIMARY KEY,
    project_id            INT           NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    query_id              INT           NOT NULL REFERENCES queries(id)  ON DELETE CASCADE,
    -- Какой вариант BuildResult выбран: 'Intel' или 'AMD'.
    variant_manufacturer  VARCHAR(20)   NOT NULL,
    quantity              INT           NOT NULL DEFAULT 1 CHECK (quantity > 0),
    -- Порядок строки в спецификации (1, 2, ...). Считается от 1
    -- в рамках project_id — в том порядке, в котором менеджер
    -- ставил галочки.
    position              INT           NOT NULL,
    -- Автоназвание конфигурации на момент выбора (строка вида
    -- «Системный блок / LGA1700 / Intel Core i5-12400F 2.5/4.4GHz / ...»).
    auto_name             VARCHAR(500)  NOT NULL,
    -- Если менеджер отредактировал имя вручную (задел на этап 7).
    custom_name           VARCHAR(500),
    -- Цена одного ПК выбранного варианта на момент выбора.
    unit_usd              NUMERIC(10,2) NOT NULL,
    unit_rub              NUMERIC(10,2) NOT NULL,
    -- Кэш unit × quantity. Пересчитывается при update_quantity.
    total_usd             NUMERIC(10,2) NOT NULL,
    total_rub             NUMERIC(10,2) NOT NULL,
    created_at            TIMESTAMP     NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMP     NOT NULL DEFAULT NOW(),
    -- Один и тот же вариант конфигурации нельзя выбрать дважды.
    UNIQUE (project_id, query_id, variant_manufacturer)
);

-- Выборка позиций спецификации проекта в нужном порядке.
CREATE INDEX idx_spec_project ON specification_items (project_id, position);
