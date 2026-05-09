-- 033: дефолтные auctions-permissions для существующих пользователей
-- (этап 7 слияния QT↔CPC2, 2026-05-08).
--
-- В C-PC2 действует плоская модель прав: users.permissions JSONB —
-- словарь {key: bool}, ключи перечислены в shared/permissions.py
-- (MODULE_KEYS). Тонкие права для модуля «Аукционы» решено
-- реализовать как отдельные ключи верхнего уровня:
--   * auctions               — базовый view (страница /auctions, чтение)
--   * auctions_edit_status   — менять статус лота
--   * auctions_edit_settings — править margin_threshold, ktru_watchlist
--                              и excluded_regions
--
-- Дефолты по роли (выставляем только на отсутствующие ключи —
-- не перезаписываем уже выставленные администратором значения):
--   * admin   → auctions=true, auctions_edit_status=true, auctions_edit_settings=true
--   * manager → auctions=true, auctions_edit_status=true, auctions_edit_settings=false
--
-- Идемпотентность гарантируется условием WHERE NOT (permissions ? '<key>'):
-- если ключ уже есть, UPDATE пропустит строку и существующее значение
-- сохранится. Повторный прогон миграции — no-op.

-- ── auctions (view) ─────────────────────────────────────────────────────
UPDATE users
SET permissions = COALESCE(permissions, '{}'::jsonb)
                  || jsonb_build_object('auctions', true)
WHERE role = 'admin'
  AND (permissions IS NULL OR NOT (permissions ? 'auctions'));

UPDATE users
SET permissions = COALESCE(permissions, '{}'::jsonb)
                  || jsonb_build_object('auctions', true)
WHERE role = 'manager'
  AND (permissions IS NULL OR NOT (permissions ? 'auctions'));

-- ── auctions_edit_status ────────────────────────────────────────────────
UPDATE users
SET permissions = COALESCE(permissions, '{}'::jsonb)
                  || jsonb_build_object('auctions_edit_status', true)
WHERE role = 'admin'
  AND (permissions IS NULL OR NOT (permissions ? 'auctions_edit_status'));

UPDATE users
SET permissions = COALESCE(permissions, '{}'::jsonb)
                  || jsonb_build_object('auctions_edit_status', true)
WHERE role = 'manager'
  AND (permissions IS NULL OR NOT (permissions ? 'auctions_edit_status'));

-- ── auctions_edit_settings ─────────────────────────────────────────────
UPDATE users
SET permissions = COALESCE(permissions, '{}'::jsonb)
                  || jsonb_build_object('auctions_edit_settings', true)
WHERE role = 'admin'
  AND (permissions IS NULL OR NOT (permissions ? 'auctions_edit_settings'));

UPDATE users
SET permissions = COALESCE(permissions, '{}'::jsonb)
                  || jsonb_build_object('auctions_edit_settings', false)
WHERE role = 'manager'
  AND (permissions IS NULL OR NOT (permissions ? 'auctions_edit_settings'));
