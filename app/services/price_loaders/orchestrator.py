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

from __future__ import annotations

import logging
import os
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
    # Карта source → counter (для отладки/отчёта).
    by_source: dict[str, int] = field(default_factory=dict)


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
) -> None:
    session.execute(
        text(
            "INSERT INTO supplier_prices "
            "    (supplier_id, category, component_id, supplier_sku, "
            "     price, currency, stock_qty, transit_qty, updated_at) "
            "VALUES "
            "    (:supplier_id, :category, :component_id, :supplier_sku, "
            "     :price, :currency, :stock_qty, :transit_qty, NOW()) "
            "ON CONFLICT (supplier_id, category, component_id) DO UPDATE SET "
            "    supplier_sku = EXCLUDED.supplier_sku, "
            "    price        = EXCLUDED.price, "
            "    currency     = EXCLUDED.currency, "
            "    stock_qty    = EXCLUDED.stock_qty, "
            "    transit_qty  = EXCLUDED.transit_qty, "
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

    row = session.execute(
        text(
            "INSERT INTO price_uploads "
            "    (supplier_id, filename, rows_total, rows_matched, rows_unmatched, status, notes) "
            "VALUES "
            "    (:supplier_id, :filename, :rows_total, :rows_matched, :rows_unmatched, :status, :notes) "
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
        },
    ).first()
    return int(row.id), status


def _save_failed_upload(filepath: str, supplier_name: str, counters: Counters) -> None:
    session = SessionLocal()
    try:
        row = session.execute(
            text("SELECT id FROM suppliers WHERE name = :n LIMIT 1"),
            {"n": supplier_name},
        ).first()
        if row is None:
            return
        session.execute(
            text(
                "INSERT INTO price_uploads "
                "    (supplier_id, filename, rows_total, rows_matched, rows_unmatched, status, notes) "
                "VALUES "
                "    (:supplier_id, :filename, :rows_total, 0, :rows_unmatched, 'failed', :notes)"
            ),
            {
                "supplier_id":    row.id,
                "filename":       os.path.basename(filepath),
                "rows_total":     counters.total_rows,
                "rows_unmatched": counters.skipped + counters.errors,
                "notes":          "Критическая ошибка при загрузке",
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
) -> None:
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

    session = SessionLocal()
    try:
        supplier_id = _get_or_create_supplier(session, supplier_name)

        rows_iter: Iterator[PriceRow] = loader.iter_rows(filepath)
        for row in rows_iter:
            counters.total_rows += 1

            savepoint = session.begin_nested()
            try:
                _process_row(session, supplier_id=supplier_id, row=row, counters=counters)
                savepoint.commit()
            except Exception as exc:
                savepoint.rollback()
                logger.error(
                    "%s строка %s (supplier_sku=%r): ошибка — %s",
                    supplier_name, row.row_number, row.supplier_sku, exc,
                )
                counters.errors += 1

        upload_id, status = _record_upload(
            session,
            supplier_id=supplier_id,
            filename=os.path.basename(filepath),
            counters=counters,
        )
        session.commit()

    except Exception:
        session.rollback()
        _save_failed_upload(filepath, supplier_name, counters)
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
        "by_source":  dict(counters.by_source),
        "status":     status,
        "upload_id":  upload_id,
    }

    # Авто-хук обогащения OpenAI — как и раньше, не бросает исключения.
    # Флаг OPENAI_ENRICH_AUTO_HOOK в .env, по умолчанию выключен.
    try:
        from app.services.enrichment.openai_search.hooks import auto_enrich_new_skus
        auto_enrich_new_skus(added_new=counters.added)
    except Exception as exc:
        logger.warning("auto-hook OpenAI пропущен из-за ошибки: %s", exc)

    return result
