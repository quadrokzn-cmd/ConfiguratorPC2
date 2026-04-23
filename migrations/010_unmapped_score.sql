-- =============================================================
-- КВАДРО-ТЕХ: миграция 010 — score подозрительности для очереди
-- ручного сопоставления (Этап 7.1).
--
-- Цель — сократить ручной труд: из ~2000 created_new-записей
-- только ~300-500 подозрительных требуют внимания, остальные
-- одним кликом переводятся в confirmed_new.
--
-- Алгоритм расчёта score (0..100) живёт в app/services/mapping_service.py,
-- здесь — только схема для хранения результата и индекс для сортировки.
-- =============================================================

ALTER TABLE unmapped_supplier_items
    ADD COLUMN IF NOT EXISTS best_candidate_score INT,
    ADD COLUMN IF NOT EXISTS best_candidate_component_id INT,
    ADD COLUMN IF NOT EXISTS best_candidate_calculated_at TIMESTAMP;

CREATE INDEX IF NOT EXISTS idx_unmapped_score
    ON unmapped_supplier_items(status, best_candidate_score DESC)
    WHERE status IN ('pending', 'created_new');
