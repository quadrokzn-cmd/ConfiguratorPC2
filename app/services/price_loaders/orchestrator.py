# Общий раннер загрузки прайс-листа.
#
# Делает одно и то же для любого поставщика:
#   1) получает loader по supplier_name;
#   2) iter_rows → поток PriceRow;
#   3) для каждой строки вызывает matching.resolve;
#   4) по результату пишет в supplier_prices, при ambiguous/no_match —
#      дополнительно в unmapped_supplier_items;
#   5) ведёт сводные счётчики и записывает итог в price_uploads.
#
# После этапа 6 у нас уже был SAVEPOINT на каждую строку — сохраняем
# этот паттерн, чтобы сбой на одной позиции не откатывал всю загрузку.
#
# Этап 11.2: расширили _record_upload — пишем полный отчёт в
# price_uploads.report_json (JSONB), чтобы UI /admin/price-uploads
# мог показать кнопку «Подробности» с разбивкой по источникам.
#
# Этап 11.4: при ежедневной перезагрузке прайса:
#   - в supplier_prices обновляются price/currency/stock/transit/raw_name
#     и updated_at (raw_name добавлен миграцией 022);
#   - позиции, которых нет в текущем прайсе, но были «активны» (stock+transit>0)
#     до загрузки, помечаются stock=0, transit=0 — это «disappeared».
#     Подбор кандидатов фильтрует stock>0, поэтому исчезнувшие позиции
#     автоматически выпадают из конфигуратора, но запись остаётся —
#     если поставщик завтра вернёт их, обычный UPDATE подхватит наличие.
#   - disappeared_count и список первых 50 SKU попадают в report_json и
#     показываются в UI /admin/price-uploads.
#   - Ключевая защита: при status='failed' (упало в loader или 0 матчей)
#     disappeared НЕ применяется — иначе кривая загрузка обнулит остатки.

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.services.enrichment.base import ALLOWED_TABLES, CATEGORY_TO_TABLE
from app.services.price_loaders.base import BasePriceLoader
from app.services.price_loaders.matching import (
    AMBIG_GTIN, AMBIG_MPN, EXISTING, MATCH_GTIN, MATCH_MPN, NO_MATCH,
    MatchResult, resolve,
)
from app.services.price_loaders.models import PriceRow
from shared.component_filters import (
    is_likely_case_fan,
    is_likely_case_panel_or_filter,
    is_likely_drive_cage,
    is_likely_gpu_support_bracket,
    is_likely_loose_case_fan,
    is_likely_non_psu_in_psus,
    is_likely_non_storage,
    is_likely_pcie_riser,
    is_likely_psu_adapter,
)

logger = logging.getLogger(__name__)


@dataclass
class Counters:
    total_rows: int = 0   # считано из Excel (без пустых)
    processed:  int = 0   # прошло фильтр our_category
    updated:    int = 0   # UPSERT попал в существующий component_id
    added:      int = 0   # создан новый компонент (скелет)
    skipped:    int = 0   # отфильтровано (наша категория None или нет цены)
    errors:     int = 0   # исключение на уровне строки
    unmapped_created:    int = 0  # сколько строк завели в unmapped_supplier_items
    unmapped_ambiguous:  int = 0  # из них — ambiguous_mpn/gtin
    unmapped_new:        int = 0  # из них — created_new
    # 11.4: счётчик disappeared (см. _mark_disappeared).
    disappeared:        int = 0
    # 11.4: первые 50 SKU, помеченные как disappeared — для отладки/UI.
    disappeared_skus:   list[str] = field(default_factory=list)
    disappeared_truncated: bool = False
    # Карта source → counter (для отладки/отчёта).
    by_source: dict[str, int] = field(default_factory=dict)


# Сколько SKU исчезнувших позиций сохранить в report_json (отладка/UI).
_DISAPPEARED_SAMPLE_LIMIT = 50


# ---------------------------------------------------------------------------
# Работа со справочником поставщиков
# ---------------------------------------------------------------------------

