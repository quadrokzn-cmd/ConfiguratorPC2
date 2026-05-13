-- =============================================================
-- Migration 0038: backfill supplier_prices.category для MFU-позиций.
--                 Мини-этап 2026-05-13 «Excel-каталог печатной техники:
--                 пустые цены на листе «МФУ» из-за неоднозначного
--                 backward-lookup'а table → category».
--
-- Контекст. На Этапе 6 слияния (2026-05-08) в CATEGORY_TO_TABLE
-- появились две категории, указывающие на одну таблицу:
--     "printer": "printers_mfu",
--     "mfu":     "printers_mfu".
-- Orchestrator (`portal/services/configurator/price_loaders/orchestrator.py`)
-- при записи в supplier_prices вычислял category через помощник
-- `_category_of_component(table)`, который шёл по dict в insertion-
-- порядке и для table='printers_mfu' всегда возвращал ПЕРВУЮ
-- совпадающую категорию — 'printer'. В итоге все MFU-строки от
-- OCS и Merlion (439 шт. из 615) попадали в supplier_prices с
-- category='printer', хотя в каталоге `printers_mfu` они правильно
-- помечены как 'mfu'.
--
-- Симптом: `excel_export.export_printers_mfu` тянул min-цены для листа
-- «МФУ» строго по `WHERE sp.category = 'mfu'` — и не находил ни одной
-- активной строки (0/360 MFU SKU с ценой). Лист «Принтеры» работал
-- частично (правильные 'printer'-строки совпадали).
--
-- Фикс кода — параллельно в orchestrator.py (`category = row.our_category`).
-- Эта миграция чинит уже накопленные данные.
--
-- Что делает миграция:
--   UPDATE supplier_prices SET category='mfu' для строк, где
--   совпадающая запись в printers_mfu имеет category='mfu', а в
--   supplier_prices ошибочно записано 'printer'. Конфликта по
--   UNIQUE (supplier_id, category, component_id) не будет —
--   дублей 'mfu'-строк рядом с 'printer'-строками на момент
--   написания миграции нет (проверено эмпирически на prod).
--
-- Идемпотентно: повторный накат NOOP (после первого UPDATE'а
-- условие category='printer' больше не выполнится для MFU-строк).
-- =============================================================

UPDATE supplier_prices sp
   SET category = 'mfu'
  FROM printers_mfu pm
 WHERE pm.id = sp.component_id
   AND pm.category = 'mfu'
   AND sp.category = 'printer';
