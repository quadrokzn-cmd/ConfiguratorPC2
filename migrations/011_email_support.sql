-- =============================================================
-- КВАДРО-ТЕХ: миграция 011 — email-поддержка (этап 8.3)
--
-- Что меняет:
--   1. Добавляет email-адрес в suppliers (nullable для обратной
--      совместимости: для старых записей email может быть не задан).
--   2. Создаёт sent_emails — лог отправок писем поставщикам.
--   3. Сидит email-ы для трёх наших поставщиков.
-- =============================================================

-- 1. EMAIL-адрес у поставщика.
ALTER TABLE suppliers
    ADD COLUMN IF NOT EXISTS email VARCHAR(255);

-- 2. Журнал отправленных писем.
-- ON DELETE CASCADE по project_id: удаляем проект — удаляем его лог.
-- Для supplier_id CASCADE не ставим: удалить поставщика, про которого
-- у нас есть история переписки, — повод подумать, а не молча потерять лог.
CREATE TABLE IF NOT EXISTS sent_emails (
    id              SERIAL PRIMARY KEY,
    project_id      INT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    supplier_id     INT NOT NULL REFERENCES suppliers(id),
    sent_by_user_id INT NOT NULL REFERENCES users(id),
    to_email        VARCHAR(255) NOT NULL,
    subject         TEXT NOT NULL,
    body_html       TEXT NOT NULL,
    status          VARCHAR(20) NOT NULL CHECK (status IN ('sent','failed')),
    error_message   TEXT,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sent_emails_project  ON sent_emails(project_id);
CREATE INDEX IF NOT EXISTS idx_sent_emails_supplier ON sent_emails(supplier_id);

-- 3. Сид адресов для поставщиков, которых заводит миграция 009.
-- Для OCS (из 001) записи могло и не быть — тогда UPDATE просто ничего не сделает.
UPDATE suppliers SET email = 'egarifullina@ocs.ru'   WHERE name = 'OCS';
UPDATE suppliers SET email = 'matveeva.y@merlion.ru' WHERE name = 'Merlion';
UPDATE suppliers SET email = 'd.teretin@treolan.ru'  WHERE name = 'Treolan';
