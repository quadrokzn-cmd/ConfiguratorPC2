# План: выгрузка и загрузка каталога товаров в Excel

**Дата:** 2026-05-13
**Название функции:** Excel-экспорт и Excel-импорт каталога (комплектующие ПК + печатная техника)
**Владелец:** Собственник-1 (продукт, реализатор)
**Пользователи:** админ (правит характеристики массово в Excel и возвращает обратно)

**Цель:** дать админу возможность скачать всю базу товаров в Excel, поправить характеристики массово (это сейчас неудобно делать поштучно через UI), и загрузить файл обратно — изменения должны попасть в каталог.

---

## Объём

**Два отдельных файла** (решение собственника 2026-05-13):

1. **`Комплектующие_ПК.xlsx`** — комплектующие конфигуратора ПК.
   - 8 листов по типам: `CPU`, `Motherboard`, `RAM`, `GPU`, `Storage`, `Case`, `PSU`, `Cooler`.
   - Источник данных — таблицы `cpu`, `motherboard`, `ram`, `gpu`, `storage`, `case_`, `psu`, `cooler` (или их единая схема — уточнить при discovery).

2. **`Печатная_техника.xlsx`** — для аукционного модуля.
   - 2 листа: `Принтеры`, `МФУ`.
   - Источник — таблица `printers_mfu` (после миграции 031, этап 6 слияния),
     фильтр по `category` (`printer` / `mfu`). В discovery
     2026-05-13 уточнено: таблица называется `printers_mfu`, а не
     `nomenclature` — старое название встречается в коде только в
     legacy-комментариях.

Логика «один файл — один источник истины»: каждый файл скачивается и загружается обратно как единое целое, листы не перепутать.

---

## Структура колонок по листам (зафиксирована Фазой 1 — 2026-05-13)

### Условные обозначения

- **категория «edit»** — редактируемая ячейка, белый фон.
- **категория «ro»** — read-only, жёлтая заливка (`PatternFill('solid', fgColor='FFF4CE')`), import игнорирует значения.
- **категория «hidden»** — скрытая колонка (`column_dimensions[<letter>].hidden = True`). Внутренний `id` идёт первой колонкой — он используется importer'ом как ключ строки.
- **категория «service»** — служебная (например, ячейка курса в строке 1), показывается отдельно (см. «Архитектурные решения»).

### Служебная шапка каждого листа (общая)

| Строка | Колонка A | Колонка B | Назначение |
|---|---|---|---|
| 1 | `Курс ЦБ (USD→RUB)` | число (например `97.4523`) — берётся из `exchange_rates` LATEST на дату выгрузки | Используется в формулах `RUB-ячеек`. Редактируемая. |
| 2 | *(пусто)* | *(пусто)* | Разделитель |
| 3 | Заголовки колонок | … | Шапка таблицы (autofilter применяется к этой строке) |
| 4+ | Данные | … | По одной строке на товар |

### 1. Лист `CPU` (таблица `cpus`)

| Колонка | Тип | Категория | Примечание |
|---|---|---|---|
| `id` | int | hidden | PK, ключ для UPDATE |
| `model` | varchar(500) | edit | Название (NOT NULL) |
| `manufacturer` | varchar(50) | edit | AMD/Intel (NOT NULL) |
| `sku` | varchar(100) | edit | Артикул для matching |
| `gtin` | varchar(20) | edit | Штрих-код (миграция 009) |
| `is_hidden` | bool | edit | TRUE/FALSE (миграция 013), скрывает товар в подборе |
| `socket` | varchar(20) | edit | AM5, LGA1700 |
| `cores` | int | edit | Кол-во ядер |
| `threads` | int | edit | Кол-во потоков |
| `base_clock_ghz` | numeric(4,2) | edit | Базовая частота |
| `turbo_clock_ghz` | numeric(4,2) | edit | Турбо-частота |
| `tdp_watts` | int | edit | TDP |
| `has_integrated_graphics` | bool | edit | Есть ли встроенная графика |
| `memory_type` | varchar(20) | edit | DDR4/DDR5/DDR4+DDR5 |
| `package_type` | varchar(10) | edit | OEM/BOX |
| `process_nm` | int | edit | Техпроцесс |
| `l3_cache_mb` | int | edit | L3-кэш |
| `max_memory_freq` | int | edit | Макс. частота памяти |
| `release_year` | int | edit | Год релиза |
| `Цена min, USD` | number | ro | Минимум по `supplier_prices`(currency='USD') для (category='cpu', component_id=id) |
| `Цена min, RUB` | number/formula | ro | Если есть USD-цена: `=USD_cell*$B$1`. Если только RUB: статика. |
| `Поставщик (min)` | string | ro | Имя поставщика, давшего min-цену |
| `Цена обновлена` | datetime | ro | `supplier_prices.updated_at` |
| `Склад, шт` | int | ro | `SUM(stock_qty)` по активным/активным предложениям |
| `Транзит, шт` | int | ro | `SUM(transit_qty)` по тому же подмножеству |
| `Поставщиков, шт` | int | ro | `COUNT(DISTINCT supplier_id)` с наличием/транзитом |
| `created_at` | timestamp | — | НЕ выгружается |