def _get_or_create_supplier(session: Session, supplier_name: str) -> int:
    """После миграции 009 suppliers.name UNIQUE, поэтому ON CONFLICT
    безопасен. Merlion/Treolan уже INSERT-нуты миграцией, OCS тоже
    существует с этапа 1 — но на случай чистой БД в тестах оставляем
    создание здесь."""
    row = session.execute(
        text("SELECT id FROM suppliers WHERE name = :n LIMIT 1"),
        {"n": supplier_name},
    ).first()
    if row:
        return int(row.id)
    result = session.execute(
        text(
            "INSERT INTO suppliers (name, is_active) VALUES (:n, TRUE) "
            "ON CONFLICT (name) DO UPDATE SET is_active = suppliers.is_active "
            "RETURNING id"
        ),
        {"n": supplier_name},
    ).first()
    return int(result.id)


# ---------------------------------------------------------------------------
# Запись компонента (создание скелета при NO_MATCH)
# ---------------------------------------------------------------------------

# Минимальный набор NOT NULL колонок по каждой таблице компонентов —
# нужен для создания «скелета» при no_match. Значения по умолчанию
# подобраны так, чтобы удовлетворить NOT NULL, но при этом явно сигналить
# («unknown»/0), что характеристика не заполнена — дозаполнит enrichment.
_SKELETON_DEFAULTS: dict[str, dict[str, object]] = {
    "cpus": {
        "socket": "unknown",
        "cores": 0,
        "threads": 0,
        "base_clock_ghz": Decimal("0"),
        "turbo_clock_ghz": Decimal("0"),
        "tdp_watts": 0,
        "has_integrated_graphics": False,
        "memory_type": "unknown",
        "package_type": "BOX",
    },
    "motherboards": {
        "socket": "unknown",
        "chipset": "unknown",
        "form_factor": "ATX",
        "memory_type": "unknown",
        "has_m2_slot": False,
    },
    "rams": {
        "memory_type": "unknown",
        "form_factor": "DIMM",
        "module_size_gb": 0,
        "modules_count": 1,
        "frequency_mhz": 0,
    },
    "gpus": {},       # все NOT NULL-поля? проверяется ниже по факту
    "storages": {},
    "cases": {},
    "psus": {},
    "coolers": {},
}


def _notnull_columns(session: Session, table: str) -> list[str]:
    """Возвращает список NOT NULL колонок таблицы (без дефолта),
    чтобы при создании скелета закрыть их все."""
    rows = session.execute(
        text(
            "SELECT column_name "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "  AND table_name = :t "
            "  AND is_nullable = 'NO' "
            "  AND column_default IS NULL "
            "  AND column_name NOT IN ('id')"
        ),
        {"t": table},
    ).all()
    return [r.column_name for r in rows]


