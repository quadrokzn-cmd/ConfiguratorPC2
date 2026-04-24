# Этап 2.5В — добивающий микропроход (итоговый отчёт)

**Дата:** 2026-04-24
**Продолжение этапа 2.5Б** (коммит 4909d5d). OpenAI не использовался,
бюджет не тратился. Источник — только оф.сайты из whitelist.

## Блок 1 — проверка и добавление 2 новых доменов в whitelist

| Домен | Статус | Обоснование |
|---|---|---|
| `afox-corp.com` | ✅ Добавлен | WebFetch главной + каталога (`index.php?...catid=55`) вернул полноценный список motherboard LGA1200/AM5/LGA1151, mining-раздел. Сайт активный, каталог на сайте есть. |
| `gamerstorm.com` | ✅ Добавлен | WebFetch главной + `/product/PowerSupply/`: серия PN-D активна, есть продуктовые страницы (`/product/PowerSupply/2025-03/2595_15233.shtml`). Не редирект на deepcool. |

**Whitelist:** 70 → **72 домена**.

## Блок 2 — добивание pending-батчей

Запущены 3 параллельных агента:

| Агент | Файлы | Позиций | Результат |
|---|---|---|---|
| GPU batch 2+3 | `pending/gpu/batch_002-003.json` | 80 | **380/400 полей** |
| GPU batch 4+5 | `pending/gpu/batch_004-005.json` | 55 | **0** (упёрся в rate-limit до 19:00 MSK, файлы done не записал) |
| MB+PSU+CASE retry | `pending/motherboard/`, `psu/`, `case/batch_002` | 10 | **4 MB + 1 PSU (Deepcool 1000W через gamerstorm.com)** |

### Импорт в БД (после запуска enrich_import.py --all)

| Категория | Принято | Отклонено | Null |
|---|---|---|---|
| GPU | 380 | 0 | 20 |
| MOTHERBOARD | 4 | 0 | 0 |
| CASE | 0 (уже заполнены) | 0 | 1 (Zalman T4 Plus правильно null) |
| STORAGE | 0 (done пусто) | 0 | 0 |
| PSU | 0 (уже заполнены) | 0 | 0 |
| COOLER | 0 (done пусто) | 0 | 0 |
| **Итого** | **384** | **0** | **21** |

Отдельный успех: **bad_domain снят** — AFOX motherboards и Deepcool PN1000D прошли валидацию благодаря новым доменам.

## Блок 3 — Backfill GPU.video_outputs как `derived_from_name`

**Решение:** Variant B (без миграции) — использована существующая таблица
`component_field_sources`, добавлен новый `source='derived_from_name'`
с `confidence=0.85`, `source_url=NULL`. Колонка source уже
`character varying(20)` — помещается.

**Скрипт:** `scripts/backfill_video_outputs_from_name.py` — читает
archive-файлы 2.5Б, извлекает records с `video_outputs.source_url='about:blank'`,
нормализует через `normalize_video_outputs()` и записывает в БД.

**Результат:** **31 запись** GPU.video_outputs добавлено с
`source='derived_from_name'`.

**Итоговый регистр значений колонки `source`:**

| source | Количество |
|---|---|
| regex | 2838 |
| claude_code | 519 (134 из 2.5Б + 384 из 2.5В + 1 для retry) |
| derived | 275 |
| derived_from_name | 31 |

## Блок 4 — техдолг

**Путь:** `docs/enrichment_techdebt.md`

Зафиксированы 4 пункта из ТЗ:
1. COOLER.max_tdp_watts — не публикуется на оф.сайтах (228 осталось); предложены derived + ручная разметка.
2. 4 Netac Z9/Z Slim USB-C SSD (id 216-219) — кандидаты на `is_internal=FALSE` (миграция 012 в Этапе 9).
3. Распределение оставшихся NULL GPU.tdp_watts по производителям (AFOX 46, MSI 40, ASUS 39 и т.д.).
4. Общая статистика обогащения (см. §4 ниже).

## 4. Общая статистика обогащения (2.5А + 2.5Б + 2.5В)

| Категория | Поле | Было NULL (до 2.5А) | После 2.5А | После 2.5Б | После 2.5В | % покрытия итог |
|---|---|---|---|---|---|---|
| gpu | tdp_watts | 495 | 227 | 199 | **119** | 76.0% |
| gpu | needs_extra_power | 495 | 227 | 199 | **119** | 76.0% |
| gpu | video_outputs | 495 | 238 | 229 | **130** | 73.7% (из них 31 derived_from_name) |
| gpu | core_clock_mhz | 495 | 252 | 225 | **150** | 69.7% |
| gpu | memory_clock_mhz | 495 | 228 | 200 | **123** | 75.2% |
| gpu | vram_gb | 495 | 3 | 3 | 3 | 99.4% |
| gpu | vram_type | 495 | 6 | 6 | 6 | 98.8% |
| cooler | max_tdp_watts | 716 | 228 | 228 | **228** | 68.2% (весь остаток — архитектурный) |
| case | has_psu_included | 896 | 7 | 7 | **7** | 99.2% |
| case | supported_form_factors | 896 | 60 | 60 | **58** | 93.5% |
| case | included_psu_watts (has_psu=TRUE) | 79 | 3 | 3 | **0**/3 | — |
| mb | memory_type | 658 | 2 | 2 | **0** | 100% |
| mb | has_m2_slot | 658 | 2 | 2 | **0** | 100% |
| psu | power_watts | 635 | 5 | 2 | **1** | 99.8% |
| storage | form_factor | 813 | 26 | 5 | 5 | 99.4% |
| storage | interface | 813 | 36 | 8 | 4 | 99.5% |
| storage | capacity_gb | 813 | 40 | 4 | 1 | 99.9% |

