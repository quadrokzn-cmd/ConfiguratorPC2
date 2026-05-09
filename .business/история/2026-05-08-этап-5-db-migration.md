# 2026-05-08 — Этап 5 (из 9): перенос аукционных таблиц в БД `kvadro_tech`

## 1. Какая задача была поставлена

После Этапа 4 (унификация `price_loaders/`) — Этап 5: перенести из БД `quadrotech` (заморожена с Этапа 1) в БД `kvadro_tech` (продакшен C-PC2) 8 аукционно-специфичных таблиц без конфликтов с существующими C-PC2-таблицами. После этапа в `kvadro_tech` должны лежать 162 лота / 438 позиций / 187 primary-матчей / KTRU-watchlist / settings / excluded_regions.

DoD:
- 8 таблиц аукционов созданы в `kvadro_tech` через единую миграцию `030_auctions_tables.sql`.
- Все счётчики совпадают со snapshot Этапа 1.
- Миграция идемпотентна (повторный прогон = NOOP).
- Эталоны KPI: `tender_items.nmck_per_unit IS NOT NULL=438`, `matches.primary=187`, `ktru_watchlist.is_active=TRUE=2`.
- C-PC2-таблицы не изменены.
- Pytest C-PC2 зелёный.
- План + рефлексия обновлены.

Рамка: не дропать QT БД, не трогать C-PC2-таблицы, не подключать `auctions_staging/`-код, не переносить `nomenclature` / `supplier_prices` / `users` QT (это Этап 6+).

## 2. Как я её решал

Линейно, по DoD:

1. **Прочитал контекст:** CLAUDE.md, MEMORY.md, рефлексии Этапов 1-4 (особенно `2026-05-08-этап-1-backup-freeze.md` — там snapshot цифр и `db-snapshot.md`), все 9 staging-миграций `auctions_staging/migrations/0001..0009_*.sql`, существующие `migrations/001..029` (стиль нумерации, формат комментариев), `scripts/apply_migrations.py` (логика журнала `schema_migrations`).

2. **Verify конфликтов имён.** `psql -d kvadro_tech -c "SELECT … WHERE table_name IN ('tenders','tender_items',…)"` → пустой результат. Конфликтов нет. Имя `settings` использовать можно без переименования. Список существующих C-PC2-таблиц (23): `api_usage_log, audit_log, cases, component_field_sources, coolers, cpus, daily_budget_log, exchange_rates, gpus, motherboards, price_uploads, projects, psus, queries, rams, schema_migrations, sent_emails, specification_items, storages, supplier_prices, suppliers, unmapped_supplier_items, users` — пересечений с 8 целевыми именами нет.

3. **Создал `migrations/030_auctions_tables.sql`** — объединяющий идемпотентный скрипт. Объединил DDL из 5 staging-миграций (`0002_catalog`, `0003_tenders`, `0004_matches`, `0005_statuses`, `0006_settings`, `0008/0009_ktru_watchlist`). Структура:
   - 1) `ktru_catalog` (DDL из 0002).
   - 2) `ktru_watchlist` (DDL из 0002 + ALTER ADD COLUMN `display_name` из 0009 + seed 4 базовых кодов из 0002 + seed 6 расширенных из 0008 + UPDATE `is_active=FALSE` для 8 узких + UPDATE `display_name` для 2 зонтиков).
   - 3) `excluded_regions` (DDL из 0006 + seed 7 регионов).
   - 4) `settings` (DDL из 0006 + seed 5 параметров).
   - 5) `tenders` (DDL из 0003 + 3 индекса).
   - 6) `tender_items` (DDL из 0003 + индекс `idx_tender_items_ktru`).
   - 7) `tender_status` (DDL из 0005 + индекс).
   - 8) `matches` (DDL из 0004 + 3 индекса).
   - **`matches.nomenclature_id` оставлен `BIGINT NOT NULL` БЕЗ FK** — на Этапе 6 подключим к `printers_mfu(id)`. Записал это явным комментарием в миграции и в `Что НЕ переносит` шапки.

4. **Применил миграцию через `python -m scripts.apply_migrations`.** Раннер обнаружил, что 028 и 029 (Этап 4 не накатывал на БД, только файлы) ещё не применены к `kvadro_tech` — в `schema_migrations` было 27 записей, но `auto_price_loads` / `auto_price_load_runs` физически не существовали. Раннер за один прогон применил 028 + 029 + 030. Это закрыло технический долг Этапа 4 как побочный эффект — отдельной задачи не было, но и оставлять «гнилой» апдейт схемы было нельзя (apply_migrations при первом же старте всё равно их применил бы). Verify: `psql … "\dt"` → 8 новых таблиц + auto_price_loads + auto_price_load_runs появились, в `schema_migrations` запись `030_auctions_tables.sql` есть.

