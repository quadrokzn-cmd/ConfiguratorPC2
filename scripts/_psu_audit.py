#!/usr/bin/env python3
"""Аудит psus на проде (этап 11.6.2.5.0).

Запускается через railway ssh: получает DATABASE_URL из окружения,
коннектится psycopg2-ом и печатает все секции аудита в stdout.
Временный, добавлен в .gitignore через scripts/reports вершину;
после этапа можно удалить.
"""
from __future__ import annotations

import os
import sys
import psycopg2


def fetch(cur, sql, params=None):
    cur.execute(sql, params or ())
    cols = [d.name for d in cur.description]
    return cols, cur.fetchall()


def print_table(title, cols, rows, *, max_col=120):
    print(f"\n=== {title} ===")
    if not rows:
        print("(нет строк)")
        return
    norm_rows = []
    for r in rows:
        nr = []
        for v in r:
            if v is None:
                nr.append("")
            elif isinstance(v, list):
                nr.append("; ".join(str(x) for x in v if x is not None)[:max_col])
            else:
                s = str(v).replace("\n", " ").replace("\r", " ")
                nr.append(s[:max_col])
            if len(nr[-1]) > max_col:
                nr[-1] = nr[-1][:max_col-3] + "..."
        norm_rows.append(nr)
    widths = [len(c) for c in cols]
    for r in norm_rows:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len(v))
    fmt = " | ".join("{:<" + str(w) + "}" for w in widths)
    print(fmt.format(*cols))
    print("-+-".join("-" * w for w in widths))
    for r in norm_rows:
        print(fmt.format(*r))
    print(f"({len(rows)} строк)")


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL не задан", file=sys.stderr)
        return 1

    conn = psycopg2.connect(db_url, connect_timeout=15)
    conn.set_client_encoding("UTF8")
    cur = conn.cursor()

    cur.execute(
        "SELECT COUNT(*) AS total_visible, "
        "       COUNT(*) FILTER (WHERE power_watts IS NULL) AS power_null "
        "  FROM psus WHERE is_hidden = false"
    )
    total_visible, power_null = cur.fetchone()
    print("=== STEP 1.1: totals (visible) ===")
    print(f"total_visible = {total_visible}")
    print(f"power_null    = {power_null}")

    cols, rows = fetch(cur,
        "SELECT column_name, data_type, is_nullable "
        "  FROM information_schema.columns "
        " WHERE table_schema='public' AND table_name='psus' "
        " ORDER BY ordinal_position"
    )
    print_table("STEP 1.1b: psus columns", cols, rows)

    cols, rows = fetch(cur,
        "SELECT manufacturer AS brand, COUNT(*) "
        "  FROM psus WHERE is_hidden=false AND power_watts IS NULL "
        "  GROUP BY manufacturer ORDER BY COUNT(*) DESC LIMIT 30"
    )
    print_table("STEP 1.2a: brands with NULL power_watts", cols, rows)

    adapter_re = (
        r"(адаптер|adapter|переходник|кабель.*пит|converter|"
        r"step.up|step.down|inverter|powerbank|charger|зарядн)"
    )
    cols, rows = fetch(cur,
        "SELECT p.id, p.manufacturer AS brand, p.model, "
        "       array_remove(array_agg(DISTINCT sp.raw_name), NULL) AS raw_names "
        "  FROM psus p "
        "  LEFT JOIN supplier_prices sp ON sp.component_id=p.id AND sp.category='psu' "
        " WHERE p.is_hidden=false "
        "   AND ( p.model ~* %s OR EXISTS ( "
        "        SELECT 1 FROM supplier_prices sp2 "
        "         WHERE sp2.component_id=p.id AND sp2.category='psu' "
        "           AND sp2.raw_name ~* %s ) ) "
        " GROUP BY p.id, p.manufacturer, p.model "
        " ORDER BY p.manufacturer, p.id LIMIT 200",
        (adapter_re, adapter_re),
    )
    print_table(f"STEP 1.2b: adapter-like in psus (re={adapter_re})", cols, rows, max_col=80)

    cooler_re = (
        r"(блок\s*питания|\bpsu\b|power\s*supply|atx.*[3-9][0-9]{2}\s*W|"
        r"[3-9][0-9]{2}\s*W.*atx|80\+|Mirage\s*Gold|Bronze.*[3-9][0-9]{2}\s*W)"
    )
    cols, rows = fetch(cur,
        "SELECT c.id, c.manufacturer, c.model, "
        "       array_remove(array_agg(DISTINCT sp.raw_name), NULL) AS raw_names "
        "  FROM coolers c "
        "  LEFT JOIN supplier_prices sp ON sp.component_id=c.id AND sp.category='cooler' "
        " WHERE c.is_hidden=false "
        "   AND ( c.model ~* %s OR EXISTS ( "
        "        SELECT 1 FROM supplier_prices sp2 "
        "         WHERE sp2.component_id=c.id AND sp2.category='cooler' "
        "           AND sp2.raw_name ~* %s ) ) "
        " GROUP BY c.id, c.manufacturer, c.model ORDER BY c.id",
        (cooler_re, cooler_re),
    )
    print_table("STEP 1.3a: PSU markers in coolers", cols, rows, max_col=80)

    case_re = (
        r"(блок\s*питания|\bpsu\b|power\s*supply|atx.*[3-9][0-9]{2}\s*W|"
        r"[3-9][0-9]{2}\s*W.*atx)"
    )
    cols, rows = fetch(cur,
        "SELECT c.id, c.manufacturer, c.model, c.is_hidden, "
        "       array_remove(array_agg(DISTINCT sp.raw_name), NULL) AS raw_names "
        "  FROM cases c "
        "  LEFT JOIN supplier_prices sp ON sp.component_id=c.id AND sp.category='case' "
        " WHERE c.is_hidden=false "
        "   AND ( c.model ~* %s OR EXISTS ( "
        "        SELECT 1 FROM supplier_prices sp2 "
        "         WHERE sp2.component_id=c.id AND sp2.category='case' "
        "           AND sp2.raw_name ~* %s ) ) "
        "   AND c.id NOT IN (1066, 1737) "
        " GROUP BY c.id, c.manufacturer, c.model, c.is_hidden ORDER BY c.id",
        (case_re, case_re),
    )
    print_table("STEP 1.3b: PSU markers in cases (excl 1066/1737)", cols, rows, max_col=80)

    for tbl, cat in (("cases", "case"), ("coolers", "cooler"), ("psus", "psu")):
        cols, rows = fetch(cur,
            f"SELECT c.id, c.manufacturer, c.model, c.is_hidden, "
            f"       array_remove(array_agg(DISTINCT sp.raw_name), NULL) AS raw_names "
            f"  FROM {tbl} c "
            f"  LEFT JOIN supplier_prices sp ON sp.component_id=c.id AND sp.category=%s "
            f" WHERE c.id IN (1066, 1737) "
            f" GROUP BY c.id, c.manufacturer, c.model, c.is_hidden",
            (cat,),
        )
        print_table(f"STEP 1.3c: ids 1066/1737 in {tbl}", cols, rows, max_col=120)

    cols, rows = fetch(cur,
        "SELECT manufacturer AS brand, COUNT(*) "
        "  FROM psus WHERE is_hidden=false "
        "  GROUP BY manufacturer ORDER BY COUNT(*) DESC LIMIT 50"
    )
    print_table("STEP 1.4: PSU brand distribution (visible, top 50)", cols, rows)

    cols, rows = fetch(cur,
        "SELECT p.id, p.manufacturer AS brand, p.model, "
        "       array_remove(array_agg(DISTINCT sp.raw_name), NULL) AS raw_names "
        "  FROM psus p "
        "  LEFT JOIN supplier_prices sp ON sp.component_id=p.id AND sp.category='psu' "
        " WHERE p.is_hidden=false AND p.power_watts IS NULL "
        " GROUP BY p.id, p.manufacturer, p.model "
        " ORDER BY p.manufacturer, p.id LIMIT 250",
        (),
    )
    print_table("STEP 1.5: psus NULL power_watts sample", cols, rows, max_col=130)

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
