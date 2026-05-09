-- 0001_init.sql
-- users: получатели уведомлений и единственный признак роли для MVP.
-- Basic Auth в .env отдельная сущность, здесь хранятся только уведомительные данные.
-- digest_period принимает 'yesterday' | 'today' (без enum — проще править в post-MVP).

CREATE TABLE IF NOT EXISTS users (
    id                         BIGSERIAL PRIMARY KEY,
    email                      TEXT        NOT NULL UNIQUE,
    role                       TEXT        NOT NULL CHECK (role IN ('manager', 'owner')),
    notify_telegram_chat_id    BIGINT,
    notify_max_chat_id         BIGINT,
    digest_time_msk            TIME        NOT NULL,
    digest_period              TEXT        NOT NULL CHECK (digest_period IN ('yesterday', 'today')),
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed: 2 пользователя. chat_id оставлены NULL — заполняются в Волне 3Б,
-- когда собственник узнаёт реальные id в Telegram/Max.
INSERT INTO users (email, role, notify_telegram_chat_id, notify_max_chat_id, digest_time_msk, digest_period)
VALUES
    ('manager@quadrotech.local', 'manager', NULL, NULL, '09:00', 'yesterday'),
    ('owner@quadrotech.local',   'owner',   NULL, NULL, '20:00', 'today')
ON CONFLICT (email) DO NOTHING;
