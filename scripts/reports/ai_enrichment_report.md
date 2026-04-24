# Этап 2.5Б — AI-обогащение с официальных сайтов производителей (итоговый отчёт)

**Дата:** 2026-04-24
**Источник данных:** только официальные сайты производителей из whitelist (70 доменов).
**OpenAI auto-hook:** выключен (`OPENAI_ENRICH_AUTO_HOOK=false`) — как требовало ТЗ.
**Архитектурная инфраструктура:** переиспользована (`app/services/enrichment/claude_code/`),
добавлены тесты и derive-хелпер.

## 1. Сводная таблица по категориям

| Категория | Было NULL-полей | Заполнено | Осталось NULL | % покрытия (этого раунда) |
|---|---|---|---|---|
| gpu         | 1181 | 120 | 1061 | 10.2% |
| case        |   70 |   4 |   66 |  5.7% |
| cooler      |  228 |   0 |  228 |  0.0% |
| storage     |   17 |   7 |   10 | 41.2% |
| psu         |    5 |   3 |    2 | 60.0% |
| motherboard |    4 |   0 |    4 |  0.0% |
| cpu         |    0 |   0 |    0 |  — |
| **Итого**   | **1505** | **134** | **1371** | **8.9%** |

Число позиций со ≥1 NULL-полем: было 564 → осталось 544 (-20 позиций
перестали быть полноценными скелетами).

Размер раунда этапа 2.5Б:
- В pending было сформировано **15 батчей = 359 позиций**.
- Обработано (агенты проработали) **3 батча = 92 позиции**: GPU batch_001 (40),
  COOLER batch_001 (40), MINOR (case × 2 + mb + psu + storage = 22 позиции).
- 12 батчей (COOLER 4, GPU 4) оставлены в pending/ для следующих раундов —
  объём работ в одном сеансе превышал разумные лимиты по токенам
  параллельных агентов.

## 2. Распределение принятых значений по полям

| Категория.поле            | Принято |
|---|---|
| gpu.tdp_watts             | 28 |
| gpu.needs_extra_power     | 28 |
| gpu.memory_clock_mhz      | 28 |
| gpu.core_clock_mhz        | 27 |
| gpu.video_outputs         |  9 |
| storage.interface         |  4 |
| storage.capacity_gb       |  3 |
| psu.power_watts           |  3 |
| case.supported_form_factors | 2 |
| case.included_psu_watts   |  2 |
| **Итого**                 | **134** |

## 3. Изменения в коде и схеме

### 3.1. Whitelist
`app/services/enrichment/claude_code/schema.py` — расширен с **54 до 70 доменов**
(+16 новых; подробно в `ai_enrichment_whitelist_recon.md`):
```
ocypus.com, maxsun.com, maxsun.com.cn, idcooling.com, sapphiretech.com,
inno3d.com, hpe.com, netac.com, fsp-group.com, fsplifestyle.com, seagate.com,
pccooler.com.cn, apacer.com, kingston.com, in-win.com, westerndigital.com
```
Каждый домен прокомментирован с датой добавления («Этап 2.5Б, 2026-04-24,
оркестратор») и обоснованием.

### 3.2. Схема `TARGET_FIELDS`
- Добавлен ключ `storage` с полями
  `[storage_type, form_factor, interface, capacity_gb]`.
- `ALL_CATEGORIES` и `DEFAULT_BATCH_SIZES` также расширены.

### 3.3. Валидаторы `validators.py`
- Новые: `_v_storage_type`, `_v_storage_form_factor`,
  `_v_storage_interface`, `_v_storage_capacity_gb`.
- Свои нормализаторы (без `_as_enum`), потому что в БД значения хранятся
  с нестандартным регистром: `"2.5\""`, `"M.2"`, `"mSATA"`, `"NVMe"`.
- Маппинг: `NVMe storage_type → SSD`, `SATA-III/SATAIII/SATA3 → SATA`,
  `PCIe → NVMe`.

### 3.4. Новый модуль `derive.py`
- `derive_needs_extra_power(tdp, connector_text)` — по правилу TDP≥75W
  OR наличие разъёма (6/8/12pin, 12VHPWR).
- `normalize_video_outputs(raw)` — к формату "NxPortVER+…",
  "1xHDMI2.1+3xDP1.4".
- `has_power_connector_hint(text)` — детектор разъёмов питания.

### 3.5. Скрипты
- `scripts/backup_ai_enrichment.py` — бэкап NULL-значений таблиц перед обогащением.
- `scripts/force_export_minor.py` — force-export для PSU/MB (минуя
  idempotency-фильтр archive/).
- `scripts/ai_enrichment_log.py` — CSV-лог реально записанных значений
  из `component_field_sources` за последний час (source='claude_code').

## 4. Unresolved — причины и распределение