def _create_skeleton(
    session: Session, table: str, row: PriceRow,
) -> int:
    """Создаёт минимальную запись компонента (скелет) и возвращает его id.

    Правила: заполняем model, manufacturer, sku, gtin (если есть) и все
    NOT NULL-колонки значениями из _SKELETON_DEFAULTS. Если в таблице
    есть ещё какие-то NOT NULL без дефолта, про которые мы забыли, —
    пытаемся закрыть их «безопасными» значениями по типу (0 / FALSE /
    'unknown'), иначе бросаем понятную ошибку.
    """
    assert table in ALLOWED_TABLES, f"Недопустимая таблица: {table}"

    values: dict[str, object] = {
        "model":        row.name or (row.mpn or "unknown"),
        "manufacturer": row.brand or "unknown",
        "sku":          row.mpn,
        "gtin":         row.gtin,
    }
    values.update(_SKELETON_DEFAULTS.get(table, {}))

    # Этап 9Г.1: корпусные вентиляторы при создании скелета помечаем
    # is_hidden=True, чтобы они не попадали в подбор CPU-кулеров.
    # См. shared/component_filters.py и docs/enrichment_techdebt.md §9.
    if table == "coolers" and is_likely_case_fan(
        row.name, row.brand, row.our_category,
    ):
        values["is_hidden"] = True

    # Этап 11.6.2.4.0: мусор в категории case (отдельные корзины 3.5",
    # PCIe-райзеры, сменные боковые панели, антипровисные кронштейны для
    # GPU, одиночные 120-мм вентиляторы) — при создании скелета помечаем
    # is_hidden=True. Защитный слой _CASE_HOUSING_HINTS внутри детекторов
    # не трогает полноценные корпуса с предустановленным riser/dust filter
    # (например, Lian Li SUP01X). Подробности — в docs/enrichment_techdebt.md.
    if table == "cases" and (
        is_likely_loose_case_fan(row.name, row.brand)
        or is_likely_drive_cage(row.name, row.brand)
        or is_likely_pcie_riser(row.name, row.brand)
        or is_likely_case_panel_or_filter(row.name, row.brand)
        or is_likely_gpu_support_bracket(row.name, row.brand)
    ):
        values["is_hidden"] = True

    # Этап 11.6.2.5.0b/c: адаптеры/зарядные/PoE-инжекторы/dock-станции
    # (is_likely_psu_adapter) + корпуса/кулеры/вентиляторы/радиаторы,
    # попавшие в psus (is_likely_non_psu_in_psus, добавлен на 11.6.2.5.0c —
    # ловит ситуации «model начинается с Корпус/Кулер/Вентилятор/MasterBox/
    # AIO/Mid-tower»). Оба детектора применяются логическим OR. При создании
    # скелета такие позиции помечаются is_hidden=True, чтобы AI-обогащение
    # на 11.6.2.5.1 не тратило тулколлы на поиск power_watts у не-PSU.
    # Защитные слои внутри детекторов (ATX/SFX/80+/мощность ≥200W/серии CBR/
    # Exegate UN/Ginzzu CB/XPG KYBER/Zalman ZM/Aerocool Mirage/Powerman PM/
    # 1STPLAYER NGDP/Thermaltake Smart/«Блок питания»/«Power Supply») не
    # трогают настоящие ATX-PSU. См. подробности в
    # docs/enrichment_techdebt.md, секция «PSU audit (11.6.2.5.0a/b/c)».
    if table == "psus" and (
        is_likely_psu_adapter(row.name, row.brand)
        or is_likely_non_psu_in_psus(row.name, row.brand)
    ):
        values["is_hidden"] = True

    # Этап 11.6.2.6.0b: рамки-переходники 2.5"→3.5", card-reader, USB-hub
    # и прочие аксессуары, ошибочно классифицированные как storage. При
    # создании скелета помечаем is_hidden=True, чтобы AI-обогащение
    # 11.6.2.6.1 не тратило тулколлы на поиск capacity_gb / interface
    # у рамки SNA-BR2/35. Защитные слои детектора (capacity_gb≥32 /
    # storage_type / форм-факторы NVMe/M.2/2280/mSATA/U.2) не трогают
    # настоящие SSD/HDD топ-брендов. См. подробности в
    # docs/enrichment_techdebt.md, секция «Storage audit (11.6.2.6.0a/b)».
    if table == "storages" and is_likely_non_storage(row.name, row.brand):
        values["is_hidden"] = True

    # Доп. страховка: запрашиваем актуальный список NOT NULL-колонок,
    # чтобы не упасть на таблицах, где в defaults чего-то не хватает.
    for col in _notnull_columns(session, table):
        if col in values:
            continue
        # Подбираем нейтральное значение. Для типов на этапе 1 проекта
        # подойдёт 0/False/'unknown' — это явно видимые «пустышки»,
        # которые заменяются при enrichment.
        col_type = session.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name=:t AND column_name=:c"
            ),
            {"t": table, "c": col},
        ).scalar()
        if col_type in {"integer", "smallint", "bigint"}:
            values[col] = 0
        elif col_type in {"numeric", "real", "double precision"}:
            values[col] = Decimal("0")
        elif col_type == "boolean":
            values[col] = False
        else:
            values[col] = "unknown"

    cols = list(values.keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    col_list = ", ".join(cols)
    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) RETURNING id"
    )
    res = session.execute(text(sql), values).first()
    return int(res.id)


# ---------------------------------------------------------------------------
# Запись supplier_prices и unmapped_supplier_items
# ---------------------------------------------------------------------------