### 2. Лист `Motherboard` (таблица `motherboards`)

| Колонка | Тип | Категория | Примечание |
|---|---|---|---|
| `id` | int | hidden | |
| `model`, `manufacturer`, `sku`, `gtin`, `is_hidden` | … | edit | Общая часть |
| `socket` | varchar(20) | edit | |
| `chipset` | varchar(50) | edit | |
| `form_factor` | varchar(20) | edit | ATX / mATX / ITX |
| `memory_type` | varchar(20) | edit | DDR4/DDR5 |
| `has_m2_slot` | bool | edit | |
| `memory_slots` | int | edit | |
| `max_memory_gb` | int | edit | |
| `max_memory_freq` | int | edit | |
| `sata_ports` | int | edit | |
| `m2_slots` | int | edit | |
| `has_wifi` | bool | edit | |
| `has_bluetooth` | bool | edit | |
| `pcie_version` | varchar(10) | edit | |
| `pcie_x16_slots` | int | edit | |
| `usb_ports` | int | edit | |
| `Цена min, USD/RUB`, `Поставщик (min)`, `Цена обновлена`, `Склад, шт`, `Транзит, шт`, `Поставщиков, шт` | … | ro | Через `supplier_prices` (category='motherboard') |

### 3. Лист `RAM` (таблица `rams`)

| Колонка | Тип | Категория | Примечание |
|---|---|---|---|
| `id` | int | hidden | |
| `model`, `manufacturer`, `sku`, `gtin`, `is_hidden` | … | edit | |
| `memory_type` | varchar(20) | edit | DDR4/DDR5 |
| `form_factor` | varchar(20) | edit | DIMM/SO-DIMM |
| `module_size_gb` | int | edit | |
| `modules_count` | int | edit | |
| `frequency_mhz` | int | edit | |
| `cl_timing` | int | edit | |
| `voltage` | numeric(3,2) | edit | |
| `has_heatsink` | bool | edit | |
| `has_rgb` | bool | edit | |
| `Цена min, USD/RUB`, `Поставщик (min)`, `Цена обновлена`, `Склад, шт`, `Транзит, шт`, `Поставщиков, шт` | … | ro | (category='ram') |

### 4. Лист `GPU` (таблица `gpus`)

| Колонка | Тип | Категория | Примечание |
|---|---|---|---|
| `id` | int | hidden | |
| `model`, `manufacturer`, `sku`, `gtin`, `is_hidden` | … | edit | |
| `vram_gb` | int | edit | |
| `vram_type` | varchar(20) | edit | GDDR6/GDDR6X/GDDR7 |
| `tdp_watts` | int | edit | |
| `needs_extra_power` | bool | edit | |
| `video_outputs` | text | edit | «HDMI 2.1 x1, DisplayPort 1.4 x3» |
| `core_clock_mhz` | int | edit | |
| `memory_clock_mhz` | int | edit | |
| `gpu_chip` | varchar(100) | edit | |
| `recommended_psu_watts` | int | edit | |
| `length_mm` | int | edit | |
| `height_mm` | int | edit | |
| `power_connectors` | varchar(50) | edit | |
| `fans_count` | int | edit | |
| `Цена min, USD/RUB`, `Поставщик (min)`, `Цена обновлена`, `Склад, шт`, `Транзит, шт`, `Поставщиков, шт` | … | ro | (category='gpu') |

### 5. Лист `Storage` (таблица `storages`)

| Колонка | Тип | Категория | Примечание |
|---|---|---|---|
| `id` | int | hidden | |
| `model`, `manufacturer`, `sku`, `gtin`, `is_hidden` | … | edit | |
| `storage_type` | varchar(10) | edit | SSD/HDD |
| `form_factor` | varchar(20) | edit | M.2 / 2.5" / 3.5" |
| `interface` | varchar(20) | edit | NVMe/SATA |
| `capacity_gb` | int | edit | |
| `read_speed_mb` | int | edit | |
| `write_speed_mb` | int | edit | |
| `tbw` | int | edit | |
| `rpm` | int | edit | |
| `cache_mb` | int | edit | |
| `Цена min, USD/RUB`, `Поставщик (min)`, `Цена обновлена`, `Склад, шт`, `Транзит, шт`, `Поставщиков, шт` | … | ro | (category='storage') |

