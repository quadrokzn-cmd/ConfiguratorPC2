"""Доступ к данным матчинга: загрузка лотов/SKU, derive ktru_codes_array,
derive nmck_per_unit, UPSERT в `matches`.

Контекст состояния БД (2026-05-07): у SKU `ktru_codes_array` пустой, у позиций
лотов `nmck_per_unit` NULL и `required_attrs_jsonb` пустой. Чтобы матчинг
заработал на пилотных данных, repository:
- деривирует `ktru_codes_array` SKU из (`category`, `attrs_jsonb.colorness`)
  по таблице KTRU_DERIVE — раз и идемпотентно (UPDATE WHERE ktru_codes_array
  IS NULL OR cardinality(...)=0). Это восстанавливаемая операция.
- деривирует `nmck_per_unit` для одно-позиционных тендеров: nmck_total/qty.

Атрибуты лота извлекает из `name` в matcher (см. `name_attrs_parser`).

Этап 8 слияния (2026-05-08): таблица `nomenclature` (QT-репо) переименована
в `printers_mfu` (C-PC2 миграция 031). SQL ниже работает с printers_mfu;
колонки совпадают (id, sku, brand, name, category, ktru_codes_array,
attrs_jsonb, cost_base_rub).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from loguru import logger
from sqlalchemy import Engine, text

from app.services.auctions.match.matcher import (
    CandidateMatch,
    NomenclatureView,
    TenderItemView,
    serialize_rule_hits,
)

# (category, colorness) → конкретные KTRU-коды, под которые подходит SKU.
# - 26.20.18.000-00000069 — МФУ ч/б
# - 26.20.18.000-00000068 — МФУ цветной
# - 26.20.16.120-00000013 — Принтер ч/б
# - 26.20.16.120-00000014 — Принтер цветной
# - 26.20.16.120-00000101 — Принтер (общий) — добавлен в оба printer-варианта,
#   потому что в живых лотах под ним встречаются и ч/б, и цветные требования.
KTRU_DERIVE: dict[tuple[str, str], list[str]] = {
    ("mfu", "ч/б"):     ["26.20.18.000-00000069"],
    ("mfu", "цветной"): ["26.20.18.000-00000068"],
    ("mfu", "n/a"):     ["26.20.18.000-00000068", "26.20.18.000-00000069"],
    ("printer", "ч/б"):     ["26.20.16.120-00000013", "26.20.16.120-00000101"],
    ("printer", "цветной"): ["26.20.16.120-00000014", "26.20.16.120-00000101"],
    ("printer", "n/a"):     ["26.20.16.120-00000013", "26.20.16.120-00000014", "26.20.16.120-00000101"],
}


@dataclass(frozen=True)
class DeriveStats:
    sku_ktru_filled: int
    items_nmck_derived: int


def derive_sku_ktru_codes(engine: Engine) -> int:
    """Заполняет printers_mfu.ktru_codes_array для SKU с пустым массивом,
    исходя из (category, colorness). Идемпотентно. Возвращает кол-во строк
    с присвоенными значениями."""
    sql = text("""
        SELECT id, category, attrs_jsonb->>'colorness' AS colorness
        FROM printers_mfu
        WHERE category IN ('mfu', 'printer')
          AND (ktru_codes_array IS NULL OR cardinality(ktru_codes_array) = 0)
    """)
    update_sql = text(
        "UPDATE printers_mfu SET ktru_codes_array = CAST(:codes AS TEXT[]) WHERE id = :id"
    )
    filled = 0
    with engine.begin() as conn:
        rows = list(conn.execute(sql))
        for r in rows:
            colorness = (r.colorness or "n/a").strip().lower()
            if colorness in ("ч/б", "цветной"):
                key = (r.category, colorness)
            else:
                key = (r.category, "n/a")
            codes = KTRU_DERIVE.get(key)
            if not codes:
                continue
            pg_array = "{" + ",".join(f'"{c}"' for c in codes) + "}"
            conn.execute(update_sql, {"codes": pg_array, "id": r.id})
            filled += 1
    return filled


def derive_single_position_nmck(engine: Engine) -> int:
    """Для одно-позиционных тендеров (ровно одна tender_items, qty>0, nmck_total!=NULL)
    выставляет tender_items.nmck_per_unit = nmck_total / qty.
    Идемпотентно: затрагивает только строки с nmck_per_unit IS NULL.
    """
    sql = text("""
        UPDATE tender_items ti
        SET nmck_per_unit = ROUND(t.nmck_total / ti.qty, 2)
        FROM tenders t
        WHERE ti.tender_id = t.reg_number
          AND ti.nmck_per_unit IS NULL
          AND ti.qty > 0
          AND t.nmck_total IS NOT NULL
          AND (SELECT count(*) FROM tender_items x WHERE x.tender_id = t.reg_number) = 1
        RETURNING ti.id
    """)
    with engine.begin() as conn:
        rows = list(conn.execute(sql))
    return len(rows)


def prepare_data(engine: Engine) -> DeriveStats:
    """Один вызов на запуск матчинга — приведение БД к виду, в котором матчинг работает."""
    sku_filled = derive_sku_ktru_codes(engine)
    nmck_derived = derive_single_position_nmck(engine)
    if sku_filled or nmck_derived:
        logger.info(
            "match.derive: ktru_codes_array filled for {} SKU; nmck_per_unit derived for {} items",
            sku_filled,
            nmck_derived,
        )
    return DeriveStats(sku_ktru_filled=sku_filled, items_nmck_derived=nmck_derived)


def load_tender_items(engine: Engine, tender_id: str | None = None) -> list[TenderItemView]:
    """Все позиции лотов (если tender_id=None) или одного тендера. Только те, у
    которых есть KTRU и есть `nmck_per_unit` — позиции без KTRU/цены матчинг
    обработать не может (Этап 1/3 проваливаются)."""
    sql = """
        SELECT id, tender_id, position_num, ktru_code, name, qty, unit,
               nmck_per_unit, required_attrs_jsonb
        FROM tender_items
        WHERE ktru_code IS NOT NULL AND ktru_code != ''
    """
    params: dict = {}
    if tender_id is not None:
        sql += " AND tender_id = :tender_id"
        params["tender_id"] = tender_id
    sql += " ORDER BY tender_id, position_num"

    out: list[TenderItemView] = []
    with engine.connect() as conn:
        for r in conn.execute(text(sql), params):
            out.append(
                TenderItemView(
                    id=r.id,
                    tender_id=r.tender_id,
                    position_num=r.position_num,
                    ktru_code=r.ktru_code,
                    name=r.name,
                    qty=Decimal(r.qty) if r.qty is not None else Decimal("1"),
                    unit=r.unit,
                    nmck_per_unit=Decimal(r.nmck_per_unit) if r.nmck_per_unit is not None else None,
                    required_attrs_jsonb=r.required_attrs_jsonb or {},
                )
            )
    return out


def load_candidates_for_ktru(engine: Engine, ktru_code: str) -> list[NomenclatureView]:
    """Все SKU, у которых ktru_code попадает в `ktru_codes_array`.

    Этап 9a-uncenka (2026-05-10): добавлен фильтр `is_hidden = FALSE` —
    SKU с уценкой/повреждением/refurb/б-у помечаются is_hidden=TRUE
    скриптом `scripts/cleanup_uncenka_skus.py` и оркестратором при
    последующих загрузках прайса. Такие SKU не должны попадать в
    кандидатов матчинга — нельзя предлагать менеджеру уценочную
    позицию как primary для тендера 44-ФЗ (по закону заявить
    «новый товар» с уценкой нельзя).
    """
    sql = text("""
        SELECT id, sku, brand, name, category, ktru_codes_array, attrs_jsonb, cost_base_rub
        FROM printers_mfu
        WHERE :ktru = ANY(ktru_codes_array)
          AND is_hidden = FALSE
    """)
    out: list[NomenclatureView] = []
    with engine.connect() as conn:
        for r in conn.execute(sql, {"ktru": ktru_code}):
            out.append(
                NomenclatureView(
                    id=r.id,
                    sku=r.sku,
                    brand=r.brand,
                    name=r.name,
                    category=r.category,
                    ktru_codes_array=list(r.ktru_codes_array or []),
                    attrs_jsonb=r.attrs_jsonb or {},
                    cost_base_rub=Decimal(r.cost_base_rub) if r.cost_base_rub is not None else None,
                )
            )
    return out


def save_matches(engine: Engine, item_id: int, matches: Iterable[CandidateMatch]) -> int:
    """UPSERT-семантика: для одной tender_item_id сначала удаляем все её
    предыдущие matches, потом вставляем новые. Это даёт идемпотентность
    повторного запуска матчинга (правила/cost_base могли поменяться)."""
    delete_sql = text("DELETE FROM matches WHERE tender_item_id = :id")
    insert_sql = text("""
        INSERT INTO matches (
            tender_item_id, nomenclature_id, match_type, rule_hits_jsonb,
            price_total_rub, margin_rub, margin_pct
        ) VALUES (
            :tender_item_id, :nomenclature_id, :match_type, CAST(:rule_hits AS JSONB),
            :price_total_rub, :margin_rub, :margin_pct
        )
    """)
    inserted = 0
    with engine.begin() as conn:
        conn.execute(delete_sql, {"id": item_id})
        for m in matches:
            conn.execute(
                insert_sql,
                {
                    "tender_item_id": m.tender_item_id,
                    "nomenclature_id": m.nomenclature_id,
                    "match_type": m.match_type,
                    "rule_hits": json.dumps(serialize_rule_hits(m.rule_hits), ensure_ascii=False),
                    "price_total_rub": m.price_total_rub,
                    "margin_rub": m.margin_rub,
                    "margin_pct": m.margin_pct,
                },
            )
            inserted += 1
    return inserted


def clear_all_matches(engine: Engine) -> int:
    """Полная очистка `matches` — для полного пересчёта."""
    with engine.begin() as conn:
        result = conn.execute(text("DELETE FROM matches"))
    return result.rowcount or 0
