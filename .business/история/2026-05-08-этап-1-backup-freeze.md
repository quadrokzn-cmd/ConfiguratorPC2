# 2026-05-08 — Этап 1 (из 9): backup данных QT + freeze репо

## 1. Какая задача была поставлена

Принято решение слить QuadroTech и ConfiguratorPC2 в единый проект (Вариант 1: C-PC2 главный, QT вливается). Текущий чат — этап 1 из 9-этапного плана слияния. Задачи:

- Снять полный backup БД `quadrotech` в 4 формах (custom dump, plain SQL с inserts, schema-only, data-only).
- Зафиксировать snapshot ключевых цифр (счётчики таблиц + три KPI: 438 / 187 / 628).
- Прогнать test-restore во временную БД и убедиться, что цифры совпадают.
- Поставить freeze-маркер в QT-репо, добавить отметку в итоговый блок плана.
- Сделать снимок auto-memory.
- Не трогать код, не дропать БД, не запускать ингест/матчинг.

## 2. Как я её решал

Линейно, по DoD:

1. Прочитал CLAUDE.md, MEMORY.md, итоговый блок плана `2026-04-23-platforma-i-aukciony.md`, начало discovery `2026-05-08-configurator-discovery.md` и последнюю рефлексию `2026-05-08-expander-парсер.md`.
2. Проверил наличие утилит Postgres (`pg_dump`, `pg_restore`, `psql`, `createdb`, `dropdb` — все в `C:\Program Files\PostgreSQL\16\bin`) и доступ к БД (`postgres@localhost trust`, БД `quadrotech`, 14 таблиц).
3. Создал папку `.business/_backups_2026-05-08-merge/` и подкаталог `auto-memory-snapshot/`.
4. Запустил 4 `pg_dump` фоновыми задачами, что дало параллельный прогон. Все 4 завершились с exit 0.
5. Снял snapshot: `count(*)` по 14 таблицам + 6 контрольных метрик (`nmck_per_unit`, primary/alternative матчи, `attrs_jsonb`, `required_attrs_jsonb`, тендеры с primary), плюс `version()` и `pg_database_size`. Все три KPI из ТЗ совпали (`nmck_per_unit_NOT_NULL=438`, `primary=187`, `attrs_jsonb_filled=628`).
6. Test-restore: `createdb quadrotech_restore_test` → `pg_restore -d quadrotech_restore_test quadrotech-full.dump` (ошибок не было) → перепрогнал тот же набор `count(*)` запросов в восстановленной БД → все 20 строк совпали один-в-один с продакшеном → `dropdb quadrotech_restore_test`.
7. Создал `FROZEN.md` в корне репо.
8. Добавил буллет «**FROZEN 2026-05-08:**» в итоговый блок `plans/2026-04-23-platforma-i-aukciony.md`.
9. Скопировал содержимое `C:\Users\quadr\.claude\projects\d--ProjectsClaudeCode-KVADRO-TEX\memory\` в `auto-memory-snapshot/` (7 файлов: MEMORY.md + 6 memory-записей).

## 3. Решил ли — да / нет / частично

**Да, полностью.** Все DoD из ТЗ выполнены:

| DoD                                                             | Статус |
|-----------------------------------------------------------------|:------:|
| 4 dump-файла в `.business/_backups_2026-05-08-merge/`           |   ✅   |
| `db-snapshot.md` с цифрами всех таблиц                          |   ✅   |
| Test-restore прошёл, цифры совпали                              |   ✅   |
| `FROZEN.md` в корне репо                                        |   ✅   |
| Запись в плане про freeze                                       |   ✅   |
| Auto-memory снимок скопирован                                   |   ✅   |
| Размеры дампов адекватные (full.dump ≈ 10–30 MB ожидаемо)       |   ⚠️   |
| Рефлексия написана                                              |   ✅   |

**Замечание по размерам:** custom-format `full.dump` получился **5.16 MB** (а не 10–30 MB, как ожидало ТЗ). Причины: БД небольшая (37 MB на диске), `tenders.raw_html` крупных строк не так много (162 карточки), а custom-формат внутри сжимает gzip'ом. Это **не аномалия**: plain `full.sql` весит 55.9 MB (нет сжатия) — это и есть «настоящий» объём данных. Снимок жизнеспособен — test-restore это подтвердил.

## 4. Эффективно ли решение, что можно было лучше

**Что получилось хорошо:**

- Запуск 4 `pg_dump` параллельно фоновыми задачами вместо последовательного — секунды вместо минут.
- Test-restore делал тот же блок `count(*)`-запросов, что и snapshot — `psql` сортирует UNION ALL по второму столбцу неустойчиво, но числа на месте — сверка по парам «метрика → значение» в обоих прогонах прошла однозначно. Если бы цифры расходились, временную БД оставил бы для разбора — этого не понадобилось.
- Все три KPI из ТЗ (438 / 187 / 628) совпали с состоянием после фикса expander-парсера (рефлексия 2026-05-08) — БД в консистентном состоянии, ничего не пропало после последнего чата.

**Что можно было лучше:**

- Можно было сразу включить флаг `pg_dump --quote-all-identifiers` для бóльшей переносимости (на случай разных SQL-режимов C-PC2). На этапе 5 (загрузка данных QT в C-PC2 БД) это может выстрелить — Postgres-версии совпадают (16.13 и там, и тут), но имена таблиц лучше явно квотировать. Не блокер: при необходимости пересобрать дамп — 5 секунд.
- Не сохранил `.dat` версии (binary COPY) — но они дают преимущество только на >100M строк, у нас и так быстро. Излишество для масштаба QT.
- В `db-snapshot.md` колонку «Ожидание» заполнил только для трёх KPI из ТЗ. Для остальных таблиц «ожидание» — это «текущее значение из последней рефлексии», которое вынесено в план — не дублирую.

## 5. Как было и как стало

**Было:**
- БД `quadrotech` (37 MB) — единственная live-копия данных QT. Бэкап последний раз делался в момент Волны 1Б (2026-04-25, до фикса парсеров).
- В корне репо нет маркера заморозки — внешнему чату непонятно, что проект перенесён.
- В итоговом блоке плана отметка о слиянии отсутствует.
- Auto-memory только в `~/.claude/projects/d--ProjectsClaudeCode-KVADRO-TEX/memory/` — при переезде в новый путь снимка для отката не было бы.

**Стало:**
- 4 backup-файла + snapshot цифр в `.business/_backups_2026-05-08-merge/`. Снимок жизнеспособен (test-restore верифицирован).
- `FROZEN.md` в корне репо — любой чат, открывший QT, сразу видит, что работа ведётся в C-PC2.
- Итоговый блок плана содержит буллет «FROZEN 2026-05-08» с указанием новой рабочей директории и всех ключевых ссылок.
- Auto-memory скопирована — на этапе 2 переедет в новый путь `d--ProjectsClaudeCode-ConfiguratorPC2/memory/`, снимок страхует от потери на случай отката.

---

## Артефакты этапа 1 (полный список с размерами)

| Файл                                                                | Размер, байт | Размер |
|---------------------------------------------------------------------|-------------:|-------:|
| `.business/_backups_2026-05-08-merge/quadrotech-full.dump`          |  5 416 308   |  5.16 MB |
| `.business/_backups_2026-05-08-merge/quadrotech-full.sql`           | 58 599 778   | 55.89 MB |
| `.business/_backups_2026-05-08-merge/quadrotech-schema-only.sql`    |     19 960   | 19.49 KB |
| `.business/_backups_2026-05-08-merge/quadrotech-data-only.sql`      | 58 580 461   | 55.87 MB |
| `.business/_backups_2026-05-08-merge/db-snapshot.md`                |      3 027   |  2.96 KB |
| `.business/_backups_2026-05-08-merge/auto-memory-snapshot/`         |     15 527   | 15.16 KB |
| `FROZEN.md` (корень репо)                                           |        289   |  0.28 KB |
| Правка `plans/2026-04-23-platforma-i-aukciony.md` (буллет FROZEN)   |       +400   |  +0.40 KB |

`auto-memory-snapshot/` содержит 7 файлов: `MEMORY.md` (1 416), `feedback_minimize_owner_actions.md` (2 889), `feedback_orchestrator_role.md` (2 686), `feedback_short_messages.md` (2 171), `feedback_subagent_parallelism.md` (1 880), `feedback_ui_editable_settings.md` (2 529), `user_domain_expert.md` (1 956).

---

## Snapshot цифр на момент freeze (2026-05-08 16:01:55 UTC / 19:01:55 MSK)

**Postgres:** PostgreSQL 16.13, 64-bit. **БД:** `quadrotech`, 37 MB (39 091 223 B).

### Счётчики таблиц

| Таблица            |   Prod (live) | Restore-test  | Совпадение |
|--------------------|--------------:|--------------:|:----------:|
| `users`            |             2 |             2 |     ✅     |
| `suppliers`        |             8 |             8 |     ✅     |
| `nomenclature`     |           628 |           628 |     ✅     |
| `supplier_prices`  |           943 |           943 |     ✅     |
| `price_uploads`    |             4 |             4 |     ✅     |
| `ktru_catalog`     |             0 |             0 |     ✅     |
| `ktru_watchlist`   |            10 |            10 |     ✅     |
| `tenders`          |           162 |           162 |     ✅     |
| `tender_items`     |           438 |           438 |     ✅     |
| `matches`          |        13 575 |        13 575 |     ✅     |
| `tender_status`    |           162 |           162 |     ✅     |
| `settings`         |             5 |             5 |     ✅     |
| `excluded_regions` |             7 |             7 |     ✅     |
| `_migrations`      |             9 |             9 |     ✅     |

### Контрольные метрики

| Метрика                                                                    | Prod (live) | Restore-test  | Ожидание из ТЗ | Совпадение |
|----------------------------------------------------------------------------|------------:|--------------:|---------------:|:----------:|
| `tender_items WHERE nmck_per_unit IS NOT NULL`                             |         438 |           438 |            438 |     ✅     |
| `matches WHERE match_type='primary'`                                       |         187 |           187 |            187 |     ✅     |
| `matches WHERE match_type='alternative'`                                   |      13 388 |        13 388 |              — |     ✅     |
| `nomenclature WHERE attrs_jsonb IS NOT NULL AND attrs_jsonb != '{}'::jsonb` |         628 |           628 |            628 |     ✅     |
| `tender_items WHERE required_attrs_jsonb непустой`                         |         198 |           198 |              — |     ✅     |
| Тендеров с primary (`COUNT DISTINCT tender_id` через `matches JOIN items`) |         144 |           144 |              — |     ✅     |

**Все 20 контрольных пар совпали.** Test-restore валиден. БД QT после backup — НЕ удалена и НЕ дропнута, остаётся живым источником правды до завершения этапа 5.