def _upsert_price(
    session: Session, *,
    supplier_id: int, category: str, component_id: int,
    supplier_sku: str | None, price: Decimal, currency: str,
    stock_qty: int, transit_qty: int,
    raw_name: str | None = None,
) -> None:
    # 11.4: raw_name обновляется ВСЕГДА на актуальное название из прайса —
    # даже если оно стало короче или беднее предыдущего. Для конфигуратора
    # это не критично (основное имя — components.model + имена от других
    # поставщиков); агрегацию делает enrichment (этап 11.6).
    session.execute(
        text(
            "INSERT INTO supplier_prices "
            "    (supplier_id, category, component_id, supplier_sku, "
            "     price, currency, stock_qty, transit_qty, raw_name, updated_at) "
            "VALUES "
            "    (:supplier_id, :category, :component_id, :supplier_sku, "
            "     :price, :currency, :stock_qty, :transit_qty, :raw_name, NOW()) "
            "ON CONFLICT (supplier_id, category, component_id) DO UPDATE SET "
            "    supplier_sku = EXCLUDED.supplier_sku, "
            "    price        = EXCLUDED.price, "
            "    currency     = EXCLUDED.currency, "
            "    stock_qty    = EXCLUDED.stock_qty, "
            "    transit_qty  = EXCLUDED.transit_qty, "
            "    raw_name     = EXCLUDED.raw_name, "
            "    updated_at   = NOW()"
        ),
        {
            "supplier_id":  supplier_id,
            "category":     category,
            "component_id": component_id,
            "supplier_sku": supplier_sku,
            "price":        price,
            "currency":     currency,
            "stock_qty":    stock_qty,
            "transit_qty":  transit_qty,
            "raw_name":     raw_name,
        },
    )


def _upsert_unmapped(
    session: Session, *,
    supplier_id: int, row: PriceRow, status: str, notes: str | None,
    resolved_component_id: int | None,
) -> None:
    """INSERT в unmapped_supplier_items с ON CONFLICT DO UPDATE по
    (supplier_id, supplier_sku). Статус и notes обновляются только
    пока строка не разобрана админом: 'merged' и 'confirmed_new' —
    финальные решения, их не затираем повторной загрузкой прайса."""
    session.execute(
        text(
            "INSERT INTO unmapped_supplier_items "
            "    (supplier_id, supplier_sku, raw_category, guessed_category, "
            "     brand, mpn, gtin, raw_name, price, currency, stock, transit, "
            "     status, notes, resolved_component_id) "
            "VALUES "
            "    (:sid, :ssku, :raw_cat, :our_cat, :brand, :mpn, :gtin, :name, "
            "     :price, :cur, :stock, :transit, :status, :notes, :rcid) "
            "ON CONFLICT (supplier_id, supplier_sku) DO UPDATE SET "
            "    raw_category     = EXCLUDED.raw_category, "
            "    guessed_category = EXCLUDED.guessed_category, "
            "    brand            = EXCLUDED.brand, "
            "    mpn              = EXCLUDED.mpn, "
            "    gtin             = EXCLUDED.gtin, "
            "    raw_name         = EXCLUDED.raw_name, "
            "    price            = EXCLUDED.price, "
            "    currency         = EXCLUDED.currency, "
            "    stock            = EXCLUDED.stock, "
            "    transit          = EXCLUDED.transit, "
            "    status = CASE "
            "        WHEN unmapped_supplier_items.status IN ('merged', 'confirmed_new') "
            "            THEN unmapped_supplier_items.status "
            "        ELSE EXCLUDED.status END, "
            "    notes = CASE "
            "        WHEN unmapped_supplier_items.status IN ('merged', 'confirmed_new') "
            "            THEN unmapped_supplier_items.notes "
            "        ELSE EXCLUDED.notes END, "
            "    resolved_component_id = CASE "
            "        WHEN unmapped_supplier_items.status IN ('merged', 'confirmed_new') "
            "            THEN unmapped_supplier_items.resolved_component_id "
            "        ELSE EXCLUDED.resolved_component_id END"
        ),
        {
            "sid":     supplier_id,
            "ssku":    row.supplier_sku,
            "raw_cat": row.raw_category or "",
            "our_cat": row.our_category,
            "brand":   row.brand,
            "mpn":     row.mpn,
            "gtin":    row.gtin,
            "name":    row.name,
            "price":   row.price,
            "cur":     row.currency,
            "stock":   row.stock,
            "transit": row.transit,
            "status":  status,
            "notes":   notes,
            "rcid":    resolved_component_id,
        },
    )


