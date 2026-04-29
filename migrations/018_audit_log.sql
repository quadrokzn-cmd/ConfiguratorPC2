-- 018: аудит-лог действий пользователей (Этап 9В.4).
--
-- Зачем: фиксируем «нормальные» действия (login, создание/удаление,
-- экспорт КП, отправка письма поставщику и т.п.). Sentry ловит ошибки
-- — этот лог отвечает на вопрос «кто что когда делал».
--
-- Решения по схеме:
--   - user_id ON DELETE SET NULL (а не CASCADE): даже если пользователя
--     удалят, его действия в логе остаются — для расследований
--     инцидентов это критично. Имя при этом сохраняется в user_login
--     (денормализация).
--   - target_id — TEXT, чтобы не привязываться к типу первичных ключей
--     разных таблиц (где-то int, где-то составной).
--   - service — отличаем portal/configurator: одно и то же действие
--     может прийти из обоих сервисов.
--   - payload JSONB DEFAULT '{}' — гибкая полезная нагрузка
--     (project_id, name, diff и т.д.).
--   - Индексы под типичные фильтры UI /admin/audit и под выборку для
--     ретенции по created_at.
CREATE TABLE IF NOT EXISTS audit_log (
    id            BIGSERIAL PRIMARY KEY,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_id       INTEGER REFERENCES users(id) ON DELETE SET NULL,
    user_login    TEXT,
    action        TEXT NOT NULL,
    target_type   TEXT,
    target_id     TEXT,
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    ip            INET,
    user_agent    TEXT,
    service       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_audit_log_created_at
    ON audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_user_id
    ON audit_log (user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_action
    ON audit_log (action);
CREATE INDEX IF NOT EXISTS idx_audit_log_target
    ON audit_log (target_type, target_id);
