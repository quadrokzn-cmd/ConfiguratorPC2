-- Миграция 0009: переход на 2 KTRU-зонтика для ингеста.
-- Решение собственника от 2026-05-07: ручная проверка на zakupki.gov.ru показала,
-- что текстовый поиск (`searchString=<KTRU-код>`) по 10 узким кодам даёт ≈3 лота
-- за 14 дней, а структурированный фильтр `ktruCodeNameList=<КОД>&&&<НАЗВАНИЕ>`
-- по 2 зонтикам (МФУ и Принтер) — ≈130 активных лотов на МФУ-зонтик.
-- Поэтому: оставляем активными только два зонтика, остальные 8 кодов выключаем.
-- Колонка display_name нужна, потому что параметр zakupki требует пары код+название.

ALTER TABLE ktru_watchlist ADD COLUMN IF NOT EXISTS display_name TEXT;

UPDATE ktru_watchlist
SET is_active = false
WHERE code NOT IN ('26.20.18.000-00000001', '26.20.16.120-00000001');

UPDATE ktru_watchlist
SET display_name = 'Многофункциональное устройство (МФУ)'
WHERE code = '26.20.18.000-00000001';

UPDATE ktru_watchlist
SET display_name = 'Принтер'
WHERE code = '26.20.16.120-00000001';
