"""[scratch] Диагностика расследования run #17 (этап 12.3).

SQL-снапшот состояния supplier_prices для Treolan ПОСЛЕ run #17,
а также сравнение с предыдущей успешной загрузкой.

Запуск:  railway run python scripts/_diag_treolan_prices_prod.py
        (или python scripts/_diag_treolan_prices_prod.py с DATABASE_URL).

Ничего не пишет в БД, только SELECT.
"""
import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

cur.execute(
    """
    SELECT id FROM suppliers WHERE name = 'Treolan' LIMIT 1
    """
)
sid_row = cur.fetchone()
if sid_row is None:
    print("Treolan supplier_id: NOT FOUND")
    raise SystemExit(1)
sid = sid_row[0]
print(f"supplier_id (Treolan) = {sid}")
print()

cur.execute(
    """
    SELECT
      COUNT(*)                                                          AS total,
      COUNT(*) FILTER (WHERE stock_qty > 0 OR transit_qty > 0)          AS active,
      COUNT(*) FILTER (WHERE stock_qty = 0 AND transit_qty = 0)         AS disappeared_now,
      COUNT(*) FILTER (WHERE stock_qty > 0)                             AS stock_pos,
      COUNT(*) FILTER (WHERE stock_qty = 0)                             AS stock_zero,
      COUNT(*) FILTER (WHERE transit_qty > 0)                           AS transit_pos,
      MIN(updated_at)                                                   AS oldest_update,
      MAX(updated_at)                                                   AS newest_update
    FROM supplier_prices
    WHERE supplier_id = %s
    """,
    (sid,),
)
row = cur.fetchone()
labels = ["total", "active(stock+transit>0)", "stock=0 AND transit=0",
          "stock>0", "stock=0", "transit>0",
          "oldest_updated_at", "newest_updated_at"]
print("--- supplier_prices (Treolan) — снимок СЕЙЧАС ---")
for label, value in zip(labels, row):
    print(f"  {label:<28} {value}")
print()

print("--- price_uploads (Treolan) — последние 6 ---")
cur.execute(
    """
    SELECT id, filename, rows_total, rows_matched, rows_unmatched, status,
           uploaded_at, report_json->>'disappeared' AS disappeared,
           report_json->>'total_rows' AS rj_total_rows,
           report_json->>'processed' AS rj_processed,
           report_json->>'duration_seconds' AS dur
      FROM price_uploads
     WHERE supplier_id = %s
     ORDER BY id DESC
     LIMIT 6
    """,
    (sid,),
)
for r in cur.fetchall():
    print(
        f"  id={r[0]:<5} status={r[5]:<8} rows_total={r[2]:<6} matched={r[3]:<6} "
        f"unmatched={r[4]:<6} disappeared={r[7]} rj_total={r[8]} rj_processed={r[9]} "
        f"dur={r[10]}s | {r[6]} | {r[1]}"
    )
print()

print("--- Самый ранний (предыдущий перед run #17) реальный счётчик ---")
cur.execute(
    """
    SELECT id, filename, rows_total, rows_matched, status, uploaded_at,
           report_json->>'disappeared' AS disappeared
      FROM price_uploads
     WHERE supplier_id = %s
       AND status IN ('success', 'partial')
       AND id < 17
     ORDER BY id DESC
     LIMIT 3
    """,
    (sid,),
)
for r in cur.fetchall():
    print(
        f"  id={r[0]} status={r[4]} rows_total={r[2]} rows_matched={r[3]} "
        f"disappeared={r[6]} created={r[5]} file={r[1]}"
    )
print()

print("--- Распределение updated_at для активных-до-run-17 ---")
print("(если все 1391 disappeared — у них updated_at будет совпадать с моментом run #17)")
cur.execute(
    """
    SELECT date_trunc('minute', updated_at) AS minute,
           COUNT(*) AS n,
           COUNT(*) FILTER (WHERE stock_qty = 0 AND transit_qty = 0) AS zeros
      FROM supplier_prices
     WHERE supplier_id = %s
     GROUP BY 1
     ORDER BY 1 DESC
     LIMIT 12
    """,
    (sid,),
)
for r in cur.fetchall():
    print(f"  {r[0]}  total={r[1]:<6} zeros={r[2]}")
print()

print("--- Sample 5 disappeared записей (stock=0 AND transit=0 с самым свежим updated_at) ---")
cur.execute(
    """
    SELECT supplier_sku, category, component_id, price, currency,
           stock_qty, transit_qty, updated_at, raw_name
      FROM supplier_prices
     WHERE supplier_id = %s
       AND stock_qty = 0
       AND transit_qty = 0
     ORDER BY updated_at DESC
     LIMIT 5
    """,
    (sid,),
)
for r in cur.fetchall():
    print(f"  sku={r[0]} cat={r[1]} cid={r[2]} price={r[3]} {r[4]} stock={r[5]} transit={r[6]} updated={r[7]} name={r[8][:40] if r[8] else None}")

cur.close()
conn.close()
