-- =============================================================
-- Migration 032: FK matches.nomenclature_id -> printers_mfu(id)
-- (этап 6 слияния, см. migrations/031_printers_mfu.sql и
--  scripts/migrate_qt_data_to_printers_mfu.py).
--
-- Применяется ТОЛЬКО после переноса данных QT.nomenclature →
-- printers_mfu, иначе FK упадёт на orphans. Скрипт миграции
-- данных создаёт этот файл и сразу прогоняет apply_migrations.py.
--
-- Идемпотентно: ALTER TABLE ... ADD CONSTRAINT IF NOT EXISTS
-- НЕ существует в Postgres → используем DO-блок с проверкой.
-- =============================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_matches_nomenclature_id'
          AND conrelid = 'matches'::regclass
    ) THEN
        ALTER TABLE matches
            ADD CONSTRAINT fk_matches_nomenclature_id
            FOREIGN KEY (nomenclature_id)
            REFERENCES printers_mfu (id)
            ON DELETE CASCADE;
    END IF;
END$$;
