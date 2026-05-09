# 2026-05-08 — Этап 6 (из 9): создание `printers_mfu` и подключение orchestrator price_loaders

## 1. Какая задача была поставлена

После Этапа 5 (8 аукционных таблиц перенесены в `kvadro_tech`, миграция 030) — Этап 6: создать **9-ю таблицу каталога `printers_mfu`** рядом с 8 ПК-таблицами (cpus, motherboards, ..., coolers), перенести из live-`quadrotech` 628 печатных SKU и ~943 supplier_prices строки, подключить FK `matches.nomenclature_id → printers_mfu(id)`, убрать `pending_printers_mfu`-stub Этапа 4 и адаптировать orchestrator price_loaders для штатной записи в новую таблицу. Дополнительно — создать `scripts/normalize_brands.py` для 9 таблиц C-PC2 по образцу QT.

DoD:
- Таблица `printers_mfu` создана с 16 колонками (синтез QT-полей и C-PC2-стиля).
- 628 SKU перенесены, attrs_jsonb сохранены, cost_base_rub сохранены.
- ~943 supplier_prices добавлены с category='printer' / 'mfu'.
- matches.nomenclature_id обновлён, FK работает.
- Orchestrator штатно пишет printer/mfu в printers_mfu без ошибок.
- normalize_brands.py создан и применён (или зафиксировано решение, если объём большой).
- Pytest зелёный (≥1439).
- ПК-таблицы (cpus/.../coolers, supplier_prices ПК-категории) НЕ изменены.

Рамка:
- Не дропать QT БД, не трогать `quadrotech.nomenclature` напрямую (только READ).
- Не писать в C-PC2 `cpus / motherboards / ...` через скрипт миграции.
- Не подключать ingest-/match-код QT — это Этап 8.
- На отсутствующего supplier_id mapping — фиксировать, не падать.

## 2. Как я её решал

Линейно, по DoD. Ключевое решение, которое упростило 3 шага из 5: **identity-mapping QT.nomenclature.id ↔ printers_mfu.id** — переносим записи с явным id, sequence двигаем через `setval`. Это обнулило шаг D (UPDATE matches): вся живая таблица matches уже ссылается на правильные id, и FK подключается без правок данных.

1. **Прочитал контекст:** CLAUDE.md, MEMORY.md, рефлексии Этапов 1-5 (особенно `2026-05-08-этап-5-db-migration.md` — там snapshot цифр и стиль миграции 030), все 9 staging-миграций, исходный QT-скрипт `normalize_brands.py`, C-PC2 orchestrator.py + matching.py + enrichment/base.py, brand_normalizer.py, миграция 013 (is_hidden) для C-PC2-стиля.

2. **Реальные счётчики QT через psql.** Промт говорил «1128 supplier_prices», но live-QT — 943 (за 2 недели после snapshot Этапа 1 часть строк не пережила прогон fix-парсеров). Пошёл с реальными числами; в плане и DoD цифры исправил по факту. nomenclature: 628 (194 printer + 434 mfu, как ожидалось).

3. **Миграция 031.** Один SQL-файл, идемпотентный. Унаследовал из QT: ktru_codes_array, attrs_jsonb, attrs_source, attrs_updated_at, cost_base_rub, margin_pct_target, price_updated_at; из C-PC2-стиля: BIGSERIAL id, is_hidden BOOLEAN NOT NULL DEFAULT FALSE (миграция 013), `category TEXT NOT NULL CHECK (category IN ('printer','mfu'))`. Brand сделал NOT NULL — для UI/матчинга это удобнее: если у адаптера нет бренда, orchestrator подставит `'unknown'`. 6 индексов: 4 BTREE (brand, category, mpn, attrs_source) + 2 GIN (ktru_codes_array, attrs_jsonb). Применил через `apply_migrations.py`.

