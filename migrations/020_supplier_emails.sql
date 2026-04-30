-- =============================================================
-- КВАДРО-ТЕХ: миграция 020 — email-контакты Netlab/Ресурс Медиа/
-- Green Place (этап 11.1.1)
--
-- Что меняет:
--   Проставляет email-адреса трёх «новых» поставщиков, заведённых
--   миграцией 019 с email=NULL. Адреса нужны для рассылок «есть
--   в наличии?» из этапа 8 (sent_emails / SMTP-агент).
--
-- Идемпотентно: UPDATE срабатывает только пока email всё ещё NULL.
-- Если кто-то ранее обновит email через /admin/suppliers вручную —
-- значение уже не NULL и эта миграция его не затрёт.
-- =============================================================

UPDATE suppliers SET email = 'ZVerkhovykh@netlab.ru'
 WHERE name = 'Netlab'        AND email IS NULL;

UPDATE suppliers SET email = 'afedichkin@resurs-media.ru'
 WHERE name = 'Ресурс Медиа'  AND email IS NULL;

UPDATE suppliers SET email = 'Julik@grplace.ru'
 WHERE name = 'Green Place'   AND email IS NULL;
