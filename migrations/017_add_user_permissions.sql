-- 017: добавляем permissions JSONB для гибких прав на модули портала.
--
-- Этап 9Б.1. Поле наполняется в /admin/users портала чекбоксами по
-- ключам shared.permissions.MODULE_KEYS. Если пусто — manager не имеет
-- доступа ни к одному модулю; admin не смотрит на permissions вообще.
ALTER TABLE users
ADD COLUMN IF NOT EXISTS permissions JSONB NOT NULL DEFAULT '{}'::jsonb;

-- Существующих пользователей пускаем в конфигуратор по умолчанию —
-- это единственный модуль, доступный сейчас. Новые модули появятся
-- в этапах 9Б.2/9Б.3 и им permissions нужно будет выставлять явно.
UPDATE users
SET permissions = '{"configurator": true}'::jsonb
WHERE permissions = '{}'::jsonb;