### 6. Лист `Case` (таблица `cases`)

| Колонка | Тип | Категория | Примечание |
|---|---|---|---|
| `id` | int | hidden | |
| `model`, `manufacturer`, `sku`, `gtin`, `is_hidden` | … | edit | |
| `supported_form_factors` | TEXT[] | edit | **В Excel сериализуется как `ATX,mATX,ITX` через запятую**. Importer делает split + trim. |
| `has_psu_included` | bool | edit | |
| `included_psu_watts` | int | edit | |
| `max_gpu_length_mm` | int | edit | |
| `max_cooler_height_mm` | int | edit | |
| `psu_form_factor` | varchar(20) | edit | |
| `color` | varchar(50) | edit | |
| `material` | varchar(50) | edit | |
| `drive_bays` | int | edit | |
| `fans_included` | int | edit | |
| `has_glass_panel` | bool | edit | |
| `has_rgb` | bool | edit | |
| `Цена min, USD/RUB`, `Поставщик (min)`, `Цена обновлена`, `Склад, шт`, `Транзит, шт`, `Поставщиков, шт` | … | ro | (category='case') |

### 7. Лист `PSU` (таблица `psus`)

| Колонка | Тип | Категория | Примечание |
|---|---|---|---|
| `id` | int | hidden | |
| `model`, `manufacturer`, `sku`, `gtin`, `is_hidden` | … | edit | |
| `power_watts` | int | edit | |
| `form_factor` | varchar(20) | edit | ATX/SFX |
| `efficiency_rating` | varchar(20) | edit | Bronze/Gold/Platinum |
| `modularity` | varchar(20) | edit | |
| `has_12vhpwr` | bool | edit | |
| `sata_connectors` | int | edit | |
| `main_cable_length_mm` | int | edit | |
| `warranty_years` | int | edit | |
| `Цена min, USD/RUB`, `Поставщик (min)`, `Цена обновлена`, `Склад, шт`, `Транзит, шт`, `Поставщиков, шт` | … | ro | (category='psu') |

### 8. Лист `Cooler` (таблица `coolers`)

| Колонка | Тип | Категория | Примечание |
|---|---|---|---|
| `id` | int | hidden | |
| `model`, `manufacturer`, `sku`, `gtin`, `is_hidden` | … | edit | |
| `supported_sockets` | TEXT[] | edit | **Сериализуется как `AM5,LGA1700` через запятую** (как и `supported_form_factors` у case) |
| `max_tdp_watts` | int | edit | |
| `cooler_type` | varchar(20) | edit | воздушный/жидкостный |
| `height_mm` | int | edit | |
| `radiator_size_mm` | int | edit | |
| `fans_count` | int | edit | |
| `noise_db` | numeric(4,1) | edit | |
| `has_rgb` | bool | edit | |
| `Цена min, USD/RUB`, `Поставщик (min)`, `Цена обновлена`, `Склад, шт`, `Транзит, шт`, `Поставщиков, шт` | … | ro | (category='cooler') |

### 9. Лист `Принтеры` (таблица `printers_mfu`, фильтр `category='printer'`)

| Колонка | Тип | Категория | Примечание |
|---|---|---|---|
| `id` | bigint | hidden | PK |
| `sku` | text | edit | UNIQUE NOT NULL |
| `mpn` | text | edit | Manufacturer Part Number |
| `gtin` | text | edit | |
| `brand` | text | edit | NOT NULL |
| `name` | text | edit | NOT NULL |
| `category` | text | edit | Должно остаться `printer` (CHECK constraint) |
| `ktru_codes_array` | TEXT[] | edit | Сериализация через запятую |
| `is_hidden` | bool | edit | |
| `cost_base_rub` | numeric(12,2) | edit | Базовая закупочная цена для маржи (правится UI отдельно — но через Excel тоже можно) |
| `margin_pct_target` | numeric(5,2) | edit | Целевая маржа |
| **Из `attrs_jsonb` (PRINTER_MFU_ATTRS — 9 ключей)**: | | | |
| `print_speed_ppm` | int / "n/a" | edit | |
| `colorness` | ч/б, цветной, n/a | edit | |
| `max_format` | A4, A3, n/a | edit | |
| `duplex` | yes, no, n/a | edit | |
| `resolution_dpi` | int / "n/a" | edit | |
| `network_interface` | LAN,WiFi через запятую / "n/a" | edit | Массив → строка через запятую |
| `usb` | yes, no, n/a | edit | |
| `starter_cartridge_pages` | int / "n/a" | edit | |
| `print_technology` | лазерная, струйная, светодиодная, n/a | edit | |
| **Из `attrs_jsonb` (PRINTER_MFU_DIMENSION_ATTRS — 4 ключа, опциональные)**: | | | |
| `weight_kg` | number / "n/a" / пусто | edit | Вес брутто в упаковке |
| `box_width_cm` | number / "n/a" / пусто | edit | Ширина упаковки |
| `box_height_cm` | number / "n/a" / пусто | edit | Высота упаковки |
| `box_depth_cm` | number / "n/a" / пусто | edit | Глубина упаковки |
| `attrs_source` | text | ro | claude_code / regex_name / manual / союзы; не правится из Excel |
| `Цена min, USD/RUB`, `Поставщик (min)`, `Цена обновлена`, `Склад, шт`, `Транзит, шт`, `Поставщиков, шт` | … | ro | Через `supplier_prices` (category='printer' для листа «Принтеры», `category='mfu'` для листа «МФУ») |
| `attrs_updated_at`, `price_updated_at`, `created_at` | timestamp | — | НЕ выгружаются |