5. **Перенос данных через `pg_dump --data-only` → `psql -f`.** Стратегия — дампы в файлы (а не пайп), чтобы видеть размеры/ошибки. Создал `.business/_backups_2026-05-08-merge/stage5-data-transfer/`, выгрузил 7 таблиц (без `ktru_watchlist` — миграция 030 уже создала seed). Перед `psql -f` для `settings` и `excluded_regions` — `TRUNCATE` (миграция 030 наполнила их seed-ом, а COPY не уживётся с дубликатами PK). Порядок применения соблюдает FK-целостность: `tenders` → `tender_items` → `tender_status` → `matches`. Используется `-v ON_ERROR_STOP=1`, чтобы любой косяк остановил процесс.

6. **Verify счётчиков.** Прогнал тот же набор `count(*)`-запросов, что в snapshot Этапа 1 — все 12 совпали. Дополнительные контрольные срезы (`tenders_with_primary=144`, `items_with_required_attrs=198`) тоже совпали с snapshot.

7. **Verify идемпотентности.** Прогнал `psql -f migrations/030_auctions_tables.sql` второй раз вручную: NOTICE'ы `relation "X" already exists, skipping`, `INSERT 0 0`, никаких ALTER изменений (display_name уже добавлен). Счётчики после повторного прогона тождественны.

8. **Verify неизменности C-PC2.** Счётчики ключевых C-PC2-таблиц (`users=2, suppliers=6, supplier_prices=13010, price_uploads=9, cpus=228, projects=18, specification_items=13`) живы и не изменены — миграция 030 их не трогала.

9. **Pytest C-PC2.** `python -m pytest --tb=short -q` → **1439 passed, 2 skipped (live), 0 failed**, ~76 секунд. Тестовый `conftest.py` не включает миграцию 030 в свой список `_MIGRATIONS` (там только 001-023 + 028 + 029 — миграции конфигуратора), и таблиц аукционов в `_ALL_TABLES` нет — поэтому тестовая БД не трогается. Регрессий нет.

10. **Обновил план + написал эту рефлексию.** В итоговый блок `plans/2026-04-23-platforma-i-aukciony.md` добавлен буллет «Этап 5/9 завершён 2026-05-08…» (вставлен ПЕРЕД буллетом Этапа 4, в обратно-хронологическом порядке — таком же, как в существующих этапах).

## 3. Решил ли — да / нет / частично

**Да, полностью.** Все DoD из ТЗ выполнены:

| DoD                                                                  | Статус |
|----------------------------------------------------------------------|:------:|
| 8 таблиц аукционов созданы в `kvadro_tech`                           |   ✅   |
| Все счётчики совпадают со snapshot Этапа 1 (12 метрик)               |   ✅   |
| Миграция идемпотентна (повторный прогон = NOOP)                      |   ✅   |
| `tender_items.nmck_per_unit IS NOT NULL = 438`                       |   ✅   |
| `matches.match_type='primary' = 187`                                 |   ✅   |
| `ktru_watchlist.is_active = TRUE = 2`                                |   ✅   |
| C-PC2-таблицы не изменены                                            |   ✅   |
| Pytest C-PC2 зелёный (1439 passed)                                   |   ✅   |
| Конфликтов имён нет — `settings` без переименования                  |   ✅   |
| План + рефлексия обновлены                                           |   ✅   |
| QT БД не дропнута, остаётся fall-back                                |   ✅   |
| Код `auctions_staging/` не подключён к C-PC2-приложениям             |   ✅   |
| `nomenclature` / `supplier_prices` QT не перенесены (это Этап 6)     |   ✅   |

**Расхождение с цифрами в задании.** В промте было `matches=11691, alternative=11504`. По snapshot Этапа 1 (источник истины): `matches=13575, alternative=13388, primary=187`. Сверял с фактом snapshot — все 12 метрик совпали. В рефлексии и плане использую корректные числа из snapshot, а не из промта.

## 4. Эффективно ли решение, что можно было лучше

**Что получилось хорошо:**

- **Один SQL-файл вместо переноса 5 миграций.** Объединяющая миграция `030_auctions_tables.sql` пишет всё в правильном порядке (CREATE → seed → ALTER → UPDATE для display_name), идемпотентна, и читается как единый «эпизод» истории C-PC2-репо. Альтернатива — копировать `0002..0009` как `030..035` — захламила бы каталог и оставила бы чужой стиль (BIGSERIAL, TIMESTAMPTZ NOT NULL DEFAULT now() — отличается от C-PC2-овского SERIAL/TIMESTAMP DEFAULT NOW()). Решил пожертвовать «исторической точностью» 5 файлов в пользу одной осмысленной миграции, тождественной по результату.

