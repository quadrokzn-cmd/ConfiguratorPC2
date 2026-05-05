"""Этап 11.6.2.7: сводное покрытие ключевых полей AI-блока 11.6.2.x
по 6 категориям на проде.

Запуск:
  cat scripts/_ai_block_coverage_prod.py | railway ssh -- python -

Печатает markdown-таблицу total_visible × % filled для:
  gpus / coolers / cases / psus / storages / motherboards.

Не модифицирует БД. После закрытия этапа можно удалить или оставить
как репортер."""
import os
import psycopg2

QUERIES = {
    "gpus": """
        SELECT
          COUNT(*)                                              AS total_visible,
          ROUND(100.0 * COUNT(*) FILTER (WHERE tdp_watts        IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS tdp_watts,
          ROUND(100.0 * COUNT(*) FILTER (WHERE video_outputs    IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS video_outputs,
          ROUND(100.0 * COUNT(*) FILTER (WHERE vram_gb          IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS vram_gb,
          ROUND(100.0 * COUNT(*) FILTER (WHERE vram_type        IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS vram_type
        FROM gpus WHERE is_hidden = FALSE
    """,
    "coolers": """
        SELECT
          COUNT(*)                                              AS total_visible,
          ROUND(100.0 * COUNT(*) FILTER (WHERE max_tdp_watts     IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS max_tdp_watts,
          ROUND(100.0 * COUNT(*) FILTER (WHERE supported_sockets IS NOT NULL
                                          AND array_length(supported_sockets,1) > 0)
                                                          / NULLIF(COUNT(*),0), 1) AS supported_sockets
        FROM coolers WHERE is_hidden = FALSE
    """,
    "cases": """
        SELECT
          COUNT(*)                                              AS total_visible,
          ROUND(100.0 * COUNT(*) FILTER (WHERE has_psu_included       IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS has_psu_included,
          ROUND(100.0 * COUNT(*) FILTER (WHERE supported_form_factors IS NOT NULL
                                          AND array_length(supported_form_factors,1) > 0)
                                                          / NULLIF(COUNT(*),0), 1) AS supported_form_factors,
          ROUND(100.0 * COUNT(*) FILTER (WHERE has_psu_included = TRUE
                                          AND included_psu_watts IS NOT NULL)
                                / NULLIF(COUNT(*) FILTER (WHERE has_psu_included = TRUE), 0), 1)
                                                                                  AS included_psu_watts_when_has_psu
        FROM cases WHERE is_hidden = FALSE
    """,
    "psus": """
        SELECT
          COUNT(*)                                              AS total_visible,
          ROUND(100.0 * COUNT(*) FILTER (WHERE power_watts IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS power_watts
        FROM psus WHERE is_hidden = FALSE
    """,
    "storages": """
        SELECT
          COUNT(*)                                              AS total_visible,
          ROUND(100.0 * COUNT(*) FILTER (WHERE interface    IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS interface,
          ROUND(100.0 * COUNT(*) FILTER (WHERE form_factor  IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS form_factor,
          ROUND(100.0 * COUNT(*) FILTER (WHERE storage_type IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS storage_type,
          ROUND(100.0 * COUNT(*) FILTER (WHERE capacity_gb  IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS capacity_gb
        FROM storages WHERE is_hidden = FALSE
    """,
    "motherboards": """
        SELECT
          COUNT(*)                                              AS total_visible,
          ROUND(100.0 * COUNT(*) FILTER (WHERE chipset      IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS chipset,
          ROUND(100.0 * COUNT(*) FILTER (WHERE socket       IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS socket,
          ROUND(100.0 * COUNT(*) FILTER (WHERE memory_type  IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS memory_type,
          ROUND(100.0 * COUNT(*) FILTER (WHERE has_m2_slot  IS NOT NULL) / NULLIF(COUNT(*),0), 1) AS has_m2_slot
        FROM motherboards WHERE is_hidden = FALSE
    """,
}

conn = psycopg2.connect(os.environ["DATABASE_URL"])
cur = conn.cursor()

print("# Покрытие AI-блока 11.6.2.x — финальная сводка")
print()
print("| Категория | Total visible | Field | % filled |")
print("|---|---:|---|---:|")
for cat, sql in QUERIES.items():
    cur.execute(sql)
    cols = [d.name for d in cur.description]
    row = cur.fetchone()
    total = row[0]
    for col, val in zip(cols[1:], row[1:]):
        pct = "—" if val is None else f"{val}%"
        print(f"| {cat} | {total} | {col} | {pct} |")
print()

cur.close()
conn.close()