### 10. Лист `МФУ` (таблица `printers_mfu`, фильтр `category='mfu'`)

Структура идентична листу `Принтеры` — те же колонки, тот же набор `attrs_jsonb` (`PRINTER_MFU_ATTRS` + `PRINTER_MFU_DIMENSION_ATTRS`), отличается только фильтр `category='mfu'`.

---

## Архитектурные решения (фиксированы — AskUserQuestion не нужен)

- **Идентификатор строки в Excel — id из БД.** Это первая скрытая колонка (`column_dimensions['A'].hidden = True`). По id importer находит запись для обновления.
- **Что можно править:** характеристики (`attrs_jsonb` для печатной техники; типовые колонки для комплектующих), `name`, `brand`, `sku`, `gtin`, `is_hidden`, `cost_base_rub`, `margin_pct_target`. Цены, остатки, поставщики **редактировать нельзя** — они приходят от поставщиков через автозагрузку прайсов, ручная правка их в Excel перетрётся при следующем тике. Цены показываются read-only (для понимания контекста — «какая цена сейчас у этой модели»).
- **Новые строки (id пустой) — создавать.** Поведение как при ручном добавлении товара.
- **Удалённые строки игнорируем** (не удаляем из БД автоматически — слишком опасно). Удаление товаров — отдельная операция через UI.
- **Конфликт «параллельная правка»:** если за время «скачал → правил → загрузил обратно» товар был обновлён (например, автозагрузкой прайса), в первом MVP принимается **last-write-wins**; в `audit_log` пишется запись о том, кто и когда правил. Diff-резолюшн — отдельный мини-этап после первого фидбэка.
- **Аудит:** каждый импорт фиксируется в `audit_log` (action: `catalog_excel_import`) — кто, когда, сколько строк затронул, по каким листам.
- **Файл импорта сохраняется** в `data/catalog_imports/<timestamp>_<filename>.xlsx` (для отката вручную, если что).
- **UI:** один экран `/databases/catalog-excel` (или внутри существующего `/nomenclature` для печатной техники + новый раздел для комплектующих). Две кнопки на каждый файл: «Скачать» и «Загрузить». Доступ — только админ.
- **Цена — две колонки + редактируемый курс ЦБ в служебной строке 1** (уточнение собственника 2026-05-13):
  - На каждом листе строка 1 содержит:
    - `A1` = `Курс ЦБ (USD→RUB)` (подпись), `B1` = числовое значение курса на дату выгрузки (берётся из `exchange_rates` LATEST по `rate_date DESC, fetched_at DESC`).
    - `B1` редактируемая; при ручной правке курса все RUB-формулы пересчитываются автоматически.
  - Колонки `Цена min, USD` и `Цена min, RUB`:
    - Если у поставщика цена изначально в USD — `Цена min, USD` = статическое число, `Цена min, RUB` = формула вида `=<USD_cell>*$B$1` (абсолютная ссылка на ячейку курса).
    - Если цена изначально в RUB — `Цена min, USD` = пусто, `Цена min, RUB` = статическое число.
  - Заменяет старый «вариант А / Б» в открытых вопросах. **Старый пункт удалён.**