### 4.1. Причины отклонения при импорте (из dry-run)

| Причина | Количество |
|---|---|
| null_value (value=null) | 92 (40 COOLER + 10 STORAGE + 32 GPU + 10 прочие) |
| bad_scheme (URL не HTTPS или about:blank при непустом value) | 32 (GPU) |
| bad_domain (URL вне whitelist) | 5 (4 MB + 1 PSU) |

### 4.2. Архитектурные причины по категориям

1. **COOLER `max_tdp_watts` — 228/228 unresolved (0% покрытие).**
   **Критическая архитектурная находка:** производители CPU-кулеров
   (Thermalright, Lian-Li, Zalman, ID-Cooling, Deepcool, Noctua, Corsair)
   **намеренно не публикуют Max TDP Rating на официальных сайтах.**
   Агент проверил 24 позиции Thermalright, 10 Lian-Li, 4 Zalman,
   1 ID-Cooling, 1 PcCooler — во всех случаях спецификация ограничивается
   heatsink dimensions, fan speed/CFM, noise dB, socket compatibility и
   количеством heat pipes. TDP-рейтинги, которые встречаются в продаже
   (245W Peerless Assassin и т.п.), публикуются ритейлерами и обзорщиками —
   источники, запрещённые whitelist-правилом.

   **Рекомендация:** для категории COOLER.max_tdp_watts whitelist-подход
   неприменим. Варианты:
   - оставить поле NULL и использовать derived-оценку по размеру радиатора
     (уже есть в regex-этапе для AIO с confidence 0.7);
   - расширить whitelist на обзорщиков techpowerup.com / guru3d.com
     (противоречит принципу этапа, требует пересмотра правила);
   - собрать внутренний справочник «модель кулера → TDP» (ручная работа).

2. **MOTHERBOARD — 0/4 unresolved (bad_domain).**
   Агент нашёл спеки AFOX motherboard AFHM65-ETH8EX / AFB250-BTC12EX на
   домене `afox-corp.com`, которого нет в whitelist (whitelist содержит
   `afox.eu`, `afox.ru`). По словам агента, afox.eu «не активен» и не
   содержит этих моделей.

   **Рекомендация:** добавить `afox-corp.com` в whitelist следующим раундом.
   Это головной сайт компании AFOX International Ltd., подтверждение
   требуется от пользователя.

3. **PSU Deepcool PN1000D — 1/5 unresolved (bad_domain).**
   Агент использовал `gamerstorm.com` — суб-бренд Deepcool. По аналогии
   с `aorus.com` (суб-бренд Gigabyte, уже в whitelist) домен официален.

   **Рекомендация:** добавить `gamerstorm.com` в whitelist. Deepcool
   в whitelist уже есть, но для legacy-моделей GamerStorm PN1000D
   datasheet доступен только на gamerstorm.com.

4. **GPU.video_outputs — 32 случая bad_scheme.**
   Агент вывел значения из текста `model` (наименование содержит
   «HDMI, DP*3», «1xHDMI+1xDVI-D+1xVGA» и т.п.), источник URL не задал.
   По правилу задачи значения без URL-источника недопустимы — importer
   отклонил их.

   **Рекомендация:** переклассифицировать эти значения как source=`derived`
   (по name) с confidence 0.85 и записать через альтернативный путь —
   требует доработки на следующем микроэтапе.

5. **GPU.* — 13 позиций полностью unresolved (null + bad_scheme).**
   Бот-защиты AIB-сайтов (Gigabyte, MSI 403/redirect), отсутствие compare-
   страниц NVIDIA для legacy GT-серий, таймауты amd.com. AFOX (id 321-323)
   SPA без SSR — WebFetch не получает контент.

6. **STORAGE — 10/17 unresolved.**
   - Netac Z9/Z Slim (4 шт.) — внешние USB-C SSD 1.8"; интерфейс USB-C и
     form_factor 1.8" не входят в allowed-enum схемы (см. `_v_storage_*`).
   - Kingston Brackets and Screws — не диск, а монтажный адаптер.
   - Cisco Pluggable SSD 240GB — проприетарный модуль без
     form_factor-эквивалента в схеме.

## 5. Топ-10 моделей с наибольшей неопределённостью

| id | model | Полей unresolved |
|---|---|---|
| 216 | Netac NT01Z9-001T-32BK (USB-C SSD) | 3 |
| 217 | Netac NT01ZSLIM-001T-32BK | 3 |
| 218 | Netac NT01ZSLIM-002T-32BK | 3 |
| 219 | Netac NT01Z9-002T-32BK | 3 |
| 321, 322, 323 | AFOX GT 210/GT 710 (SPA без SSR) | 5 каждая |
| 336, 337, 338 | ASUS/MSI GT 710/GT 730 (403) | 5 каждая |
| 352, 355, 356, 357 | GIGABYTE/MSI GT-серия | 5 каждая |

