"""Защита от регрессии: supplier_prices.raw_name должен оставаться NULLable.

Этап 6 слияния перенёс из QT 943 строки supplier_prices с category in
('printer','mfu') и raw_name=NULL — это единственный способ помечать
«исторические QT-строки» внутри смешанной таблицы. Если кто-нибудь в
будущем добавит миграцию `ALTER COLUMN raw_name SET NOT NULL`, импорт
QT-данных и весь набор печатных адаптеров (где raw_name не вычисляется
из колонок прайса) сломается.

Тест на тестовой БД повторяет физический контракт прода:
  - проверяет, что INFORMATION_SCHEMA.COLUMNS.is_nullable = 'YES';
  - вставляет реальные строки supplier_prices с raw_name=NULL и
    category in ('printer','mfu') — INSERT не должен падать.
"""

from __future__ import annotations

from sqlalchemy import text


def test_supplier_prices_raw_name_column_is_nullable(db_session) -> None:
    """Schema-level контракт: raw_name в supplier_prices — nullable."""
    is_nullable = db_session.execute(
        text(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'supplier_prices' "
            "  AND column_name = 'raw_name'"
        )
    ).scalar()
    assert is_nullable == "YES", (
        f"Колонка supplier_prices.raw_name стала NOT NULL "
        f"(is_nullable={is_nullable!r}). Это сломает импорт QT-данных "
        f"Этапа 6 слияния (943 строки с raw_name=NULL для printer/mfu)."
    )


def test_supplier_prices_accepts_null_raw_name_for_printer_mfu(db_session) -> None:
    """Behavioral: вставка supplier_prices с raw_name=NULL для printer/mfu
    проходит без ошибок и читается обратно как NULL.

    Имитирует ровно тот сценарий, что прошёл при переносе QT-данных
    `scripts/migrate_qt_data_to_printers_mfu.py`: INSERT с явным
    raw_name=NULL по category in ('printer','mfu').
    """
    sup_id = db_session.execute(
        text(
            "INSERT INTO suppliers (name) VALUES ('TestSupplierRawNameNull') "
            "RETURNING id"
        )
    ).scalar()

    # Skeleton printers_mfu — нужен для FK подобия (FK на printers_mfu(id)
    # из supplier_prices не объявлен в схеме, но идентификатор
    # component_id всё равно должен быть валидным int).
    pmfu_id = db_session.execute(
        text(
            "INSERT INTO printers_mfu (sku, mpn, brand, name, category) "
            "VALUES ('test:raw-null-1', 'RAW-NULL-1', 'TestBrand', "
            "        'Test printer skeleton', 'printer') "
            "RETURNING id"
        )
    ).scalar()
    pmfu_id_2 = db_session.execute(
        text(
            "INSERT INTO printers_mfu (sku, mpn, brand, name, category) "
            "VALUES ('test:raw-null-2', 'RAW-NULL-2', 'TestBrand', "
            "        'Test mfu skeleton', 'mfu') "
            "RETURNING id"
        )
    ).scalar()

    for category, comp_id in (("printer", pmfu_id), ("mfu", pmfu_id_2)):
        db_session.execute(
            text(
                "INSERT INTO supplier_prices "
                "  (supplier_id, category, component_id, supplier_sku, "
                "   price, currency, stock_qty, transit_qty, raw_name) "
                "VALUES (:sid, :cat, :cid, :sku, :price, 'RUB', 0, 0, NULL)"
            ),
            {
                "sid": sup_id, "cat": category, "cid": comp_id,
                "sku": f"sku-{category}", "price": 100,
            },
        )
    db_session.commit()

    rows = db_session.execute(
        text(
            "SELECT category, raw_name FROM supplier_prices "
            "WHERE supplier_id = :sid "
            "ORDER BY category"
        ),
        {"sid": sup_id},
    ).all()

    assert len(rows) == 2
    cats = {r.category: r.raw_name for r in rows}
    assert cats == {"mfu": None, "printer": None}, (
        f"Ожидалось raw_name=NULL для printer/mfu, получено {cats!r}"
    )