- **Autofilter** — на строку шапки (строка 3 каждого листа): `ws.auto_filter.ref = "A3:<last_col>3"`. Так админ может фильтровать товары по бренду / категории / любой характеристике прямо в Excel.
- **Сериализация массивов** (TEXT[] в БД, например `supported_form_factors`, `supported_sockets`, `ktru_codes_array`, `network_interface`) — в одной ячейке через запятую (`ATX,mATX,ITX`). При import — split по `,`, trim, фильтр пустых. Альтернатива «отдельные колонки» отклонена: ширина листа неконтролируема (у cooler может быть 40+ сокетов).
- **Пустые ключи `attrs_jsonb` / NULL в БД**:
  - Если значение в БД отсутствует → ячейка в Excel пустая.
  - Если значение в БД = `n/a` → ячейка `n/a` (маркер «искали — не нашли»).
  - При import: пустая ячейка → ключ не обновляется (no-op, per-key merge сохраняет существующее значение); `n/a` → пишется `n/a` (как и сейчас в Claude-Code-flow).
- **Read-only визуально** — жёлтая заливка (`PatternFill('solid', fgColor='FFF4CE')`). Excel не блокирует ввод (sheet protection требует пароль и ломает совместимость с LibreOffice), но importer ro-колонки игнорирует с предупреждением в отчёте.
- **Fallback курса при пустой `exchange_rates`** (решение собственника 2026-05-13, после обсуждения по итогам Фазы 2). Если на момент экспорта в таблице `exchange_rates` нет ни одной строки (например, до первого `ensure_initial_rate()` при свежем развёртывании), `excel_export.py` использует hardcoded `90.0` как значение ячейки `B1`. В норме на проде таблица не бывает пустой — `ensure_initial_rate()` зовётся при старте scheduler'а. Альтернативы (вынос в `settings` или 503 «экспорт невозможен») рассмотрены и отклонены — edge case на холодном старте, defensive default достаточен. `ExportReport` несёт флаг `rate_is_fallback=True`, WARNING пишется в лог. В будущем UI (Фаза 4) может показать предупреждение «последний экспорт сделан с fallback-курсом», если флаг был выставлен — мелкая UX-улучшение, не блокер.

---

## Фазы реализации

### Фаза 1. Discovery — структура колонок ✅ (2026-05-13)

- [x] Прочитать схемы таблиц комплектующих (`cpus`, `motherboards`, `rams`, `gpus`, `storages`, `cases`, `psus`, `coolers`) — все колонки и их категории зафиксированы в секции «Структура колонок по листам» выше.
- [x] Прочитать схему `printers_mfu` + `attrs_jsonb` (`PRINTER_MFU_ATTRS` в `portal/services/auctions/catalog/enrichment/schema.py`).
- [x] Расширить `enrichment/schema.py` 4 опциональными ключами габаритов (`weight_kg`, `box_width_cm`, `box_height_cm`, `box_depth_cm`) — словарь общий с планом ПЭК-логистики (`plans/2026-05-13-logistics-pek.md`).
- [x] Уточнить архитектуру: формула RUB-цен от ячейки курса, autofilter, сериализация массивов через запятую, поведение пустых/NA-ячеек.

### Фаза 2. Export (выгрузка) ✅ (2026-05-13)

- [x] Сервис `portal/services/catalog/excel_export.py` с функциями:
  - `export_components_pc(output_path, *, db=None) -> ExportReport` — собирает 8 листов в один файл.
  - `export_printers_mfu(output_path, *, db=None) -> ExportReport` — собирает 2 листа.
- [x] Каждый лист: служебная строка 1 (курс ЦБ), пустая 2, шапка на строке 3, autofilter на A3:<last>3, скрытая первая колонка `id`, ro-колонки (цены / поставщик / `attrs_source`) с жёлтой заливкой `FFF4CE`. RUB-формула `=<USD_cell><row>*$B$1` для товаров с USD-ценой; статика RUB для товаров без USD-цены; курс из `exchange_rates` LATEST или fallback 90.0 если таблица пуста (без обращения к ЦБ — экспорт офлайн).
- [x] Min-цена: WINDOW-функция `ROW_NUMBER() PARTITION BY (component_id, currency)` среди активных поставщиков и активных позиций (`stock_qty>0 OR transit_qty>0`); поставщик USD-min имеет приоритет в колонке «Поставщик (min)» (единая шкала сравнения).
- [x] CLI-обёртка `scripts/catalog_excel_export.py` (`--target {pc|printers|both}`, `--output <dir>`).
- [x] UI-эндпоинт `GET /databases/catalog-excel/download/{pc|printers}` → `FileResponse` xlsx, MIME `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`, имя `Комплектующие_ПК_YYYY-MM-DD.xlsx` / `Печатная_техника_YYYY-MM-DD.xlsx`, доступ `require_admin`, запись `audit_log.action='catalog_excel_export'` с payload `{target, rows_count, sheet_counts, rate_used, rate_fallback}`. Временный файл удаляется `BackgroundTask`'ом после отдачи.
- [x] Тесты: 14 в `tests/test_catalog/test_excel_export.py` (структура файла, RUB-формула, сериализация массивов, фильтр printer/mfu, габариты attrs, неактивные поставщики, out-of-stock) + 6 в `tests/test_portal/test_catalog_excel.py` (HTTP-доступы, audit_log). Полный pytest: 2013 passed, 0 failed (baseline 1995 → +18 catalog + auto-detected ещё пара).

