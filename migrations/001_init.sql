-- =============================================================
-- КВАДРО-ТЕХ: начальная схема базы данных PostgreSQL
-- =============================================================

-- 1. ПРОЦЕССОРЫ
CREATE TABLE cpus (
    id SERIAL PRIMARY KEY,
    model VARCHAR(200) NOT NULL,                -- модель, например "Ryzen 7 7700X"
    manufacturer VARCHAR(50) NOT NULL,          -- AMD / Intel
    sku VARCHAR(100),                           -- артикул для сопоставления с прайсами
    -- Обязательные
    socket VARCHAR(20) NOT NULL,                -- AM5, LGA1700
    cores INT NOT NULL,
    threads INT NOT NULL,
    base_clock_ghz NUMERIC(4,2) NOT NULL,
    turbo_clock_ghz NUMERIC(4,2) NOT NULL,
    tdp_watts INT NOT NULL,
    has_integrated_graphics BOOLEAN NOT NULL,
    memory_type VARCHAR(20) NOT NULL,           -- DDR4 / DDR5 / DDR4+DDR5
    package_type VARCHAR(10) NOT NULL,          -- OEM / BOX
    -- Опциональные
    process_nm INT,
    l3_cache_mb INT,
    max_memory_freq INT,
    release_year INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 2. МАТЕРИНСКИЕ ПЛАТЫ
CREATE TABLE motherboards (
    id SERIAL PRIMARY KEY,
    model VARCHAR(200) NOT NULL,
    manufacturer VARCHAR(50) NOT NULL,
    sku VARCHAR(100),
    -- Обязательные
    socket VARCHAR(20) NOT NULL,
    chipset VARCHAR(50) NOT NULL,
    form_factor VARCHAR(20) NOT NULL,           -- ATX / mATX / ITX
    memory_type VARCHAR(20) NOT NULL,           -- DDR4 / DDR5
    has_m2_slot BOOLEAN NOT NULL,
    -- Опциональные
    memory_slots INT,
    max_memory_gb INT,
    max_memory_freq INT,
    sata_ports INT,
    m2_slots INT,
    has_wifi BOOLEAN,
    has_bluetooth BOOLEAN,
    pcie_version VARCHAR(10),
    pcie_x16_slots INT,
    usb_ports INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 3. ОПЕРАТИВНАЯ ПАМЯТЬ
CREATE TABLE rams (
    id SERIAL PRIMARY KEY,
    model VARCHAR(200) NOT NULL,
    manufacturer VARCHAR(50) NOT NULL,
    sku VARCHAR(100),
    -- Обязательные
    memory_type VARCHAR(20) NOT NULL,           -- DDR4 / DDR5
    form_factor VARCHAR(20) NOT NULL,           -- DIMM / SO-DIMM
    module_size_gb INT NOT NULL,
    modules_count INT NOT NULL,
    frequency_mhz INT NOT NULL,
    -- Опциональные
    cl_timing INT,
    voltage NUMERIC(3,2),
    has_heatsink BOOLEAN,
    has_rgb BOOLEAN,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 4. ВИДЕОКАРТЫ
CREATE TABLE gpus (
    id SERIAL PRIMARY KEY,
    model VARCHAR(200) NOT NULL,
    manufacturer VARCHAR(50) NOT NULL,
    sku VARCHAR(100),
    -- Обязательные
    vram_gb INT NOT NULL,
    vram_type VARCHAR(20) NOT NULL,             -- GDDR6 / GDDR6X / GDDR7
    tdp_watts INT NOT NULL,
    needs_extra_power BOOLEAN NOT NULL,
    video_outputs TEXT NOT NULL,                -- "HDMI 2.1 x1, DisplayPort 1.4 x3"
    core_clock_mhz INT NOT NULL,
    memory_clock_mhz INT NOT NULL,
    -- Опциональные
    gpu_chip VARCHAR(100),
    recommended_psu_watts INT,
    length_mm INT,
    height_mm INT,
    power_connectors VARCHAR(50),
    fans_count INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 5. НАКОПИТЕЛИ
CREATE TABLE storages (
    id SERIAL PRIMARY KEY,
    model VARCHAR(200) NOT NULL,
    manufacturer VARCHAR(50) NOT NULL,
    sku VARCHAR(100),
    -- Обязательные
    storage_type VARCHAR(10) NOT NULL,          -- SSD / HDD
    form_factor VARCHAR(20) NOT NULL,           -- M.2 / 2.5" / 3.5"
    interface VARCHAR(20) NOT NULL,             -- NVMe / SATA
    capacity_gb INT NOT NULL,
    -- Опциональные
    read_speed_mb INT,
    write_speed_mb INT,
    tbw INT,
    rpm INT,
    cache_mb INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 6. КОРПУСА
CREATE TABLE cases (
    id SERIAL PRIMARY KEY,
    model VARCHAR(200) NOT NULL,
    manufacturer VARCHAR(50) NOT NULL,
    sku VARCHAR(100),
    -- Обязательные
    supported_form_factors TEXT[] NOT NULL,     -- {'ATX','mATX','ITX'}
    has_psu_included BOOLEAN NOT NULL,
    included_psu_watts INT,
    -- Опциональные
    max_gpu_length_mm INT,
    max_cooler_height_mm INT,
    psu_form_factor VARCHAR(20),
    color VARCHAR(50),
    material VARCHAR(50),
    drive_bays INT,
    fans_included INT,
    has_glass_panel BOOLEAN,
    has_rgb BOOLEAN,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 7. БЛОКИ ПИТАНИЯ
CREATE TABLE psus (
    id SERIAL PRIMARY KEY,
    model VARCHAR(200) NOT NULL,
    manufacturer VARCHAR(50) NOT NULL,
    sku VARCHAR(100),
    -- Обязательные
    power_watts INT NOT NULL,
    -- Опциональные
    form_factor VARCHAR(20),                    -- ATX / SFX
    efficiency_rating VARCHAR(20),              -- Bronze / Gold / Platinum
    modularity VARCHAR(20),
    has_12vhpwr BOOLEAN,
    sata_connectors INT,
    main_cable_length_mm INT,
    warranty_years INT,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 8. КУЛЕРЫ
CREATE TABLE coolers (
    id SERIAL PRIMARY KEY,
    model VARCHAR(200) NOT NULL,
    manufacturer VARCHAR(50) NOT NULL,
    sku VARCHAR(100),
    -- Обязательные
    supported_sockets TEXT[] NOT NULL,          -- {'AM5','LGA1700'}
    max_tdp_watts INT NOT NULL,
    -- Опциональные
    cooler_type VARCHAR(20),                    -- воздушный / жидкостный
    height_mm INT,
    radiator_size_mm INT,
    fans_count INT,
    noise_db NUMERIC(4,1),
    has_rgb BOOLEAN,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 9. ПОСТАВЩИКИ
CREATE TABLE suppliers (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    contact_email VARCHAR(200),
    contact_phone VARCHAR(50),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

-- 10. ЦЕНЫ, ОСТАТКИ И ТРАНЗИТ ПО ПОСТАВЩИКАМ
-- Одна таблица на все категории. Связь с компонентом через пару (category, component_id).
CREATE TABLE supplier_prices (
    id SERIAL PRIMARY KEY,
    supplier_id INT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    category VARCHAR(20) NOT NULL,              -- cpu/motherboard/ram/gpu/storage/case/psu/cooler
    component_id INT NOT NULL,
    supplier_sku VARCHAR(100),                  -- артикул у поставщика
    price NUMERIC(12,2) NOT NULL,
    stock_qty INT NOT NULL DEFAULT 0,
    transit_qty INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (supplier_id, category, component_id)
);

CREATE INDEX idx_supplier_prices_component ON supplier_prices(category, component_id);

-- 11. ИСТОРИЯ ЗАГРУЗОК ПРАЙС-ЛИСТОВ
CREATE TABLE price_uploads (
    id SERIAL PRIMARY KEY,
    supplier_id INT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    filename VARCHAR(500),
    uploaded_at TIMESTAMP NOT NULL DEFAULT NOW(),
    rows_total INT,
    rows_matched INT,
    rows_unmatched INT,
    status VARCHAR(20),                         -- success / partial / failed
    notes TEXT
);
