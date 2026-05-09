-- normalize_brands.sql
-- Diagnostic SELECT-ы для брендов в nomenclature.
-- Запускать вручную через psql ДО прогона `python scripts/normalize_brands.py --apply`
-- и ПОВТОРНО — после, чтобы сравнить.
--
--   psql "$DATABASE_URL" -f scripts/normalize_brands.sql
--
-- Сами UPDATE-ы — внутри Python-скрипта (там словарь алиасов canonical_brand).

\echo === 1) Полное распределение brand → count ===
SELECT brand, count(*) AS n
  FROM nomenclature
 GROUP BY brand
 ORDER BY n DESC, brand;

\echo
\echo === 2) Задвоения по (mpn, brand) — count > 1 ===
SELECT mpn,
       brand,
       count(*)                    AS n,
       array_agg(sku ORDER BY sku) AS skus
  FROM nomenclature
 WHERE mpn IS NOT NULL
 GROUP BY mpn, brand
HAVING count(*) > 1
 ORDER BY n DESC, mpn;

\echo
\echo === 3) Один MPN под РАЗНЫМИ brand (кандидаты на дубли SKU) ===
SELECT mpn,
       count(DISTINCT brand)                    AS brand_variants,
       array_agg(DISTINCT brand ORDER BY brand) AS brands,
       array_agg(sku   ORDER BY sku)            AS skus
  FROM nomenclature
 WHERE mpn IS NOT NULL
 GROUP BY mpn
HAVING count(DISTINCT brand) > 1
 ORDER BY brand_variants DESC, mpn;