### Фаза 3. Import (загрузка обратно) ✅ (2026-05-13)

- [x] Сервис `portal/services/catalog/excel_import.py`:
  - `import_components_pc(file_path, user_id, *, session=None) -> ImportReport` — читает 8 листов, валидирует, обновляет/создаёт.
  - `import_printers_mfu(file_path, user_id, *, session=None) -> ImportReport`.
- [x] Поведение по строке:
  - id есть → `UPDATE` по id (COALESCE-семантика: пустая ячейка → значение в БД сохраняется); read-only колонки игнорируются (warning в report).
  - id пустой → `INSERT` нового товара (через `required_for_insert`-валидацию NOT NULL полей).
  - id есть, в БД не найден → строка пропускается + warning в report.
  - Полностью пустые строки → skip без ошибки.
- [x] Валидация на уровне ячейки (типы, enum, многозначные через запятую). Ошибки собираются в `ImportReport.errors` и НЕ прерывают остальные строки.
- [x] UI-эндпоинт `POST /databases/catalog-excel/upload/{pc|printers}` (multipart):
  - Доступ — `require_admin`.
  - Сохраняет файл в `data/catalog_imports/<timestamp>_<filename>.xlsx`.
  - Запуск импорта синхронный (типичный размер 8 листов + 2 листа укладывается в секунды). Async-вариант — см. шаблон `admin_price_uploads._run_loader_in_background` для будущей оптимизации, если объёмы вырастут.
  - Возвращает JSON `{updated, inserted, skipped, errors_count, errors[], warnings[], saved_path}`.
- [x] Запись в `audit_log` (action `catalog_excel_import`, target_type=`catalog_excel`, target_id=`pc|printers`, payload — счётчики + путь файла).

**Архитектурные решения (приняты по brief'у, без AskUserQuestion):**

- **Семантика UPDATE = COALESCE-merge:** пустая ячейка → значение в БД сохраняется. Это согласуется с per-key merge для `attrs_jsonb` (печатная техника) и закрывает риск «пользователь случайно стёр FALSE → потерял NOT NULL bool». Минус: «обнулить» поле через Excel нельзя — нужно через UI. Документируется в Фазе 5 (docs/catalog_excel.md).
- **INSERT для NOT NULL DEFAULT-полей через `COALESCE(:field, DEFAULT_SQL)`:** для PC — только `is_hidden=FALSE` (миграция 013); для printers_mfu — `is_hidden=FALSE` и `ktru_codes_array=ARRAY[]::TEXT[]` (миграция 031). Это позволяет батчить INSERT'ы единым SQL-шаблоном через SQLAlchemy executemany (insertmanyvalues), что критично для Railway-latency.
- **Транзакция:** один `session.commit()` на весь файл. SQL-ошибка → `rollback()` всего файла. Валидационные ошибки rollback'у не вызывают — валидные строки коммитятся.
- **Порог sync/background:** синхронный. На полном каталоге (≈1.5к ПК + ≈3к печатной техники) импорт укладывается в секунды локально; для прод-объёмов > 5к строк или 100 МБ — будущая оптимизация через `BackgroundTasks` по шаблону `admin_price_uploads`.

### Фаза 4. UI ✅ (2026-05-13)

- [x] Страница `/databases/catalog-excel` — `portal/templates/databases/catalog_excel.html`:
  - Две карточки («Комплектующие ПК» + «Печатная техника»), каждая с кнопкой «Скачать xlsx» (ссылка GET) и формой «Загрузить xlsx» (multipart POST → JSON-отчёт).
  - Кнопка «Загрузить» получает spinner / `disabled` на время импорта; после ответа сервера рисуется сводка (обновлено/создано/пропущено/ошибок) + сворачиваемый блок с полным JSON.
  - Под каждой карточкой — таблица последних 10 операций из `audit_log` (фильтр `target_type='catalog_excel'`, `target_id IN ('pc','printers')`) с подсветкой ошибочных импортов.
- [x] Router GET `/databases/catalog-excel` в `portal/routers/databases/catalog_excel.py` (`require_admin`, отдельный SQL с ROW_NUMBER per kind, чтобы избежать N+1 и не вытаскивать тысячи строк журнала).
- [x] Sidebar: новый подпункт «Выгрузка/загрузка xlsx» в разделе «Базы данных» (`shared/templates/_partials/sidebar.html`).
- [x] `base.html`: классификация `_path.startswith('/databases/catalog-excel')` → `active_section='databases'`, `active_subsection='catalog-excel'`.
- [x] Иконки `download`, `upload`, `file-spreadsheet`, `info` добавлены в `portal/templates/_macros/icons.html`.

### Мини-этап 2026-05-13 — фикс привязки supplier_prices к printers_mfu ✅

**Симптом.** Собственник скачал «Печатная_техника.xlsx» и увидел:
лист «МФУ» — все 360 строк без цен (USD/RUB/Поставщик/Цена обновлена
пустые); лист «Принтеры» — у 134 из 136 строк пуста USD-колонка
(хотя 73 имеют RUB-цену через формулу). Прайсы 4 поставщиков
загружались штатно (OCS, Merlion, Treolan, Resurs Media — Netlab и
Green Place печать не парсят, ожидаемо).

**Причина.** Баг в orchestrator'е (`portal/services/configurator/price_loaders/orchestrator.py`,
функция `_category_of_component`). Этап 6 слияния (2026-05-08) добавил
в `CATEGORY_TO_TABLE` две категории, указывающие на одну таблицу:
`"printer": "printers_mfu"` и `"mfu": "printers_mfu"`. Хелпер
`_category_of_component(table)` шёл по dict в insertion-порядке и
для `table='printers_mfu'` всегда возвращал первую совпадающую
категорию — `'printer'`. В итоге **все** MFU-строки записывались в
`supplier_prices` с `category='printer'` (439 строк на prod), а
Excel-экспорт листа «МФУ» искал строго `WHERE sp.category='mfu'`
и не находил ничего.

