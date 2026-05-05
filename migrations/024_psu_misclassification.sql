-- =============================================================
-- КВАДРО-ТЕХ: миграция 024 — переклассификация PSU из coolers
-- (этап 11.6.2.5.0b)
--
-- Что меняет:
--   1. Создаёт скелеты в psus для 7 настоящих PSU, ошибочно
--      классифицированных как coolers:
--        coolers.id 1171 → Aerocool Mirage Gold 650W
--        coolers.id 1668 → PCCOOLER 750W 80+ Gold
--        coolers.id 1681 → PcCooler P5-YK850-B1F 850W
--        coolers.id 1682 → PcCooler P5-YN1000-G1F 1000W
--        coolers.id 1683 → PcCooler P5-YS850-G1F 850W
--        coolers.id 1684 → PcCooler P3-F450-W1H 450W
--        coolers.id 1689 → PcCooler P5-YS1000-G1F 1000W
--   2. Помечает is_hidden=TRUE те же 7 строк в coolers + 2 case-дубля:
--        coolers.id 1686 → Корпус PcCooler C3B310 (это case, не cooler)
--        coolers.id 1687 → Корпус PcCooler C3D510 (это case, не cooler)
--      Случай case-дублей: настоящие корпуса PcCooler C3B310/C3D510
--      уже существуют в таблице cases, эти 2 строки в coolers — мусор
--      от первичной классификации по слову «PCCOOLER» в raw_name.
--
-- Зачем:
--   Аудит 5.0a показал, что 7 PCCooler/Aerocool PSU попали в coolers
--   потому что у них в raw_name есть «PCCOOLER» / «Aerocool» / слова
--   с маркерами кулера. Сами compiones существуют только в coolers и
--   не видны в PSU-подборе. Скелет в psus нужен, чтобы AI-обогащение
--   на этапе 11.6.2.5.1 могло заполнить power_watts/efficiency.
--
-- Идемпотентно:
--   * INSERT через NOT EXISTS — если строка уже существует (по model
--     + manufacturer), вторая попытка ничего не делает.
--   * UPDATE через WHERE is_hidden = FALSE — если уже скрыто, no-op.
--
-- Что НЕ меняется:
--   * supplier_prices не трогаем. Связки price→component_id остаются
--     на coolers.id (которые скрыты). Новые psus.id записываются как
--     pure-скелеты без прайс-привязок; следующий прогон загрузчика
--     прайсов заведёт их, если есть совпадение по SKU/GTIN.
-- =============================================================

-- 1. Скелеты в psus для 7 настоящих PSU. Делаем через INSERT ... SELECT
--    из coolers, чтобы model/sku/gtin перенеслись 1-в-1. Manufacturer
--    выставляем сразу (оставлять «unknown» — значит снова дёрнуть
--    recover_psu_manufacturer.py). is_hidden=FALSE по дефолту.
INSERT INTO psus (model, manufacturer, sku, gtin, is_hidden, created_at)
SELECT
    c.model,
    CASE
        WHEN c.id = 1171 THEN 'Aerocool'
        WHEN c.id = 1668 THEN 'PCCOOLER'
        ELSE 'PcCooler'
    END AS manufacturer,
    c.sku,
    c.gtin,
    FALSE,
    NOW()
FROM coolers c
WHERE c.id IN (1171, 1668, 1681, 1682, 1683, 1684, 1689)
  AND NOT EXISTS (
        SELECT 1
          FROM psus p
         WHERE p.model = c.model
           AND COALESCE(p.sku, '') = COALESCE(c.sku, '')
  );

-- 2. Скрываем 7 PSU + 2 case-дубля из coolers.
UPDATE coolers
   SET is_hidden = TRUE
 WHERE id IN (1171, 1668, 1681, 1682, 1683, 1684, 1689,  -- настоящие PSU
              1686, 1687)                                -- case-дубли
   AND is_hidden = FALSE;
