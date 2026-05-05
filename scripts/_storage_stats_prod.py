"""Один раз для 11.6.2.6.1b: SQL-статистика NULL по 4 полям storages
на проде. После использования удалить, если станет ненужным."""
import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()
cur.execute(
    """
    SELECT
      COUNT(*)                                              AS total_visible,
      COUNT(*) FILTER (WHERE interface    IS NOT NULL)      AS iface_filled,
      COUNT(*) FILTER (WHERE form_factor  IS NOT NULL)      AS form_filled,
      COUNT(*) FILTER (WHERE storage_type IS NOT NULL)      AS type_filled,
      COUNT(*) FILTER (WHERE capacity_gb  IS NOT NULL)      AS cap_filled
    FROM storages
    WHERE is_hidden = false
    """
)
total, iface, form, stype, cap = cur.fetchone()
print(f"total_visible={total}")
print(f"iface_filled={iface}")
print(f"form_filled={form}")
print(f"type_filled={stype}")
print(f"cap_filled={cap}")
cur.close()
conn.close()
