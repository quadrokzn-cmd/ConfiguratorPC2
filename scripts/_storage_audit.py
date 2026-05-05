#!/usr/bin/env python3
"""Аудит storages на проде (этап 11.6.2.6.0a, диагностика).

Запускается через railway ssh: получает DATABASE_URL из окружения,
коннектится psycopg2-ом и печатает все секции аудита в stdout.
Только SELECT, никаких модификаций. Временный, в .gitignore через
паттерн scripts/reports/. Сам файл — однократный, можно удалить
после этапа.
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
            elif isinstance(v, bool):
                nr.append("t" if v else "f")
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


def pick(colnames, *candidates):
    """Возвращает первое имя из candidates, реально существующее в colnames."""
    cset = set(colnames)
    for c in candidates:
        if c in cset:
            return c
    return None


def main() -> int:
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL не задан", file=sys.stderr)
        return 1

    conn = psycopg2.connect(db_url, connect_timeout=15)
    conn.set_client_encoding("UTF8")
    cur = conn.cursor()

    # ---- ШАГ 1: схема storages ----
    cols, rows = fetch(cur,
        "SELECT column_name, data_type, is_nullable "
        "  FROM information_schema.columns "
        " WHERE table_schema='public' AND table_name='storages' "
        " ORDER BY ordinal_position"
    )
    print_table("STEP 1: storages columns", cols, rows)
    storage_cols = [r[0] for r in rows]

    # Эвристика подбора имён
    iface_col = pick(storage_cols, "interface", "interface_type", "iface", "bus")
    form_col = pick(storage_cols, "form_factor", "form_factor_drive", "physical_form_factor")
    type_col = pick(storage_cols, "storage_type", "drive_type", "type", "media_type")
    cap_col = pick(storage_cols, "capacity_gb", "capacity", "capacity_mb", "size_gb", "volume_gb")

    print("\n=== Resolved field names ===")
    print(f"interface_field   = {iface_col}")
    print(f"form_factor_field = {form_col}")
    print(f"type_field        = {type_col}")
    print(f"capacity_field    = {cap_col}")
    if not all([iface_col, form_col, type_col, cap_col]):
        print("WARNING: один или несколько полей не найдены; "
              "соответствующие шаги пропустим/адаптируем", file=sys.stderr)

    # ---- ШАГ 2: NULL-распределение ----
    null_filters = []
    select_parts = ["COUNT(*) AS total_visible"]
    for label, col in (("iface_null", iface_col),
                       ("form_null", form_col),
                       ("type_null", type_col),
                       ("cap_null", cap_col)):
        if col:
            select_parts.append(
                f"COUNT(*) FILTER (WHERE {col} IS NULL) AS {label}"
            )
        else:
            select_parts.append(f"NULL::int AS {label}")
    sql2 = (
        "SELECT " + ", ".join(select_parts) +
        "  FROM storages WHERE is_hidden = false"
    )
    cols, rows = fetch(cur, sql2)
    print_table("STEP 2: NULL distribution (visible storages)", cols, rows)

    # ---- ШАГ 3: топ-30 брендов с NULL interface ----
    if iface_col:
        cols, rows = fetch(cur,
            "SELECT manufacturer, COUNT(*) FROM storages "
            f" WHERE is_hidden = false AND {iface_col} IS NULL "
            " GROUP BY manufacturer ORDER BY COUNT(*) DESC LIMIT 30"
        )
        print_table(
            f"STEP 3: top-30 brands with NULL {iface_col}",
            cols, rows,
        )
        top5_brands = [r[0] for r in rows[:5]]
    else:
        top5_brands = []
        print("\n=== STEP 3: SKIP (no interface column) ===")

    # ---- ШАГ 4: sample raw_name по топ-5 брендам ----
    if iface_col and top5_brands:
        for brand in top5_brands:
            cols, rows = fetch(cur,
                "SELECT s.id, sp.raw_name FROM storages s "
                "  JOIN supplier_prices sp ON sp.component_id = s.id "
                "                          AND sp.category = 'storage' "
                f" WHERE s.is_hidden = false AND s.{iface_col} IS NULL "
                "   AND s.manufacturer = %s "
                " LIMIT 10",
                (brand,),
            )
            print_table(
                f"STEP 4: sample raw_name for brand={brand!r}",
                cols, rows, max_col=140,
            )

    # ---- ШАГ 5: мусорные маркеры в raw_name ----
    junk_re = (
        r"(USB.?hub|card.?reader|кардридер|кабель|cable.*sata|"
        r"enclosure|корпус.*(hdd|ssd)|DVD|Blu.?ray|оптическ.*привод|"
        r"hub\b|концентратор|док.?станция|переходник)"
    )
    cols, rows = fetch(cur,
        "SELECT s.id, s.manufacturer, sp.raw_name FROM storages s "
        "  JOIN supplier_prices sp ON sp.component_id = s.id "
        "                          AND sp.category = 'storage' "
        " WHERE s.is_hidden = false "
        "   AND sp.raw_name ~* %s "
        " ORDER BY s.manufacturer LIMIT 80",
        (junk_re,),
    )
    print_table(
        f"STEP 5: junk markers in storages.raw_name",
        cols, rows, max_col=140,
    )

    # ---- ШАГ 6: storage-маркеры в чужих таблицах ----
    cases_re = r'(SSD|HDD|NVMe|2\.5"|3\.5"|M\.2\b)'
    cols, rows = fetch(cur,
        "SELECT id, manufacturer, model FROM cases "
        " WHERE is_hidden = false "
        "   AND model ~* %s "
        "   AND id NOT IN (1066, 1737) "
        " LIMIT 30",
        (cases_re,),
    )
    print_table("STEP 6a: storage markers in cases (excl 1066/1737)", cols, rows, max_col=140)

    mb_re = r"(SSD|HDD|NVMe|накопитель)"
    cols, rows = fetch(cur,
        "SELECT id, manufacturer, model FROM motherboards "
        " WHERE is_hidden = false "
        "   AND model ~* %s "
        " LIMIT 30",
        (mb_re,),
    )
    print_table("STEP 6b: storage markers in motherboards", cols, rows, max_col=140)

    # ---- ШАГ 7: топ-50 брендов всех видимых storages ----
    cols, rows = fetch(cur,
        "SELECT manufacturer, COUNT(*) FROM storages "
        " WHERE is_hidden = false GROUP BY manufacturer "
        " ORDER BY COUNT(*) DESC LIMIT 50"
    )
    print_table("STEP 7: top-50 brand distribution (visible storages)", cols, rows)

    cur.close()
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