- **Дампы через файлы, а не пайп `pg_dump | psql`.** Под Windows + git-bash пайп сжатого pg_dump на больших объёмах теоретически может ловить буферизацию, и при ошибке непонятно, на каком байте всё рухнуло. Файловой стратегией я: 1) видел размер каждого дампа сразу (`tenders.sql=43 МБ` ← это 162 лота с raw_html, ожидаемо), 2) мог пере-применить отдельную таблицу, если что-то пойдёт не так, 3) оставил аудит-копию в `.business/_backups_2026-05-08-merge/stage5-data-transfer/`. Дополнительная цена — ~58 МБ на диске, ничего страшного.

- **TRUNCATE перед COPY на seed-таблицах.** Миграция 030 создаёт seed для `settings`, `excluded_regions`, `ktru_watchlist`. Если бы я применил `pg_dump --data-only` поверх этого seed-а, COPY упал бы на дубликатах PK (`settings.key='margin_threshold_pct'` есть и в seed, и в дампе). TRUNCATE настоял на «прод данные QT > seed миграции» — `updated_at` берутся из QT-овских ручных правок, а не из времени применения миграции. Для `ktru_watchlist` — обратное решение: переносить _не стал_, потому что seed миграции уже корректен (после ALTER+UPDATE), и TRUNCATE+COPY даст ровно то же самое + сложнее.

- **Сразу обнаружил тех. долг 028/029 и не стал прятать.** Этап 4 явно сказал «не трогать БД», но миграции в файлах остались — значит, при первом же `apply_migrations.py` в любом следующем чате они применятся. Я не пытался обойти это (`--skip 028 --skip 029`-флага в раннере нет, и это правильно — раннер должен быть идемпотентным от номера). Принял как факт, прогнал, прописал в плане и рефлексии.

- **Verify через 12 контрольных счётчиков, а не «таблицы созданы и хорошо».** В snapshot Этапа 1 были не только табличные count, но и `WHERE`-срезы (nmck_per_unit, primary/alternative, required_attrs, tenders_with_primary). Все 12 пар «snapshot ↔ live» совпали — это сильнее, чем просто «8 таблиц есть в `\dt`».

**Что можно было лучше:**

- **`apply_migrations.py` без `--target` или `--dry-run`.** Раннер применяет всё, что не в журнале. Если бы тех. долг 028/029 ломал что-то конкретное на проде (его нет, но представим) — у меня нет способа сказать «применить только 030, не трогая 028/029». Не блокер этого этапа, но если на каком-то этапе понадобится частичный rollout — придётся допиливать раннер. Записал это как открытый вопрос для постановки задач в Этапах 6-8.

- **Не сделал верификации FK-целостности после переноса.** Технически: я просто проверил счётчики, но не проверил, что для каждой `tender_items.tender_id` есть запись в `tenders.reg_number`, и что для каждой `matches.tender_item_id` есть запись в `tender_items.id`. На практике — `pg_dump --data-only` восстанавливает ровно то, что было в QT, FK были живые в QT (этап 1 verify), порядок переноса соблюдает FK. Орфаны быть не могли. Но строгий verify через `LEFT JOIN … WHERE … IS NULL` стоило бы сделать ради дисциплины. Запас прочности — на Этапе 6 это всплывёт сразу, если что-то не так.

- **Тестовый conftest.py не включает миграцию 030.** Это не «лучше/хуже» — это правильное поведение для тестов конфигуратора: им не нужны аукционные таблицы. Но когда на Этапе 6+ начнётся подключение auctions-кода к C-PC2-приложениям, тестам придётся добавлять 030 в свой `_MIGRATIONS`. Это будет частью Этапов 6-8, не моя проблема сейчас.

- **Не убрал `auctions_staging/migrations/0001..0009_*.sql` после переноса.** Они теперь дубликаты `030_auctions_tables.sql` (плюс `0001_init` про users и `0007_attrs_source` про nomenclature, которые не переносим). Их удаление — задача Этапа 9 (cleanup `auctions_staging/`), а не сейчас. Сейчас они полезны как audit-trail происхождения миграции 030.

## 5. Как было и как стало

**Было:**
- БД `kvadro_tech` (C-PC2 продакшен) — 23 таблицы, в `schema_migrations` 27 записей. Аукционных таблиц нет. Миграции 028 и 029 (auto_price_loads infrastructure) — есть в файлах, но не применены к БД.
- БД `quadrotech` (заморожена с Этапа 1) — 14 таблиц, единственное место, где жили данные аукционного модуля (162 лота, 438 позиций, 13 575 матчей).
- Каталог `auctions_staging/migrations/` содержит 9 staging-миграций со схемой, отличной от C-PC2-стиля (BIGSERIAL vs SERIAL, TIMESTAMPTZ vs TIMESTAMP).
- Чтобы запустить аукционный модуль — пришлось бы либо подключаться к двум БД, либо снова возиться с миграцией.

