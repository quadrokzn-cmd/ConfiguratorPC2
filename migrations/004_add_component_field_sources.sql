-- =============================================================
-- КВАДРО-ТЕХ: миграция 004
-- Таблица источников обогащения характеристик компонентов.
--
-- Одна запись = пара (конкретное поле конкретного компонента, откуда пришло
-- значение). Позволяет:
--   - соблюдать идемпотентность при повторном запуске обогащения
--     (не перезаписывать значения, проставленные вручную или ИИ);
--   - считать покрытие по источникам (сколько заполнил regex, сколько AI);
--   - вести аудит изменений.
--
-- Связь с таблицами компонентов — через пару (category, component_id).
-- Жёсткого внешнего ключа не вводим, т.к. компоненты лежат в 8 разных
-- таблицах; целостность поддерживает код обогащения.
-- =============================================================

CREATE TABLE component_field_sources (
    id           SERIAL PRIMARY KEY,
    category     VARCHAR(20) NOT NULL,    -- cpu / motherboard / ram / gpu / storage / case / psu / cooler
    component_id INT         NOT NULL,
    field_name   VARCHAR(50) NOT NULL,    -- socket, cores, tdp_watts и т.д.
    source       VARCHAR(20) NOT NULL,    -- regex / derived / ai / web_search / manual
    confidence   NUMERIC(3,2),            -- 0.00..1.00 ; для regex/derived обычно 1.00
    updated_at   TIMESTAMP   NOT NULL DEFAULT NOW(),
    UNIQUE (category, component_id, field_name)
);

-- Быстрый поиск всех источников конкретного компонента
CREATE INDEX idx_cfs_comp ON component_field_sources (category, component_id);

-- Аналитика по источникам: «сколько полей закрыл regex / AI / derived»
CREATE INDEX idx_cfs_source ON component_field_sources (source);
