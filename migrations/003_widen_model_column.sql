-- =============================================================
-- КВАДРО-ТЕХ: миграция 003
-- Расширяем колонку model с VARCHAR(200) до VARCHAR(500) во всех таблицах
-- компонентов — прайс OCS содержит наименования длиннее 200 символов.
-- =============================================================

-- Процессоры
ALTER TABLE cpus ALTER COLUMN model TYPE VARCHAR(500);

-- Материнские платы
ALTER TABLE motherboards ALTER COLUMN model TYPE VARCHAR(500);

-- Оперативная память
ALTER TABLE rams ALTER COLUMN model TYPE VARCHAR(500);

-- Видеокарты
ALTER TABLE gpus ALTER COLUMN model TYPE VARCHAR(500);

-- Накопители
ALTER TABLE storages ALTER COLUMN model TYPE VARCHAR(500);

-- Корпуса
ALTER TABLE cases ALTER COLUMN model TYPE VARCHAR(500);

-- Блоки питания
ALTER TABLE psus ALTER COLUMN model TYPE VARCHAR(500);

-- Кулеры
ALTER TABLE coolers ALTER COLUMN model TYPE VARCHAR(500);