**Стало:**
- БД `kvadro_tech` теперь содержит **31 таблицу** (23 C-PC2 + 8 аукционных). Все аукционные данные — на месте, счётчики совпадают со snapshot Этапа 1.
- В `schema_migrations` — **30 записей** (027 + 028 + 029 + 030, в правильном порядке).
- Файл `migrations/030_auctions_tables.sql` — единственная авторитетная миграция аукционных таблиц C-PC2-репо. Идемпотентна.
- БД `quadrotech` — нетронута, остаётся fall-back до конца Этапа 9.
- Аудит-копия дампов — `.business/_backups_2026-05-08-merge/stage5-data-transfer/` (7 .sql, ~58 МБ).
- Pytest C-PC2 зелёный (1439 passed, 2 skipped); регрессий нет.
- Этапы 6-9 (перенос `nomenclature`/`supplier_prices` QT → `printers_mfu`, подключение кода аукционов, FK на `printers_mfu`, cleanup `auctions_staging/`) разблокированы — у них теперь есть «таблицы для записи» в проде.

---

## Артефакты этапа 5

| Файл / каталог                                                                | Описание                                  | Размер     |
|-------------------------------------------------------------------------------|-------------------------------------------|-----------:|
| `migrations/030_auctions_tables.sql`                                          | Объединяющая идемпотентная миграция       | ~7 КБ      |
| `.business/_backups_2026-05-08-merge/stage5-data-transfer/settings.sql`       | Дамп `settings` (data-only)               | 1.1 КБ     |
| `.business/_backups_2026-05-08-merge/stage5-data-transfer/excluded_regions.sql` | Дамп `excluded_regions`                  | 1.8 КБ     |
| `.business/_backups_2026-05-08-merge/stage5-data-transfer/ktru_catalog.sql`   | Дамп `ktru_catalog` (пустая)              | 0.9 КБ     |
| `.business/_backups_2026-05-08-merge/stage5-data-transfer/tenders.sql`        | Дамп `tenders` (162 лота, raw_html)       | 43 МБ      |
| `.business/_backups_2026-05-08-merge/stage5-data-transfer/tender_items.sql`   | Дамп `tender_items` (438 позиций)         | 213 КБ     |
| `.business/_backups_2026-05-08-merge/stage5-data-transfer/tender_status.sql`  | Дамп `tender_status` (162 строки)         | 13 КБ      |
| `.business/_backups_2026-05-08-merge/stage5-data-transfer/matches.sql`        | Дамп `matches` (13 575 матчей)            | 15 МБ      |
| Правка `plans/2026-04-23-platforma-i-aukciony.md`                             | Буллет «Этап 5/9 завершён 2026-05-08»     | ~3.5 КБ    |
| `.business/история/2026-05-08-этап-5-db-migration.md`                          | Эта рефлексия                             | ~10 КБ     |

---

## Контрольный verify (snapshot ↔ live `kvadro_tech` после Этапа 5)

| Метрика                                                  | Snapshot QT | live `kvadro_tech` | OK |
|----------------------------------------------------------|------------:|-------------------:|:--:|
| `tenders`                                                |         162 |                162 | ✅ |
| `tender_items`                                           |         438 |                438 | ✅ |
| `tender_status`                                          |         162 |                162 | ✅ |
| `matches`                                                |      13 575 |             13 575 | ✅ |
| `matches WHERE match_type='primary'`                     |         187 |                187 | ✅ |
| `matches WHERE match_type='alternative'`                 |      13 388 |             13 388 | ✅ |
| `tender_items WHERE nmck_per_unit IS NOT NULL`           |         438 |                438 | ✅ |
| `ktru_catalog`                                           |           0 |                  0 | ✅ |
| `ktru_watchlist`                                         |          10 |                 10 | ✅ |
| `ktru_watchlist WHERE is_active=TRUE`                    |           2 |                  2 | ✅ |
| `excluded_regions`                                       |           7 |                  7 | ✅ |
| `settings`                                               |           5 |                  5 | ✅ |
| `tender_items WHERE required_attrs_jsonb непустой`       |         198 |                198 | ✅ |
| `COUNT DISTINCT tender_id` через `matches JOIN items`    |         144 |                144 | ✅ |

Все 14 контрольных пар совпали. Перенос данных Этапа 5 валиден.