**Фикс.**

- [x] `orchestrator.py::_process_row`: `category = row.our_category`
  (хелпер `_category_of_component` удалён — он был нужен только для
  обратного table → category lookup, который теперь не используется).
- [x] `migrations/0038_supplier_prices_mfu_backfill.sql`:
  `UPDATE supplier_prices SET category='mfu' WHERE pm.category='mfu'
   AND sp.category='printer'` — переименовывает накопленные 439
  ошибочных строк.
- [x] `tests/test_catalog/test_excel_export.py` — два новых теста:
  `test_mfu_prices_use_mfu_category` (золотой путь — Excel «МФУ»
  показывает цены при `sp.category='mfu'`) и
  `test_mfu_price_with_printer_category_is_ignored` (защита от
  регрессии: MFU-строка с category='printer' НЕ всплывает на листе
  «МФУ»).
- [x] `tests/test_price_loaders/test_orchestrator.py` — расширен
  `test_orchestrator_writes_printer_mfu_to_printers_mfu`: проверяет,
  что `supplier_prices.category` совпадает с `printers_mfu.category`
  для каждой загруженной печатной строки.
- [x] Накат миграции на prod 2026-05-13: 439 строк MFU
  переименованы; 0 mismatches `sp.category != pm.category` для
  printer/mfu. Контрольный экспорт: лист «МФУ» — 171/360 SKU с
  ценой (было 0/360); лист «Принтеры» — без изменений (73/136).

