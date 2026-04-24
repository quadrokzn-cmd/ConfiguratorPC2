# Этап 2.5Б — целевая выборка для AI-обогащения

**Дата:** 2026-04-24
**Источник данных:** `enrich_export.py --all` + `--case-psu-pass` + `force_export_minor.py`.

## Итоговая сводка по pending-батчам

| Категория | Батчей | Позиций | Поля к заполнению |
|---|---|---|---|
| gpu         | 5 | 175 | tdp_watts, needs_extra_power, video_outputs, core_clock_mhz, memory_clock_mhz, vram_gb, vram_type |
| cooler      | 5 | 162 | max_tdp_watts |
| storage     | 1 | 10  | storage_type, form_factor, interface, capacity_gb |
| psu         | 1 | 5   | power_watts |
| case main   | 1 | 2   | has_psu_included, supported_form_factors |
| case psu-pass | 1 | 3 | included_psu_watts |
| motherboard | 1 | 2   | memory_type, has_m2_slot |
| **Итого**   | **15** | **359** | |

Все батчи лежат в `enrichment/pending/<category>/batch_*.json`.
Старые pending (выгружены 2026-04-22 до regex-этапа) перемещены в
`enrichment/stale_pending_20260424/` чтобы не вводить в заблуждение.

## Изменения в схеме

`app/services/enrichment/claude_code/schema.py`:

1. `OFFICIAL_DOMAINS` расширен с 54 до 70 доменов (+16 новых — см.
   `ai_enrichment_whitelist_recon.md`).
2. `TARGET_FIELDS` дополнен ключом `"storage"` со списком
   `[storage_type, form_factor, interface, capacity_gb]`.
3. `ALL_CATEGORIES` дополнен `"storage"`.
4. `DEFAULT_BATCH_SIZES` дополнен `"storage": 20`.

`app/services/enrichment/claude_code/validators.py`:

5. Добавлены валидаторы `_v_storage_type`, `_v_storage_form_factor`,
   `_v_storage_interface`, `_v_storage_capacity_gb` и их регистрация в
   `_VALIDATORS`. Своя нормализация, т.к. `_as_enum` форсирует `.upper()`,
   а в БД значения хранятся с нестандартным регистром
   (`"2.5\""`, `"M.2"`, `"mSATA"`, `"NVMe"`).

## Список позиций для агентов

### Агент 1 — GPU (5 батчей, 175 позиций)

Файлы: `enrichment/pending/gpu/batch_001.json .. batch_005.json`
Целевые поля: все 7 (vram_gb и vram_type в большинстве уже заполнены regex).

### Агент 2 — COOLER (5 батчей, 162 позиций)

Файлы: `enrichment/pending/cooler/batch_001.json .. batch_005.json`
Целевое поле: `max_tdp_watts`.

### Агент 3 — MINOR (4 батча, 22 позиций)

Файлы:
- `enrichment/pending/case/batch_001.json` (2 позиции, has_psu_included + supported_form_factors)
- `enrichment/pending/case/batch_002.json` (3 позиции, included_psu_watts — psu-pass)
- `enrichment/pending/motherboard/batch_001.json` (2 позиции)
- `enrichment/pending/psu/batch_001.json` (5 позиций)
- `enrichment/pending/storage/batch_001.json` (10 позиций)

## Правила для агентов

1. Источник — только официальные сайты из whitelist (70 доменов).
2. Поле обновляется только если `to_fill` содержит его имя (текущее значение NULL).
3. Если модель не найдена на официальном сайте — значение `value: null`,
   в `source_url` записать `about:blank` (importer пропустит с причиной `null_value`).
   Пример:
   ```json
   "tdp_watts": {"value": null, "source_url": "about:blank"}
   ```
4. Максимум 2-3 web-запроса на позицию. Если не нашёл — пропустить поле.
5. Поле `needs_extra_power` для GPU — derived: TDP ≥ 75W или указан
   разъём питания → true; иначе false.

## Бэкап

- `scripts/reports/ai_enrichment_backup_20260424.sql` — 1316 строк UPDATE
  для отката NULL-состояния всех затрагиваемых полей.