# ---------------------------------------------------------------------------
# Запись price_uploads
# ---------------------------------------------------------------------------

def _record_upload(
    session: Session, *,
    supplier_id: int, filename: str, counters: Counters,
    report: dict | None = None,
) -> tuple[int, str]:
    updated      = counters.updated
    added        = counters.added
    skipped      = counters.skipped
    errors       = counters.errors
    rows_matched = updated + added

    if rows_matched == 0 and (counters.processed > 0 or counters.total_rows > 0):
        status = "failed"
    elif errors > 0 or skipped > 0:
        status = "partial"
    else:
        status = "success"

    notes = (
        f"updated={updated}, added={added}, skipped={skipped}, errors={errors}, "
        f"unmapped(amb={counters.unmapped_ambiguous}, new={counters.unmapped_new})"
    )

    # 11.2: report_json — детальный отчёт для UI /admin/price-uploads.
    # default=str: на всякий случай — Decimal/datetime в дикте превратятся
    # в строки, не сломав сериализацию.
    report_dump = json.dumps(report or {}, ensure_ascii=False, default=str)

    row = session.execute(
        text(
            "INSERT INTO price_uploads "
            "    (supplier_id, filename, rows_total, rows_matched, rows_unmatched, "
            "     status, notes, report_json) "
            "VALUES "
            "    (:supplier_id, :filename, :rows_total, :rows_matched, :rows_unmatched, "
            "     :status, :notes, CAST(:report_json AS JSONB)) "
            "RETURNING id"
        ),
        {
            "supplier_id":    supplier_id,
            "filename":       filename,
            "rows_total":     counters.processed,
            "rows_matched":   rows_matched,
            "rows_unmatched": skipped + errors,
            "status":         status,
            "notes":          notes,
            "report_json":    report_dump,
        },
    ).first()
    return int(row.id), status


def _save_failed_upload(
    filepath: str,
    supplier_name: str,
    counters: Counters,
    *,
    error_message: str | None = None,
) -> None:
    session = SessionLocal()
    try:
        row = session.execute(
            text("SELECT id FROM suppliers WHERE name = :n LIMIT 1"),
            {"n": supplier_name},
        ).first()
        if row is None:
            return
        # 11.2: при критическом фейле тоже записываем report_json — пусть
        # с error_message и тем, что успели насчитать. UI «Подробности»
        # покажет его в модалке.
        # 11.4: при failed disappeared НЕ применяется и в отчёт пишутся нули —
        # это явно сигнализирует UI/админу, что обнуления остатков не было.
        report = {
            "supplier":              supplier_name,
            "filename":              os.path.basename(filepath),
            "total_rows":            counters.total_rows,
            "processed":             counters.processed,
            "updated":               counters.updated,
            "added":                 counters.added,
            "skipped":               counters.skipped,
            "errors":                counters.errors,
            "disappeared":           0,
            "disappeared_skus":      [],
            "disappeared_truncated": False,
            "status":                "failed",
            "error_message":         error_message or "Критическая ошибка при загрузке",
        }
        report_dump = json.dumps(report, ensure_ascii=False, default=str)
        session.execute(
            text(
                "INSERT INTO price_uploads "
                "    (supplier_id, filename, rows_total, rows_matched, rows_unmatched, "
                "     status, notes, report_json) "
                "VALUES "
                "    (:supplier_id, :filename, :rows_total, 0, :rows_unmatched, 'failed', "
                "     :notes, CAST(:report_json AS JSONB))"
            ),
            {
                "supplier_id":    row.id,
                "filename":       os.path.basename(filepath),
                "rows_total":     counters.total_rows,
                "rows_unmatched": counters.skipped + counters.errors,
                "notes":          (error_message or "Критическая ошибка при загрузке")[:500],
                "report_json":    report_dump,
            },
        )
        session.commit()
    except Exception:
        try:
            logger.error(
                "Не удалось сохранить запись о провальной загрузке "
                "(подавлено, чтобы не затенять исходное исключение)."
            )
            session.rollback()
        except Exception:
            pass
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Главная функция
# ---------------------------------------------------------------------------