**Side-эффекты:** ни Treolan, ни Resurs Media пока не дают
printer/mfu-строк в `supplier_prices` (Treolan loader парсит, но в
последних feed'ах позиций печати нет; Resurs Media сегодня впервые
отработал и его адаптер ещё не маппит групп `_CATEGORY_GROUP_MAP`
на printer/mfu — отдельная задача backlog'а).

### Фаза 5. Тесты + документация ✅ (2026-05-13)

- [x] HTTP-тесты Фазы 2/3 (`tests/test_portal/test_catalog_excel.py`) дополнены 4 UI-тестами Фазы 4: admin 200 + контент страницы (две карточки, ссылки скачивания, data-testid), manager 403, anonymous redirect /login, история операций per kind не перемешивается.
- [x] Юнит-тесты сервисов экспорта/импорта живут в `tests/test_catalog/test_excel_export.py` и `test_excel_import.py` (Фазы 2/3).
- [x] `docs/catalog_excel.md` — формат файла, fallback курса 90.0, COALESCE-семантика, last-write-wins, частые ошибки, CLI-обёртка.

### Мини-этап 2026-05-13 — колонки наличия (Склад/Транзит/Поставщиков) ✅

**Запрос собственника** (после скачивания Excel): «нужно добавить
столбец "наличие" или "доступность", чтобы было понятно, что есть у
поставщиков». В `supplier_prices` уже парсятся `stock_qty` и
`transit_qty` (price-loaders заполняют их из прайсов), но Excel их не
показывал.

**Архитектурное решение.** Три ro-колонки на каждом из 10 листов,
сразу после `Цена обновлена` (price-блок остаётся слитной группой,
блок наличия идёт отдельной группой):

- `Склад, шт`       = `SUM(stock_qty)`   по активным/активным предложениям
- `Транзит, шт`     = `SUM(transit_qty)` по тому же подмножеству
- `Поставщиков, шт` = `COUNT(DISTINCT supplier_id)` с `(stock_qty>0 OR transit_qty>0)`

Подмножество — то же, что для `Цена min, USD/RUB` (`is_active=TRUE` и
`stock_qty>0 OR transit_qty>0`): когерентность с min-ценой. Если в
«Поставщик (min)» виден поставщик-X, его остаток гарантированно вошёл
в суммы.

Альтернативы (одна текстовая колонка «есть/транзит/нет»; числа конкретно
у min-price-поставщика) отклонены: текст теряет деталь «сколько у
скольки поставщиков», числа min-поставщика не отвечают на «есть ли у
кого ещё кроме самого дешёвого». Вариант с тремя числовыми колонками
даёт максимум информации без переусложнения.

**Затронутые файлы:**

- [x] `portal/services/catalog/excel_export.py` — добавлен `_fetch_availability()`
  (GROUP BY-агрегат, отдельный SQL от min-цены), три новых `_Col`
  в `_PC_COMMON_PRICE_SUFFIX` и в `_printer_mfu_columns()`, новые
  ветки `stock:on_hand` / `stock:in_transit` / `stock:suppliers` в
  `_write_sheet`, проброс `availability` через `_build_workbook`.
- [x] `portal/services/catalog/excel_import.py` — три новых записи в
  `_RO_PC` и `_PRINTER_RO_COLS`. Импорт игнорирует их через общий
  read-only-механизм; warning `«read-only columns ignored: ...»`
  включает имена этих колонок, если они были в файле.
- [x] `docs/catalog_excel.md` — секция «Колонки наличия» с семантикой,
  правилами подмножества и значениями пустой ячейки vs `0`.
- [x] `tests/test_catalog/test_excel_export.py` — сценарии:
  агрегация по нескольким активным поставщикам; неактивный поставщик
  не входит в счёт; out-of-stock-предложения не считаются; для МФУ
  агрегаты берутся из `supplier_prices.category='mfu'`.
- [x] `tests/test_catalog/test_excel_import.py` — read-only-warning
  включает имена трёх новых колонок; значения этих ячеек не приводят
  к UPDATE/INSERT.

---

## Что НЕ входит в этот план (вынесено)

- Удаление товаров через Excel (помечать "DELETE" в колонке) — отдельный мини-этап после first feedback.
- Поддержка нескольких языков характеристик (только русский).
- Bulk-операции на цены/остатки — нельзя, см. архитектурное решение выше.
- Конфликт-резолюшн при параллельной правке — первый MVP принимает last-write-wins.
- Импорт CSV — только Excel.

## Открытые вопросы

*(пусто — все вопросы Фазы 1 закрыты 2026-05-13, см. блок «Архитектурные
решения» и «Структура колонок по листам»)*

---

## Итоговый блок

**Статус:** все 5 фаз закрыты 2026-05-13. Фича реализована на 100%.

**Что осталось:** ничего обязательного. Опциональные доработки в `Что НЕ входит в этот план` (удаление через Excel, конфликт-резолюшн с diff, CSV) — отдельными мини-этапами по фидбэку первых пользователей.

**Артефакты:**
- `portal/services/catalog/excel_export.py` — сервис экспорта (Фаза 2).
- `portal/services/catalog/excel_import.py` — сервис импорта (Фаза 3).
- `scripts/catalog_excel_export.py` — CLI-обёртка (Фаза 2).
- `portal/routers/databases/catalog_excel.py` — GET `/databases/catalog-excel` (UI-страница, Фаза 4), GET `/download/{kind}`, POST `/upload/{kind}` (Фазы 2/3).
- `portal/templates/databases/catalog_excel.html` — UI-страница с двумя карточками + историей (Фаза 4).
- `shared/templates/_partials/sidebar.html` + `portal/templates/base.html` — новый подпункт sidebar + классификация активного раздела (Фаза 4).
- `portal/templates/_macros/icons.html` — иконки `download`, `upload`, `file-spreadsheet`, `info` (Фаза 4).
- `tests/test_catalog/test_excel_export.py`, `tests/test_catalog/test_excel_import.py`, `tests/test_portal/test_catalog_excel.py` (Фазы 2/3 + UI-тесты Фазы 4).
- `docs/catalog_excel.md` (Фаза 5).
- `ACTION_CATALOG_EXCEL_EXPORT`, `ACTION_CATALOG_EXCEL_IMPORT` в `shared/audit_actions.py`.