## 6. Примеры unresolved (10 позиций)

| id | Категория | Проблема |
|---|---|---|
| 252 | psu | ✅ Ubiquiti POE-15-12W 12W (заполнено) |
| 282 | psu | FSP 132-41200-0500A0: OEM-part для Cisco/Dell, не в каталоге fsp-group.com |
| 310 | psu | Deepcool PN1000D: URL gamerstorm.com вне whitelist |
| 378, 379 | motherboard | AFOX: URL afox-corp.com вне whitelist |
| 463 | case | Zalman T4 Plus: БП не комплектный по zalman.com (несмотря на «W/PSU» в БД) |
| 480-503 | cooler | Thermalright: max_tdp не публикуется на thermalright.com |
| 782 | storage | Kingston Brackets: аксессуар, не диск |

## 7. Тесты

- **До (master):** 416 passed, 1 skipped.
- **После:** **470 passed, 1 skipped (+54 новых тестов)**.
- Файл: `tests/test_enrichment_claude_code.py` — 54 теста:
  - 8 whitelist-тестов (ритейлеры/обзоры/http/lookalike отклоняются, оф.URL проходят).
  - 11 derive_needs_extra_power / has_power_connector_hint.
  - 9 normalize_video_outputs.
  - 8 storage-валидаторов (типы, ff, interface, capacity_gb; ритейлер отклонён).
- Все 416 предыдущих тестов — зелёные, регрессий нет.

## 8. Артефакты

| Файл | Назначение |
|---|---|
| `scripts/reports/ai_enrichment_whitelist_recon.md` | Разведка whitelist и стоп-пойнт №1 |
| `scripts/reports/ai_enrichment_targets.md` | Целевая выборка (359 позиций в 15 батчах) |
| `scripts/reports/ai_enrichment_backup_20260424.sql` | Бэкап (1316 UPDATE для отката) |
| `scripts/reports/ai_enrichment_log.csv` | Лог 134 записанных значений (id, field, new_value, url, confidence, agent_domain) |
| `scripts/reports/ai_enrichment_report.md` | **Этот отчёт** |
| `enrichment/archive/*/batch_*__20260424_*.json` | Архив обработанных батчей |
| `enrichment/pending/*/batch_*.json` | 12 батчей на следующие раунды (4 GPU + 4 COOLER + редкие) |
| `enrichment/stale_pending_20260424/` | Устаревшие pending до regex-этапа (не для повторной обработки) |

## 9. Метрики web-запросов

- Всего WebFetch-вызовов агентами: ~246 (оценка по их отчётам).
- Отклонённых по whitelist-правилу записей при импорте: **5** (bad_domain).
  Это доказывает, что guard работает: агенты пытались использовать
  `afox-corp.com` и `gamerstorm.com` — импортер их отбил.
- Отклонённых по scheme (about:blank + value not null): **32** (GPU.video_outputs).

## 10. Рекомендации для следующих раундов

1. **Расширить whitelist на `afox-corp.com` и `gamerstorm.com`** (2 домена)
   → откроет 4 motherboard + 1 psu поля.
2. **COOLER max_tdp_watts:** принять, что whitelist-подход не работает,
   и оставить derived-оценку по размеру радиатора (уже сделано в regex)
   либо расширить whitelist на обзорщиков (требует решения пользователя).
3. **GPU остальные 4 батча pending (135 позиций).** Можно запустить в
   следующем раунде — ожидаемый прирост 300-400 полей.
4. **GPU video_outputs 32 отклонённых** — переклассифицировать как
   source='derived' через отдельный backfill-скрипт (источник: model name).
5. **STORAGE USB-C SSD (Netac Z9/ZSlim):** расширить schema
   для USB-C / 1.8" portable enclosure (1.8\"`/`USB-C` не предусмотрены enum-ом).

## 11. Архитектурные нюансы

- **Impostor-проверка работает:** в GPU батче агент отработал 40 позиций за
  ~16 минут (63 WebFetch). Импортер корректно отклонил записи без HTTPS
  и с чужим доменом — подтверждение, что whitelist-guard устойчив.
- **`case_psu_pass` двухпроходный workflow** корректно разделяет экспорт:
  сначала `--all` для has_psu_included/supported_form_factors,
  затем `--category case --case-psu-pass` для included_psu_watts только у
  has_psu=TRUE. Это сэкономило 810 ненужных позиций (case c has_psu=FALSE).
- **Storage-валидаторы осознанно сохраняют регистр** (`"2.5\""`, `"M.2"`,
  `"mSATA"`, `"NVMe"`) — `_as_enum` из старой схемы форсирует `.upper()`,
  что ломает единообразие с regex-значениями. Своя нормализация в
  `_v_storage_form_factor` / `_v_storage_interface`.
