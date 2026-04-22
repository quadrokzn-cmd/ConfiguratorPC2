-- =============================================================
-- КВАДРО-ТЕХ: миграция 006
-- Журнал расходов на внешние API (начиная с OpenAI Web Search).
--
-- Одна запись = один вызов API (например, запрос к OpenAI для
-- обогащения одного компонента). Позволяет:
--   - считать фактические расходы в USD и RUB;
--   - диагностировать неудачные вызовы (error_msg);
--   - группировать вызовы по конкретному запуску (run_id),
--     чтобы в конце прогона выдать сводный отчёт.
--
-- Курс USD/RUB сохраняется в каждой записи (usd_rub_rate) — это
-- курс, по которому посчитан cost_rub в момент вызова. Курс
-- получается из ЦБ РФ с суточным кэшем; в случае недоступности
-- применяется fallback из .env.
-- =============================================================

CREATE TABLE api_usage_log (
    id            SERIAL PRIMARY KEY,
    started_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    provider      VARCHAR(20)  NOT NULL,        -- 'openai'
    model         VARCHAR(80)  NOT NULL,        -- 'gpt-4o-mini-search-preview'
    category      VARCHAR(20),                  -- cpu / motherboard / ... (NULL для агрегирующих вызовов)
    component_id  INT,                          -- id компонента в соответствующей таблице
    tokens_in     INT          NOT NULL DEFAULT 0,
    tokens_out    INT          NOT NULL DEFAULT 0,
    web_searches  INT          NOT NULL DEFAULT 0,   -- количество tool-calls web_search в этом вызове
    cost_usd      NUMERIC(10,6) NOT NULL DEFAULT 0,
    cost_rub      NUMERIC(10,2) NOT NULL DEFAULT 0,
    usd_rub_rate  NUMERIC(10,4),                -- курс, применённый к cost_rub
    status        VARCHAR(20)  NOT NULL,        -- 'ok' / 'no_data' / 'error'
    error_msg     TEXT,
    run_id        VARCHAR(40)                   -- UUID текущего прогона для агрегации
);

CREATE INDEX idx_api_usage_run  ON api_usage_log (run_id);
CREATE INDEX idx_api_usage_date ON api_usage_log (started_at);