**Суммарное покрытие скелетов (позиций с ≥1 NULL-полем):**

| Этап | Скелетов | % от исходных 2207 |
|---|---|---|
| До 2.5А | 2207 | 100% |
| После 2.5А | 650 | 29.5% |
| После 2.5Б | 544 | 24.6% |
| **После 2.5В** | **≈430-445** | **≈19.5-20.2%** |

Остаток распределён: 228 cooler (архитектурно-неустранимые) + ~215 GPU (AIB-бот-защита, EOL-модели без datasheet) + 68 case (supported_form_factors нестандартных) + 11 мелочь (storage + psu + case psu-pass).

## 5. Тесты

- **До 2.5В:** 470 passed, 1 skipped.
- **После 2.5В:** **480 passed, 1 skipped** (+10 новых):
  - 4 теста на 2 новых домена whitelist (test_whitelist_contains_stage_2_5v_additions, test_new_2_5v_domains_pass × 2, test_afox_shop_subdomain_rejected_if_not_in_whitelist).
  - 6 тестов на `normalize_video_outputs` с прайсовыми образцами
    (`test_backfill_video_outputs.py`).
- Регрессий нет.

## 6. Артефакты

| Файл | Назначение |
|---|---|
| `scripts/reports/ai_enrichment_backup_25v_20260424.sql` | Бэкап (1309 UPDATE) состояния перед 2.5В-раундом |
| `scripts/reports/ai_enrichment_backup_20260424.sql` | Бэкап 2.5Б (в истории git, 1316 UPDATE) |
| `scripts/reports/ai_enrichment_log.csv` | 550 записей (384 claude_code + 31 derived_from_name из 2.5В, остальные из 2.5Б), с колонкой stage |
| `scripts/reports/ai_enrichment_25v_report.md` | **Этот отчёт** |
| `docs/enrichment_techdebt.md` | Техдолг обогащения |
| `scripts/backfill_video_outputs_from_name.py` | Скрипт backfill из archive gpu |
| `enrichment/archive/gpu/batch_002__*.json`, `batch_003__*.json` | Результаты 2.5В GPU агентов |
| `enrichment/pending/gpu/batch_004.json`, `batch_005.json` | Остались необработанными (rate-limit) |
| `enrichment/pending/cooler/*.json` | Не обрабатывались в 2.5В (архитектурно) |
| `tests/test_enrichment_claude_code.py` | +4 теста на новые домены |
| `tests/test_backfill_video_outputs.py` | +6 тестов на нормализатор |

## 7. Web-запросы и whitelist-guard

- WebFetch-вызовов: ≈100 (агенты GPU 2+3 — 72, MB+PSU retry — 26, Блок 1 — 5).
- Отклонено импортером по whitelist: **0** (в этом раунде). Принципиальный
  успех: расширение whitelist покрыло обе болевые точки из 2.5Б
  (afox-corp.com, gamerstorm.com).
- Отклонено по scheme: 0 (агенты строго следовали инструкциям).

## 8. Что не удалось в 2.5В и почему

1. **GPU batches 004+005 (55 позиций, ≈275 полей).** Агент упёрся в
   Anthropic rate-limit (7pm MSK reset). Файлы done не записаны.
   Позиции остаются в pending/, готовы для следующего запуска после сброса лимита.

2. **COOLER 162 позиции.** Пропущены по решению оркестратора-автопилота:
   установлено в 2.5Б, что max_tdp_watts не публикуется на оф.сайтах
   CPU-кулеров. Запускать агента для гарантированно-null результата —
   трата токенов. Путь добора — через derived/ручную разметку
   (см. docs/enrichment_techdebt.md §1).

## 9. Итоги трёх подэтапов (2.5А + 2.5Б + 2.5В)

- Начальные 2207 скелетов Merlion/Treolan сведены до ~440 остаточных (**79%+ покрытия**).
- Whitelist расширен с 0 (создан как 54 в 2.5Б) → 72 доменов.
- 4 механизма обогащения в проекте: `regex` / `derived` / `claude_code` / `derived_from_name`.
- Инфраструктура pending/done/archive с idempotent-импортом через
  `scripts/enrich_import.py`.
- 480 тестов (добавлено +64 за 2.5Б и +10 за 2.5В).
