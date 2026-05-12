-- =============================================================
-- Migration 0036: таблица resurs_media_notifications
--                 (мини-этап 2026-05-12 «Resurs Media Notification»).
--
-- Контекст. По spec API_РМ_v7.5 операция Notification — обязательная
-- к реализации: «уведомления, не предполагающие диалог в переписке,
-- будут доводиться до сведения пользователей только с помощью неё
-- (например, о предстоящем обновлении версии API, о планирующейся
-- приостановке работы API)». Электронная почта в этих случаях не
-- используется.
--
-- Решение по хранилищу: отдельная таблица (не audit_log).
-- Причина — retention audit_log 180 дней может зачистить анонс
-- будущей миграции API (например, версия 8.0 анонсируется заранее).
-- Для уведомлений РМ нужен долгосрочный архив + поле
-- acknowledged_at под будущий UI «прочитал».
--
-- Схема:
--   notification_id  — идентификатор от РМ (уникальный, основа dedup'а
--                      при повторном Notification-вызове в течение суток).
--   text             — текст уведомления (NOT NULL — пустых не ожидаем).
--   attachment_name  — оригинальное имя вложения от РМ (для UI).
--   attachment_path  — относительный путь до сохранённого файла
--                      (data/resurs_media_notifications/...).
--                      NULL — вложения у уведомления нет.
--   fetched_at       — когда мы получили уведомление (для журнала и
--                      сортировки в UI).
--   acknowledged_at  — nullable, проставляется будущим UI «прочитать»;
--                      сейчас всегда NULL.
--
-- Индекс по fetched_at DESC — основной запрос UI/CLI будет
-- «последние N уведомлений».
-- =============================================================

CREATE TABLE IF NOT EXISTS resurs_media_notifications (
    id              BIGSERIAL    PRIMARY KEY,
    notification_id TEXT         NOT NULL UNIQUE,
    text            TEXT         NOT NULL,
    attachment_name TEXT,
    attachment_path TEXT,
    fetched_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    acknowledged_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_rmn_fetched_at
    ON resurs_media_notifications (fetched_at DESC);
