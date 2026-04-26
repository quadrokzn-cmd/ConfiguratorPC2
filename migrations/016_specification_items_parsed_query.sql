-- Этап 9А.2.3: snapshot входных данных подбора + previous build для rollback.
--
-- parsed_query_snapshot — JSON с теми же входными данными, что были у
-- первоначального подбора (BuildRequest). Нужен для reoptimize: чтобы
-- заново прогнать builder.build_config(req) с тем же контекстом — и состав
-- конфигурации мог измениться, если у поставщиков появились более выгодные
-- варианты.
--
-- previous_build_result_json — снимок выбранного варианта (Intel/AMD) до
-- последнего reoptimize. При rollback'е возвращаем его в queries и в
-- ценах spec_item, отменяя последний пересчёт.
--
-- previous_unit_usd / previous_total_usd — цены до пересчёта. Хранить отдельно,
-- чтобы rollback не зависел от парсинга JSON.

ALTER TABLE specification_items
  ADD COLUMN IF NOT EXISTS parsed_query_snapshot JSONB;

ALTER TABLE specification_items
  ADD COLUMN IF NOT EXISTS previous_build_result_json JSONB;

ALTER TABLE specification_items
  ADD COLUMN IF NOT EXISTS previous_unit_usd NUMERIC(10,2);

ALTER TABLE specification_items
  ADD COLUMN IF NOT EXISTS previous_total_usd NUMERIC(10,2);

ALTER TABLE specification_items
  ADD COLUMN IF NOT EXISTS reoptimized_at TIMESTAMPTZ;

-- Backfill для существующих позиций: parsed_query_snapshot = build_request_json
-- родительского query (если он есть).
UPDATE specification_items s
   SET parsed_query_snapshot = q.build_request_json
  FROM queries q
 WHERE s.parsed_query_snapshot IS NULL
   AND s.query_id = q.id
   AND q.build_request_json IS NOT NULL;