def _load_active_skus(session: Session, supplier_id: int) -> set[str]:
    """Возвращает множество supplier_sku, которые сейчас активны
    (stock_qty + transit_qty > 0) у указанного поставщика. Используется
    для детекции «исчезнувших» позиций при повторной загрузке прайса.

    NULL-supplier_sku отбрасываем — без идентификатора такая запись не
    может быть сопоставлена со строкой нового прайса (поставщик не имеет
    к ней «ключа доступа»). Такие строки в disappeared-логике не участвуют."""
    rows = session.execute(
        text(
            "SELECT supplier_sku FROM supplier_prices "
            "WHERE supplier_id = :sid "
            "  AND supplier_sku IS NOT NULL "
            "  AND (stock_qty > 0 OR transit_qty > 0)"
        ),
        {"sid": supplier_id},
    ).all()
    return {r.supplier_sku for r in rows}


def _mark_disappeared(
    session: Session, *,
    supplier_id: int, missing_skus: set[str], counters: Counters,
) -> None:
    """Для каждого SKU, который был активен до загрузки, но не появился
    в текущем прайсе — обнуляем stock и transit. Запись supplier_prices
    остаётся: завтра поставщик может вернуть позицию в прайс, и обычный
    UPSERT поднимет stock/transit обратно.

    Подбор кандидатов в configurator/candidates.py фильтрует по stock>0
    (или stock+transit>0 в режиме allow_transit) — поэтому disappeared
    автоматически выпадают из конфигуратора.

    На больших прайсах (Netlab — десятки тысяч SKU) выполняем единым
    UPDATE с массивом, чтобы не делать тысячи отдельных запросов."""
    if not missing_skus:
        return

    skus_list = sorted(missing_skus)  # sorted — для воспроизводимости в тестах
    session.execute(
        text(
            "UPDATE supplier_prices "
            "   SET stock_qty = 0, transit_qty = 0, updated_at = NOW() "
            " WHERE supplier_id = :sid "
            "   AND supplier_sku = ANY(:skus)"
        ),
        {"sid": supplier_id, "skus": skus_list},
    )

    counters.disappeared = len(skus_list)
    if counters.disappeared <= _DISAPPEARED_SAMPLE_LIMIT:
        counters.disappeared_skus = list(skus_list)
        counters.disappeared_truncated = False
    else:
        counters.disappeared_skus = list(skus_list[:_DISAPPEARED_SAMPLE_LIMIT])
        counters.disappeared_truncated = True


def _category_of_component(session: Session, table: str) -> str:
    """Обратное сопоставление: table → category. Нужно, чтобы писать
    корректный category в supplier_prices."""
    for cat, t in CATEGORY_TO_TABLE.items():
        if t == table:
            return cat
    raise RuntimeError(f"Нет категории для таблицы {table}")


