"""Один раз для 11.6.2.7: SQL-статистика NULL у motherboards на проде +
выгрузка содержимого NULL-rows для inline AI-обогащения. После
использования можно удалить."""
import os
import psycopg2

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

# 1) Сводная статистика по NULL.
cur.execute(
    """
    SELECT
      COUNT(*)                                            AS total_visible,
      COUNT(*) FILTER (WHERE chipset     IS NULL)         AS chipset_null,
      COUNT(*) FILTER (WHERE socket      IS NULL)         AS socket_null,
      COUNT(*) FILTER (WHERE memory_type IS NULL)         AS memory_null,
      COUNT(*) FILTER (WHERE has_m2_slot IS NULL)         AS m2_null
    FROM motherboards WHERE is_hidden = false
    """
)
total, c_null, s_null, m_null, m2_null = cur.fetchone()
print(f"total_visible={total}")
print(f"chipset_null={c_null}")
print(f"socket_null={s_null}")
print(f"memory_null={m_null}")
print(f"m2_null={m2_null}")

# 2) Конкретные id-ы NULL-rows (chipset/socket).
print("\n--- NULL items (chipset OR socket): ---")
cur.execute(
    """
    SELECT
      mb.id, mb.manufacturer, mb.model, mb.chipset, mb.socket,
      mb.memory_type, mb.has_m2_slot,
      array_remove(array_agg(DISTINCT sp.raw_name), NULL) AS raw_names
    FROM motherboards mb
    LEFT JOIN supplier_prices sp
      ON sp.component_id = mb.id AND sp.category = 'motherboard'
    WHERE mb.is_hidden = false
      AND (mb.chipset IS NULL OR mb.socket IS NULL)
    GROUP BY mb.id
    ORDER BY mb.id
    """
)
for row in cur.fetchall():
    id_, mfg, model, chipset, socket, memtype, hasm2, raw_names = row
    print(f"id={id_}")
    print(f"  manufacturer={mfg}")
    print(f"  model={model}")
    print(f"  chipset={chipset!r}  socket={socket!r}")
    print(f"  memory_type={memtype!r}  has_m2_slot={hasm2}")
    print(f"  raw_names={raw_names}")

cur.close()
conn.close()
