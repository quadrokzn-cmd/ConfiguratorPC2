# QuadroTech — snapshot БД на момент freeze

**Дата/время снимка:** 2026-05-08 16:01:55 UTC / 2026-05-08 19:01:55 MSK.
**Postgres:** PostgreSQL 16.13, compiled by Visual C++ build 1944, 64-bit.
**Хост / БД:** `localhost:5432 / quadrotech` (доступ `postgres@localhost`, trust).
**Размер БД:** 37 MB (`pg_database_size = 39 091 223 байт`).

## Счётчики строк по всем таблицам публичной схемы

| Таблица            | count(\*) |
|--------------------|----------:|
| `users`            |         2 |
| `suppliers`        |         8 |
| `nomenclature`     |       628 |
| `supplier_prices`  |       943 |
| `price_uploads`    |         4 |
| `ktru_catalog`     |         0 |
| `ktru_watchlist`   |        10 |
| `tenders`          |       162 |
| `tender_items`     |       438 |
| `matches`          |    13 575 |
| `tender_status`    |       162 |
| `settings`         |         5 |
| `excluded_regions` |         7 |
| `_migrations`      |         9 |

(итого 14 таблиц в `public`; других схем нет).

## Контрольные срезы

| Метрика                                                                                  | Значение | Ожидание |
|------------------------------------------------------------------------------------------|---------:|---------:|
| `tender_items WHERE nmck_per_unit IS NOT NULL`                                           |      438 |      438 |
| `matches WHERE match_type='primary'`                                                     |      187 |      187 |
| `matches WHERE match_type='alternative'`                                                 |   13 388 |        — |
| `nomenclature WHERE attrs_jsonb IS NOT NULL AND attrs_jsonb != '{}'::jsonb`              |      628 |      628 |
| `tender_items WHERE required_attrs_jsonb IS NOT NULL AND required_attrs_jsonb != '{}'::jsonb` |      198 |        — |
| Тендеры с primary-матчем (`COUNT DISTINCT tender_id`)                                    |      144 |        — |

Все три KPI из ТЗ (438 / 187 / 628) на месте — БД соответствует состоянию после фикса expander-парсера 2026-05-08.

## Дамп-файлы (сделаны параллельно сразу после снимка)

| Файл                          | Размер      | Что внутри                                |
|-------------------------------|------------:|-------------------------------------------|
| `quadrotech-full.dump`        |   5 416 308 | `pg_dump -Fc` (custom format, для `pg_restore`) |
| `quadrotech-full.sql`         |  58 599 778 | `pg_dump --inserts` (plain SQL, для diff/чтения) |
| `quadrotech-schema-only.sql`  |      19 960 | `pg_dump --schema-only` (DDL без данных)  |
| `quadrotech-data-only.sql`    |  58 580 461 | `pg_dump --data-only --inserts` (данные без DDL) |