def _process_row(
    session: Session, *,
    supplier_id: int, row: PriceRow, counters: Counters,
    seen_skus: set[str] | None = None,
) -> None:
    # 11.4: фиксируем все непустые supplier_sku из текущего прайса —
    # независимо от нашей категории. Если поставщик прислал позицию даже
    # не из нашей категории, но с тем же SKU — это всё ещё «не исчезла».
    if seen_skus is not None and row.supplier_sku:
        seen_skus.add(row.supplier_sku)

    # Фильтр 1: строка не из нашей категории.
    if row.our_category is None:
        counters.skipped += 1
        return

    counters.processed += 1

    res: MatchResult = resolve(session, row, supplier_id=supplier_id)
    counters.by_source[res.source] = counters.by_source.get(res.source, 0) + 1

    table = CATEGORY_TO_TABLE[row.our_category]
    category = _category_of_component(session, table)

    # Обработка по source ------------------------------------------------

    if res.source in (EXISTING, MATCH_MPN, MATCH_GTIN):
        # Чистый match — просто UPSERT в supplier_prices.
        _upsert_price(
            session,
            supplier_id=supplier_id, category=category,
            component_id=res.component_id,
            supplier_sku=row.supplier_sku or None,
            price=row.price, currency=row.currency,
            stock_qty=row.stock, transit_qty=row.transit,
            raw_name=row.name,
        )
        counters.updated += 1
        return

    if res.source in (AMBIG_MPN, AMBIG_GTIN):
        # Выбираем первого кандидата (min id), пишем supplier_prices,
        # и параллельно заводим строку в unmapped на проверку админом.
        _upsert_price(
            session,
            supplier_id=supplier_id, category=category,
            component_id=res.component_id,
            supplier_sku=row.supplier_sku or None,
            price=row.price, currency=row.currency,
            stock_qty=row.stock, transit_qty=row.transit,
            raw_name=row.name,
        )
        counters.updated += 1
        all_ids = ",".join(str(i) for i in res.ambiguous_ids)
        note = (
            f"AMBIGUOUS {'MPN' if res.source == AMBIG_MPN else 'GTIN'}: "
            f"кандидатов {len(res.ambiguous_ids)} "
            f"(ids: {all_ids}), автоматически привязан к id={res.component_id}. "
            f"Проверьте и при необходимости переназначьте."
        )
        _upsert_unmapped(
            session,
            supplier_id=supplier_id, row=row,
            status="pending", notes=note,
            resolved_component_id=res.component_id,
        )
        counters.unmapped_created += 1
        counters.unmapped_ambiguous += 1
        return

    # res.source == NO_MATCH: создаём скелет + supplier_prices + unmapped.
    new_id = _create_skeleton(session, table, row)
    counters.added += 1
    _upsert_price(
        session,
        supplier_id=supplier_id, category=category,
        component_id=new_id,
        supplier_sku=row.supplier_sku or None,
        price=row.price, currency=row.currency,
        stock_qty=row.stock, transit_qty=row.transit,
        raw_name=row.name,
    )
    note = (
        "NO_MATCH: не найдено совпадений по MPN/GTIN. "
        f"Создан новый компонент id={new_id}. "
        "Проверьте — возможно, это дубликат уже существующего товара."
    )
    _upsert_unmapped(
        session,
        supplier_id=supplier_id, row=row,
        status="created_new", notes=note,
        resolved_component_id=new_id,
    )
    counters.unmapped_created += 1
    counters.unmapped_new += 1