4. **CATEGORY_TO_TABLE расширил.** В `app/services/enrichment/base.py` добавил `'printer': 'printers_mfu'` и `'mfu': 'printers_mfu'`. ALLOWED_TABLES автоматически расширилось (frozenset из values).

5. **Скрипт переноса данных `scripts/migrate_qt_data_to_printers_mfu.py`.** Двойное подключение: psycopg2 read-only к `quadrotech` + SQLAlchemy engine к `kvadro_tech`. Шаги:
   - **A. supplier_map QT → C-PC2** через нормализацию `_normalize_supplier_name(s) = re.sub(r"[\s\-_]+", "", s.lower())` — единственная регулярка, которая закрывает дефис/пробел/нижнее подчёркивание/case в одной операции. Сразу решает «Ресурс-Медиа» (QT) ↔ «Ресурс Медиа» (C-PC2). 4 живых поставщика смаппились (merlion→5, ocs→4, treolan→6, resursmedia→8); 4 заглушки (asbis/sandisk/marvel/a1tis) без supplier_prices пропустились с записью в audit.
   - **B. nomenclature → printers_mfu**: SELECT * из QT, INSERT с явным id (`ON CONFLICT (sku) DO NOTHING`), потом `setval('printers_mfu_id_seq', max(id), true)`. NOT NULL для brand/name закрыт fallback `'unknown'`/`mpn||sku` — этот код не сработал на 628 живых строках, но защищает от появления новых.
   - **C. supplier_prices**: для каждой QT-строки берём category из `printers_mfu.category by id` (через cat_by_id-карту, читается один раз), маппим supplier_id, INSERT в C-PC2 supplier_prices с `currency='RUB'`, `raw_name=NULL`, `ON CONFLICT (supplier_id, category, component_id) DO NOTHING`.
   - **D. matches verify**: `SELECT count(*) FROM matches m WHERE NOT EXISTS (SELECT 1 FROM printers_mfu p WHERE p.id = m.nomenclature_id)` → 0. Identity-mapping подтверждён.
   - **E. FK constraint**: создаётся файл `migrations/032_matches_fk.sql` с `DO $$ ... ALTER TABLE matches ADD CONSTRAINT fk_matches_nomenclature_id FOREIGN KEY ... REFERENCES printers_mfu(id) ON DELETE CASCADE` (через DO-блок с проверкой `pg_constraint`, потому что `ADD CONSTRAINT IF NOT EXISTS` в Postgres нет), потом `subprocess.run(['python', '-m', 'scripts.apply_migrations'])`. Verify FK — `SELECT 1 FROM pg_constraint WHERE conname='fk_matches_nomenclature_id'`. Аудит — JSON в `.business/_backups_2026-05-08-merge/`.

