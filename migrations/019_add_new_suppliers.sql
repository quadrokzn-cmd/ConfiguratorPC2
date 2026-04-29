-- =============================================================
-- КВАДРО-ТЕХ: миграция 019 — три новых поставщика (этап 11.1)
--
-- Что меняет:
--   Заводит трёх новых поставщиков: Netlab, Ресурс Медиа, Green Place.
--   Email-адреса пока NULL — руководитель пришлёт отдельным шагом
--   (используется в этапе 8 для рассылок «есть в наличии?»).
--
-- Имена («Netlab», «Ресурс Медиа», «Green Place») — точное соответствие
-- supplier_name в загрузчиках app/services/price_loaders/netlab.py /
-- resurs_media.py / green_place.py. Если меняете название здесь —
-- меняйте и там, иначе orchestrator не найдёт supplier_id.
--
-- Зависимости: миграция 009 уже сделала suppliers.name UNIQUE и завела
-- ON CONFLICT-семантику; миграция 011 добавила колонку email.
-- =============================================================

INSERT INTO suppliers (name, is_active, email) VALUES
    ('Netlab',       TRUE, NULL),
    ('Ресурс Медиа', TRUE, NULL),
    ('Green Place',  TRUE, NULL)
ON CONFLICT (name) DO NOTHING;