def load_price(
    filepath: str,
    *,
    supplier_key: str | None = None,
    loader: BasePriceLoader | None = None,
) -> dict:
    """Главная точка входа.

    Если передан готовый loader — используем его (это удобно для тестов).
    Иначе supplier_key (строка 'ocs'/'merlion'/'treolan') разрешается
    через фабрику get_loader().
    """
    # Импорт локальный — иначе циклическая зависимость с __init__.py.
    from app.services.price_loaders import get_loader

    if loader is None:
        if not supplier_key:
            raise ValueError("Нужно указать supplier_key или передать loader.")
        loader = get_loader(supplier_key)

    supplier_name = loader.supplier_name
    counters = Counters()
    started_at = time.monotonic()

    session = SessionLocal()
    try:
        supplier_id = _get_or_create_supplier(session, supplier_name)

        # 11.4: фиксируем активные SKU поставщика ДО обработки прайса —
        # это базис для disappeared-детекции. Если новая загрузка не
        # упомянет какой-то из этих SKU, мы пометим его stock=0/transit=0.
        active_skus_before = _load_active_skus(session, supplier_id)
        seen_skus: set[str] = set()

        rows_iter: Iterator[PriceRow] = loader.iter_rows(filepath)
        for row in rows_iter:
            counters.total_rows += 1

            savepoint = session.begin_nested()
            try:
                _process_row(
                    session,
                    supplier_id=supplier_id, row=row, counters=counters,
                    seen_skus=seen_skus,
                )
                savepoint.commit()
            except Exception as exc:
                savepoint.rollback()
                logger.error(
                    "%s строка %s (supplier_sku=%r): ошибка — %s",
                    supplier_name, row.row_number, row.supplier_sku, exc,
                )
                counters.errors += 1

        # 11.4: считаем «исчезнувшие» позиции — те, что были активны и
        # не попали в текущий прайс. Применяем ТОЛЬКО когда загрузка
        # завершится статусом success/partial. Status решается ниже в
        # _record_upload — поэтому сначала вычисляем status «по сухому»
        # тем же правилом и применяем disappeared под защитой savepoint,
        # чтобы отдельная ошибка SQL-апдейта не повалила всю загрузку.
        rows_matched = counters.updated + counters.added
        will_be_failed = (
            rows_matched == 0
            and (counters.processed > 0 or counters.total_rows > 0)
        )
        missing_skus = active_skus_before - seen_skus
        if missing_skus and not will_be_failed:
            disappeared_savepoint = session.begin_nested()
            try:
                _mark_disappeared(
                    session,
                    supplier_id=supplier_id,
                    missing_skus=missing_skus,
                    counters=counters,
                )
                disappeared_savepoint.commit()
            except Exception as exc:
                disappeared_savepoint.rollback()
                logger.error(
                    "%s: не удалось пометить исчезнувшие позиции — %s",
                    supplier_name, exc,
                )

        duration = round(time.monotonic() - started_at, 3)
        # 11.2: собираем полный отчёт ДО _record_upload — чтобы он лёг в
        # report_json. duration_seconds — вместе со счётчиками.
        # 11.4: добавлены disappeared-счётчики.
        report = {
            "supplier":              supplier_name,
            "filename":              os.path.basename(filepath),
            "total_rows":            counters.total_rows,
            "processed":             counters.processed,
            "updated":               counters.updated,
            "added":                 counters.added,
            "skipped":               counters.skipped,
            "errors":                counters.errors,
            "unmapped_ambiguous":    counters.unmapped_ambiguous,
            "unmapped_new":          counters.unmapped_new,
            "disappeared":           counters.disappeared,
            "disappeared_skus":      list(counters.disappeared_skus),
            "disappeared_truncated": counters.disappeared_truncated,
            "by_source":             dict(counters.by_source),
            "duration_seconds":      duration,
        }
        upload_id, status = _record_upload(
            session,
            supplier_id=supplier_id,
            filename=os.path.basename(filepath),
            counters=counters,
            report=report,
        )
        report["status"] = status
        report["upload_id"] = upload_id
        session.commit()

    except Exception as exc:
        session.rollback()
        _save_failed_upload(
            filepath, supplier_name, counters,
            error_message=f"{type(exc).__name__}: {exc}",
        )
        session.close()
        raise

    session.close()

    result = {
        "supplier":   supplier_name,
        "total_rows": counters.total_rows,
        "processed":  counters.processed,
        "updated":    counters.updated,
        "added":      counters.added,
        "skipped":    counters.skipped,
        "errors":     counters.errors,
        "unmapped_ambiguous": counters.unmapped_ambiguous,
        "unmapped_new":       counters.unmapped_new,
        "disappeared":           counters.disappeared,
        "disappeared_skus":      list(counters.disappeared_skus),
        "disappeared_truncated": counters.disappeared_truncated,
        "by_source":  dict(counters.by_source),
        "status":     status,
        "upload_id":  upload_id,
        "duration_seconds": duration,
    }

    # Авто-хук обогащения OpenAI — как и раньше, не бросает исключения.
    # Флаг OPENAI_ENRICH_AUTO_HOOK в .env, по умолчанию выключен.
    try:
        from app.services.enrichment.openai_search.hooks import auto_enrich_new_skus
        auto_enrich_new_skus(added_new=counters.added)
    except Exception as exc:
        logger.warning("auto-hook OpenAI пропущен из-за ошибки: %s", exc)

    return result