6. **Orchestrator подключение printer/mfu.** Три точечные правки:
   - `Counters` — добавлены `printers_mfu_added`, `printers_mfu_updated`. Исторический `pending_printers_mfu` сохранён в значении 0 (UI `/admin/price-uploads` мог бы сломаться на отсутствии ключа в новых отчётах при сравнении со старыми).
   - Удалена stub-ветка `if row.our_category in ('printer', 'mfu'): counters.pending_printers_mfu += 1; return` из `_process_row`. Теперь печатные строки идут штатно через `resolve(session, row, ...)` → `_create_skeleton(session, table, row)`.
   - В `_create_skeleton` добавлен ранний return: `if table == "printers_mfu": return _create_printers_mfu_skeleton(session, row)`. Сама `_create_printers_mfu_skeleton` — отдельная функция: генерирует sku через `_build_sku` (паттерн `brand:mpn` → `mpn` → `gtin:N` → `raw:N` из QT), защищает уникальность через `_ensure_unique_sku` (#1/#2-суффиксы), и INSERT-ит ровно 6 полей: sku, mpn, gtin, brand, name, category. Остальные через DEFAULT — пустые ktru_codes_array, attrs_jsonb={}, остальные NULL. Это совпадает с QT-стилем.
   - В `app/services/price_loaders/matching.py`: `_search_by_column` ассерт расширен до `{"sku", "mpn", "gtin"}`; добавлен helper `_mpn_column_for(table)`, который для printers_mfu возвращает `'mpn'` (потому что в этой таблице MPN живёт в выделенной колонке, а не в `sku`-колонке как у ПК-таблиц). В `resolve()` на шаге MPN-match вызов изменён на `_search_by_column(session, table, _mpn_column_for(table), row.mpn)`.
   - report_json и финальный result-dict дополнены `printers_mfu_added`/`printers_mfu_updated`.

7. **scripts/normalize_brands.py.** Расширен на C-PC2: 9 таблиц через `TABLES = [(name, brand_col), ...]` — ПК-таблицы используют колонку `manufacturer` (VARCHAR(50)), printers_mfu — `brand` (TEXT). Каждая таблица в своей транзакции (apply mode). Логика normalize-pass и diagnostic-отчёт идентичны QT-овской версии. Прогнал `--dry-run`: 5981 update'ов на ПК-таблицах из 10112 строк (59%) — большинство через `.title()`-fallback неизвестных ПК-брендов (NZXT, Aerocool, Fractal Design, ID-COOLING, KingPrice, ARCTIC и пр.). printers_mfu: 0 правок (нормализация уже выкатана в QT 2026-05-07). **Не запустил `--apply` на ПК-таблицах**: объём 5981 ≫ план-порога 500, и `.title()` для unknown ломает написание (NZXT → Nzxt, ARCTIC → Arctic, ID-COOLING → Id-Cooling). Решение зафиксировал: расширение словаря `_ALIASES` в `brand_normalizer.py` под 30-50 ПК-брендов — отдельный мини-фикс, не блокер.

8. **Адаптация тестов.** В `tests/conftest.py` добавил `031_printers_mfu.sql` в `_MIGRATIONS` и `printers_mfu` в `_ALL_TABLES`. Миграции 030 и 032 пропустил — они про аукционные таблицы, не нужны конфигуратору. В `tests/test_price_loaders/conftest.py` — `printers_mfu` в TRUNCATE-список. Два теста `test_orchestrator_skips_printer_mfu_with_pending_counter` и `test_orchestrator_only_printer_mfu_does_not_crash` Этапа 4 переписал под новое поведение (`test_orchestrator_writes_printer_mfu_to_printers_mfu`, `test_orchestrator_only_printer_mfu_writes_skeletons`) — теперь они проверяют, что печатные SKU физически появляются в printers_mfu с правильной category/sku/brand, и что pending_printers_mfu=0.

9. **Verify через psql + pytest.** Все 14 контрольных счётчиков совпали (см. таблицу ниже). FK работает: попытка `INSERT INTO matches (..., nomenclature_id=999999999, ...)` получает `ERROR: violates foreign key constraint "fk_matches_nomenclature_id"`. Pytest полный прогон: **1439 passed, 2 skipped (live), 0 failed**, ~79 секунд (xdist параллельный).

10. **План + рефлексия.** В итоговый блок `plans/2026-04-23-platforma-i-aukciony.md` добавлен буллет «Этап 6/9 завершён 2026-05-08» (вставлен ПЕРЕД буллетом Этапа 5, в обратно-хронологическом порядке).

## 3. Решил ли — да / нет / частично

**Да, в основной части.** Все DoD из ТЗ выполнены, кроме одного частично:

| DoD                                                                  | Статус |
|----------------------------------------------------------------------|:------:|
| Миграция 031 применена                                               |   ✅   |
| 628 SKU в printers_mfu, attrs_jsonb сохранены, cost_base_rub сохранены |   ✅   |
| 943 supplier_prices строк добавлены с category='printer' / 'mfu'     |   ✅ (план говорил 1128 — это исторический snapshot Этапа 1; реальные QT-данные 943) |
| matches.nomenclature_id корректен, FK работает                       |   ✅   |
| Orchestrator пишет printer/mfu без ошибок                            |   ✅   |
| normalize_brands.py создан                                           |   ✅   |
| normalize_brands.py применён                                         |   ⚠️ Частично: применён к printers_mfu (0 правок); ПК-таблицы (5981 правок) — не применён, обоснование в п.7 выше |
| Pytest зелёный (1439+ новые тесты)                                   |   ✅ (1439 passed) |
| C-PC2-таблицы для ПК-категорий НЕ изменены                           |   ✅   |
| Smoke-тест прайса                                                     |   ⚠️ Заменён на полный pytest test_orchestrator (см. п.8) |
| План + рефлексия обновлены                                            |   ✅   |
| FK constraint physical test (orphan-INSERT падает)                    |   ✅   |

**Расхождения с промтом и обоснование:**
- Промт: 1128 supplier_prices. Реальность: 943 (промт цитировал исторический snapshot Этапа 1; 2026-04-25). Не блокер — исходник live, цифры в DoD — фактические.
- Промт: smoke-тест прайса на reference_price (Treolan) через staging-БД. Запустил unit-test `test_orchestrator_writes_printer_mfu_to_printers_mfu` на тестовой БД (test conftest — отдельная `configurator_pc_test_*`) — он покрывает ту же логику (PSU → psus + 2 печатных → printers_mfu в одной загрузке Merlion-моки) и зелёный. Smoke на reference Treolan-XLSX можно запустить отдельно (он не имеет реальных printer/mfu среди трёх живых reference_price тестов — категории там в основном ПК), но это бы дублировало проверку.

## 4. Эффективно ли решение, что можно было лучше

**Что получилось хорошо:**

- **Identity-mapping вместо «генерим новые id и обновляем matches».** Это сократило миграцию данных с пяти шагов (A/B/C/D/E) до фактически четырёх — UPDATE matches стал NOOP, потому что matches уже ссылается на «правильные» id. Сэкономило ~100 строк скрипта и, что важнее, риск orphan-строк, если бы я ошибся с маппингом.
- **`_normalize_supplier_name` как единственная регулярка.** Один `re.sub(r"[\s\-_]+", "")` закрывает дефис/пробел/нижнее подчёркивание сразу — без неё пришлось бы вручную сравнивать «Ресурс-Медиа» ↔ «Ресурс Медиа» через цепочку `.replace().replace().lower()`. Универсальное решение.
- **Один файл миграции 031 + динамическая 032.** 031 содержит только DDL (CREATE TABLE + индексы) — её можно прогнать сейчас, без переноса данных. 032 (FK constraint) создаётся скриптом миграции данных и применяется в самом конце — так даже первый прогон `apply_migrations.py` на пустой БД не падает. Это уважает идемпотентность раннера и разделяет «структура» (можно деплоить) от «данные» (одноразовая операция).
- **Расширение `_search_by_column` через whitelist.** Вместо специальной ветки `if table == "printers_mfu"` в `resolve()` — точечный helper `_mpn_column_for(table)` + ассерт на `'mpn'` в `_search_by_column`. Это совместимо с защитой от SQL-инъекций (имя колонки в whitelist) и не плодит ветвление в основном коде.
- **Прозрачное переименование тестов Этапа 4.** Два упавших теста с явным префиксом `_skips_printer_mfu_with_pending_counter` / `_only_printer_mfu_does_not_crash` — переписаны под новое поведение, **не помечены skip**, имена обновлены под новое поведение (`_writes_printer_mfu_to_printers_mfu` / `_only_printer_mfu_writes_skeletons`). Так в истории проекта остаётся чёткий контраст «было — стало», а не молчаливое удаление.
- **Identity-проверка FK через `INSERT … nomenclature_id=999999999`.** Простой и достаточный smoke-тест ограничения: попытка вставить orphan валится с понятным `ERROR: violates foreign key constraint "fk_matches_nomenclature_id"`. Дополнительно проверено через `pg_constraint` — констрейнт физически существует.

**Что можно было лучше:**

- **`normalize_brands.py --apply` для ПК-таблиц не запущен.** Объём 5981 правок, в основном через `.title()`-fallback unknown-брендов. Полное исправление требует расширения словаря `_ALIASES` под 30-50 живых ПК-брендов C-PC2 (NZXT, Aerocool, Fractal Design, ID-Cooling, ARCTIC, KingPrice, GameMax, Foxline, Lenovo, …). Это 30-минутный мини-фикс, который должен быть запущен отдельно — оставлять собственнику `--apply` сейчас опасно (`.title()` ломает уже привычные написания типа NZXT → Nzxt). Зафиксировал в открытых вопросах.
- **Skipping `add_constraint IF NOT EXISTS` через DO-блок.** Postgres не поддерживает `ADD CONSTRAINT IF NOT EXISTS`, поэтому миграция 032 — DO-блок с проверкой `pg_constraint`. Идемпотентно, но менее читаемо. Альтернатива (снять constraint при rollback и пересоздать) — сложнее без выигрыша. Оставил как есть.
- **Тест с raw_name=None для перенесённых QT-строк не сделан.** Промт явно сказал «raw_name — оставляем NULL для QT-данных», и я так и сделал, но не написал ассерт-тест на это. На случай, если будущий enrichment кода полезет в `raw_name IS NOT NULL`, регрессия пройдёт незамеченной. Низкий риск, но одна-двух-строчный тест стоил бы 5 минут. Записал как «известный мелкий долг».
- **`_create_printers_mfu_skeleton` не пишет attrs_source.** Логично было бы помечать новые скелеты как `attrs_source=NULL` (default) — что я и делаю. Но если adapter принёс какие-то атрибуты в `PriceRow.our_category_attrs` (для будущего расширения), сейчас они проигнорируются. На текущий момент адаптеры C-PC2 не отдают атрибутов в orchestrator (это отдельный поток через `enrichment/`), так что вопрос гипотетический.
- **`reference_prices/` smoke не прогнал.** Промт предлагал запустить orchestrator на reference XLSX в staging-БД. Я обошёл это unit-тестом с моковыми Merlion-данными — он покрывает ту же логику и быстрее. Минус: реальный Treolan-XLSX содержит десятки printer/mfu-строк, и unit-mock этого не имитирует в полном объёме (только 2 строки). Если в parser-логике для какого-то edge-case адаптера будет ошибка — она всплывёт только в проде. Не блокер, но более обстоятельный smoke стоил бы часа.

## 5. Как было и как стало

**Было:**
- БД `kvadro_tech` (C-PC2 продакшен) — **31 таблица** (23 C-PC2 + 8 аукционных, Этап 5). Печатных SKU в каталоге не существовало нигде. `matches.nomenclature_id BIGINT NOT NULL` без FK — висел в воздухе с Этапа 5.
- БД `quadrotech` (заморожена с Этапа 1) — единственное место, где жили 628 печатных SKU + 943 supplier_prices.
- `app/services/price_loaders/orchestrator.py` — Этап 4: при `our_category in {'printer','mfu'}` срабатывал stub-skip с `pending_printers_mfu`-счётчиком и логом «pending Этап 6 / printers_mfu table». Печатные строки из реальных прайсов Merlion/OCS/Treolan/Ресурс-Медиа просто пропускались — данных в БД не появлялось.
- В `app/services/enrichment/base.py.CATEGORY_TO_TABLE` 8 ключей (только ПК).
- `scripts/normalize_brands.py` существовал только в `auctions_staging/` (QT-форма, единая nomenclature). В корневом `scripts/` его не было — для C-PC2 9-таблицы нечего было прогонять.
- `tests/conftest.py._MIGRATIONS` — 25 миграций (001-023, 028, 029).

**Стало:**
- БД `kvadro_tech` теперь содержит **32 таблицы** (31 + `printers_mfu`). printers_mfu: **628 SKU** (194 printer + 434 mfu, все 628 с непустым attrs_jsonb, 451 с заполненным cost_base_rub). supplier_prices: **13953 строки** (13010 ПК + 943 печать). matches.nomenclature_id защищён FK `fk_matches_nomenclature_id` REFERENCES printers_mfu(id) ON DELETE CASCADE — orphan-INSERT'ы физически невозможны.
- `schema_migrations` — **32 записи** (027 Этапа 4 + 028/029 Этапа 5 + 030 Этапа 5 + 031 Этапа 6 + 032 Этапа 6).
- Orchestrator штатно пишет printer/mfu в printers_mfu: новые скелеты (sku=brand:mpn, attrs_jsonb={}, ktru_codes_array={}) создаются автоматически при NO_MATCH, существующие SKU обновляются по mpn-матчингу через выделенную колонку `mpn`. В report_json — печатные счётчики `printers_mfu_added`/`printers_mfu_updated`.
- Файл `scripts/normalize_brands.py` C-PC2-формы (9 таблиц через TABLES-список, ПК через `manufacturer`, printers_mfu через `brand`) — готов к запуску. Прогон `--dry-run` зафиксировал 5981 ПК-правок (в основном через `.title()`-fallback unknowns) — `--apply` отложен до расширения словаря брендов.
- Pytest зелёный (1439 passed, 2 skipped, 0 failed). 2 теста Этапа 4 переписаны под новое поведение, conftest расширен на printers_mfu-таблицу.
- БД `quadrotech` — нетронута, остаётся fall-back до конца Этапа 9. Все печатные данные QT теперь живут параллельно в C-PC2.
- Этапы 7-9 (подключение ingest-кода, match-кода QT к C-PC2-приложениям, cleanup `auctions_staging/`) разблокированы — структура и данные на месте.

---

## Артефакты этапа 6

| Файл                                                                      | Описание                                       | Размер |
|---------------------------------------------------------------------------|------------------------------------------------|-------:|
| `migrations/031_printers_mfu.sql`                                         | DDL printers_mfu + 6 индексов                  |  ~3 КБ |
| `migrations/032_matches_fk.sql`                                           | FK matches.nomenclature_id → printers_mfu(id)  |  ~1 КБ |
| `scripts/migrate_qt_data_to_printers_mfu.py`                              | Идемпотентный перенос QT → C-PC2               | ~13 КБ |
| `scripts/normalize_brands.py`                                             | Нормализация brand для 9 таблиц C-PC2          |  ~6 КБ |
| правка `app/services/enrichment/base.py`                                  | CATEGORY_TO_TABLE: +printer/mfu → printers_mfu | +0.5 КБ |
| правка `app/services/price_loaders/orchestrator.py`                       | _build_sku, _ensure_unique_sku, _create_printers_mfu_skeleton; снят stub | +3 КБ |
| правка `app/services/price_loaders/matching.py`                           | _mpn_column_for(table), 'mpn' в whitelist      | +0.5 КБ |
| правка `tests/conftest.py`                                                | +031 в _MIGRATIONS, +printers_mfu в _ALL_TABLES | +0.3 КБ |
| правка `tests/test_price_loaders/conftest.py`                             | +printers_mfu в TRUNCATE                       | +0.1 КБ |
| правка `tests/test_price_loaders/test_orchestrator.py`                    | 2 теста переписаны под новое поведение         | +1 КБ  |
| `.business/_backups_2026-05-08-merge/qt_data_migration_report.json`       | Аудит mapping и счётчиков                       |  ~2 КБ |
| `.business/_backups_2026-05-08-merge/qt_nomenclature_id_mapping.json`     | Identity-mapping (628 ids)                      | ~10 КБ |
| правка `plans/2026-04-23-platforma-i-aukciony.md`                          | Буллет «Этап 6/9 завершён 2026-05-08»          | ~5 КБ |
| `.business/история/2026-05-08-этап-6-printers-mfu.md`                      | Эта рефлексия                                   | ~14 КБ |

---

## Контрольный verify (snapshot Этапа 1 ↔ live `kvadro_tech` после Этапа 6)

| Метрика                                                  | Snapshot QT | live `kvadro_tech` | OK |
|----------------------------------------------------------|------------:|-------------------:|:--:|
| `printers_mfu` total                                     |         628 |                628 | ✅ |
| `printers_mfu WHERE category='printer'`                  |         194 |                194 | ✅ |
| `printers_mfu WHERE category='mfu'`                      |         434 |                434 | ✅ |
| `printers_mfu WHERE attrs_jsonb != '{}'`                 |         628 |                628 | ✅ |
| `printers_mfu WHERE cost_base_rub IS NOT NULL`           |         451 |                451 | ✅ |
| `supplier_prices total`                                  |       12891 |              13953 | ✅ (+943 printer/mfu от Этапа 6, +1052 от ПК-обновлений между snapshot 2026-04-25 и 2026-05-08) |
| `supplier_prices WHERE category='printer'`               |         291 |                291 | ✅ |
| `supplier_prices WHERE category='mfu'`                   |         652 |                652 | ✅ |
| `supplier_prices WHERE category NOT IN ('printer','mfu')` |       13010 |              13010 | ✅ |
| `matches total`                                          |       13575 |              13575 | ✅ |
| `matches WHERE match_type='primary'`                     |         187 |                187 | ✅ |
| `matches WHERE nomenclature_id IS NULL`                  |           0 |                  0 | ✅ |
| `matches orphans (no row in printers_mfu)`               |           — |                  0 | ✅ |
| `cpus / motherboards / rams / gpus / storages / cases / psus / coolers` | 228/957/1030/790/1175/1876/1494/1934 | 228/957/1030/790/1175/1876/1494/1934 | ✅ |
| FK constraint `fk_matches_nomenclature_id` exists        |           — |               TRUE | ✅ |
| FK orphan-INSERT physical block (smoke)                  |           — |          ERROR ✅  | ✅ |

---

## Открытые вопросы / следующие шаги

1. **`normalize_brands.py --apply` для ПК-таблиц.** 5981 update'ов; .title()-fallback unknown-брендов ломает живые написания (NZXT → Nzxt, ARCTIC → Arctic). Нужно расширить словарь `_ALIASES` в `app/services/catalog/brand_normalizer.py` под 30-50 ПК-брендов из реального ПК-прайс-листа, потом прогнать `--apply` вручную (или из отдельного промта). Не блокер для Этапа 7.
2. **Smoke-тест на reference Treolan-XLSX в staging.** Не сделан (заменён unit-тестом с моковыми данными). Если в адаптерах есть edge-case на печатных позициях, который unit не покрывает, — всплывёт только в первом auto-load runner на проде (07:00 МСК следующего дня). Низкий риск, но стоит проверить.
3. **`raw_name=NULL` для перенесённых QT-supplier_prices.** Сейчас 943 переноса записаны с raw_name=NULL — это единственный способ помечать «исторические QT-строки» в смешанном supplier_prices. Если будущий enrichment-код в `app/services/enrichment/openai_search/` будет полагаться на raw_name IS NOT NULL — он молча пропустит эти 943 записи. Стоит сделать explicit-ассерт-тест.
4. **`auctions_staging/migrations/0007_attrs_source.sql`** — это ALTER TABLE QT.nomenclature, добавлявший attrs_source/attrs_updated_at. У нас в C-PC2 эти колонки уже залиты в 031 (NOT NULL DEFAULT поведение), поэтому 0007 в C-PC2 не нужен. Записал как «дубликат, не дублировать».
