-- =============================================================
-- КВАДРО-ТЕХ: миграция 002
-- 1. Добавляет колонку валюты в таблицу цен поставщиков.
-- 2. Снимает NOT NULL со всех «обязательных» колонок компонентов,
--    кроме model и manufacturer — для гибкой загрузки прайсов.
-- =============================================================

-- -------------------------------------------------------------
-- Валюта в таблице цен поставщиков
-- -------------------------------------------------------------
ALTER TABLE supplier_prices
    ADD COLUMN currency VARCHAR(3) NOT NULL DEFAULT 'RUB';

-- -------------------------------------------------------------
-- Процессоры (cpus)
-- -------------------------------------------------------------
ALTER TABLE cpus ALTER COLUMN socket                  DROP NOT NULL;
ALTER TABLE cpus ALTER COLUMN cores                   DROP NOT NULL;
ALTER TABLE cpus ALTER COLUMN threads                 DROP NOT NULL;
ALTER TABLE cpus ALTER COLUMN base_clock_ghz          DROP NOT NULL;
ALTER TABLE cpus ALTER COLUMN turbo_clock_ghz         DROP NOT NULL;
ALTER TABLE cpus ALTER COLUMN tdp_watts               DROP NOT NULL;
ALTER TABLE cpus ALTER COLUMN has_integrated_graphics DROP NOT NULL;
ALTER TABLE cpus ALTER COLUMN memory_type             DROP NOT NULL;
ALTER TABLE cpus ALTER COLUMN package_type            DROP NOT NULL;

-- -------------------------------------------------------------
-- Материнские платы (motherboards)
-- -------------------------------------------------------------
ALTER TABLE motherboards ALTER COLUMN socket      DROP NOT NULL;
ALTER TABLE motherboards ALTER COLUMN chipset     DROP NOT NULL;
ALTER TABLE motherboards ALTER COLUMN form_factor DROP NOT NULL;
ALTER TABLE motherboards ALTER COLUMN memory_type DROP NOT NULL;
ALTER TABLE motherboards ALTER COLUMN has_m2_slot DROP NOT NULL;

-- -------------------------------------------------------------
-- Оперативная память (rams)
-- -------------------------------------------------------------
ALTER TABLE rams ALTER COLUMN memory_type    DROP NOT NULL;
ALTER TABLE rams ALTER COLUMN form_factor    DROP NOT NULL;
ALTER TABLE rams ALTER COLUMN module_size_gb DROP NOT NULL;
ALTER TABLE rams ALTER COLUMN modules_count  DROP NOT NULL;
ALTER TABLE rams ALTER COLUMN frequency_mhz  DROP NOT NULL;

-- -------------------------------------------------------------
-- Видеокарты (gpus)
-- -------------------------------------------------------------
ALTER TABLE gpus ALTER COLUMN vram_gb          DROP NOT NULL;
ALTER TABLE gpus ALTER COLUMN vram_type        DROP NOT NULL;
ALTER TABLE gpus ALTER COLUMN tdp_watts        DROP NOT NULL;
ALTER TABLE gpus ALTER COLUMN needs_extra_power DROP NOT NULL;
ALTER TABLE gpus ALTER COLUMN video_outputs    DROP NOT NULL;
ALTER TABLE gpus ALTER COLUMN core_clock_mhz   DROP NOT NULL;
ALTER TABLE gpus ALTER COLUMN memory_clock_mhz DROP NOT NULL;

-- -------------------------------------------------------------
-- Накопители (storages)
-- -------------------------------------------------------------
ALTER TABLE storages ALTER COLUMN storage_type DROP NOT NULL;
ALTER TABLE storages ALTER COLUMN form_factor  DROP NOT NULL;
ALTER TABLE storages ALTER COLUMN interface    DROP NOT NULL;
ALTER TABLE storages ALTER COLUMN capacity_gb  DROP NOT NULL;

-- -------------------------------------------------------------
-- Корпуса (cases)
-- -------------------------------------------------------------
ALTER TABLE cases ALTER COLUMN supported_form_factors DROP NOT NULL;
ALTER TABLE cases ALTER COLUMN has_psu_included       DROP NOT NULL;
ALTER TABLE cases ALTER COLUMN included_psu_watts     DROP NOT NULL;

-- -------------------------------------------------------------
-- Блоки питания (psus)
-- -------------------------------------------------------------
ALTER TABLE psus ALTER COLUMN power_watts DROP NOT NULL;

-- -------------------------------------------------------------
-- Кулеры (coolers)
-- -------------------------------------------------------------
ALTER TABLE coolers ALTER COLUMN supported_sockets DROP NOT NULL;
ALTER TABLE coolers ALTER COLUMN max_tdp_watts     DROP NOT NULL;
