-- =============================================================
-- КВАДРО-ТЕХ: миграция 007 — веб-сервис (этап 5)
--
-- Четыре таблицы под авторизацию, проекты менеджеров, историю
-- запросов и контроль дневного бюджета OpenAI.
--
-- Задел на этапы 6-7: проект (projects) и запрос (queries) уже
-- разнесены — на этапе 5 проект создаётся автоматически под каждый
-- запрос (1:1). На этапе 6 к одному проекту сможет привязываться
-- несколько запросов (1:N). Схема при этом не меняется.
-- =============================================================

-- 1. ПОЛЬЗОВАТЕЛИ
CREATE TABLE users (
    id            SERIAL PRIMARY KEY,
    login         VARCHAR(50)  NOT NULL UNIQUE,     -- логин для входа
    password_hash VARCHAR(255) NOT NULL,            -- bcrypt-хеш
    role          VARCHAR(20)  NOT NULL,            -- 'admin' | 'manager'
    name          VARCHAR(100) NOT NULL,            -- отображаемое имя
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_users_login ON users (login);

-- 2. ПРОЕКТЫ
-- На этапе 5 — 1 проект = 1 запрос (создаётся автоматически).
-- На этапе 6 к одному проекту сможет привязываться несколько запросов.
CREATE TABLE projects (
    id         SERIAL PRIMARY KEY,
    user_id    INT          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name       VARCHAR(300) NOT NULL,
    created_at TIMESTAMP    NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_projects_user ON projects (user_id, created_at DESC);

-- 3. ЗАПРОСЫ (каждый вызов process_query)
CREATE TABLE queries (
    id                  SERIAL PRIMARY KEY,
    project_id          INT          NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id             INT          NOT NULL REFERENCES users(id)    ON DELETE CASCADE,
    raw_text            TEXT         NOT NULL,      -- сырой текст менеджера
    parsed_json         JSONB,                      -- resp.parsed (+resolved)
    build_request_json  JSONB,                      -- resp.build_request
    build_result_json   JSONB,                      -- resp.build_result
    formatted_text      TEXT,                       -- resp.formatted_text
    interpretation      TEXT,                       -- resp.interpretation
    warnings_json       JSONB,                      -- resp.warnings
    status              VARCHAR(20)  NOT NULL,      -- 'ok'|'partial'|'failed'|'empty'|'error'
    error_msg           TEXT,                       -- если status='error' — текст ошибки
    cost_usd            NUMERIC(10,6) NOT NULL DEFAULT 0,
    cost_rub            NUMERIC(10,2) NOT NULL DEFAULT 0,
    usd_rub_rate        NUMERIC(10,4),
    created_at          TIMESTAMP    NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_queries_user    ON queries (user_id, created_at DESC);
CREATE INDEX idx_queries_project ON queries (project_id);
CREATE INDEX idx_queries_date    ON queries (created_at);

-- 4. ДНЕВНОЙ БЮДЖЕТ (снэпшот по дням для быстрой отчётности в админке).
-- Источник правды для текущего дня — api_usage_log.cost_rub (считается
-- живым SQL). Таблица daily_budget_log обновляется UPSERT-ом после
-- каждого запроса и используется в /admin/budget для выборки по месяцу.
CREATE TABLE daily_budget_log (
    date            DATE          PRIMARY KEY,
    total_cost_usd  NUMERIC(12,6) NOT NULL DEFAULT 0,
    total_cost_rub  NUMERIC(12,2) NOT NULL DEFAULT 0,
    calls_count     INT           NOT NULL DEFAULT 0,
    updated_at      TIMESTAMP     NOT NULL DEFAULT NOW()
);
