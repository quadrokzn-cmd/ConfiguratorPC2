-- =============================================================
-- КВАДРО-ТЕХ: миграция 014 — recalculated_at у позиций спецификации (этап 9А.2.1)
--
-- specification_items хранит цену-снимок на момент выбора варианта в
-- проект. Цены не пересчитываются при изменении supplier_prices.
-- Менеджеру иногда нужно «освежить» старый проект под актуальные цены.
--
-- Поле recalculated_at заполняется при ручном пересчёте через UI
-- /project/{id}/spec/recalc и /project/{id}/spec/{item_id}/recalc.
-- Если цена не изменилась — поле не обновляется.
--
-- Идемпотентно: ADD COLUMN IF NOT EXISTS.
-- =============================================================

ALTER TABLE specification_items
  ADD COLUMN IF NOT EXISTS recalculated_at TIMESTAMPTZ;
