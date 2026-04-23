# Админский веб-интерфейс /admin/mapping.
#
# Проверяем:
#   - admin видит список, manager получает 403;
#   - merge переносит supplier_prices на выбранный компонент и удаляет
#     ошибочно созданный скелет;
#   - confirm_as_new меняет статус, компонент остаётся;
#   - defer — ничего не меняет;
#   - детальная страница показывает кандидатов в нужном порядке.

from __future__ import annotations

from sqlalchemy import text as _t

from tests.test_web.conftest import extract_csrf


# ---- хелперы -----------------------------------------------------------


def _insert_supplier(session, name: str) -> int:
    row = session.execute(_t(
        "INSERT INTO suppliers (name, is_active) VALUES (:n, TRUE) "
        "ON CONFLICT (name) DO UPDATE SET is_active = suppliers.is_active "
        "RETURNING id"
    ), {"n": name}).scalar()
    session.commit()
    return int(row)


def _insert_cpu(session, *, model, manufacturer="AMD", sku, gtin=None) -> int:
    row = session.execute(_t(
        "INSERT INTO cpus "
        "  (model, manufacturer, sku, gtin, socket, cores, threads, "
        "   base_clock_ghz, turbo_clock_ghz, tdp_watts, has_integrated_graphics, "
        "   memory_type, package_type) "
        "VALUES (:m, :mfg, :sku, :gtin, 'LGA1700', 6, 12, 3.0, 4.0, 65, FALSE, "
        "        'DDR5', 'BOX') "
        "RETURNING id"
    ), {"m": model, "mfg": manufacturer, "sku": sku, "gtin": gtin}).scalar()
    session.commit()
    return int(row)


def _insert_price(session, *, supplier_id: int, component_id: int,
                  supplier_sku: str, price=1000.0):
    session.execute(_t(
        "INSERT INTO supplier_prices "
        "  (supplier_id, category, component_id, supplier_sku, price, currency, "
        "   stock_qty, transit_qty) "
        "VALUES (:sid, 'cpu', :cid, :ssku, :price, 'RUB', 1, 0)"
    ), {"sid": supplier_id, "cid": component_id, "ssku": supplier_sku, "price": price})
    session.commit()


def _insert_unmapped(session, *, supplier_id: int, supplier_sku: str,
                     raw_name: str, status: str,
                     guessed_category: str = "cpu",
                     resolved_component_id: int | None = None,
                     brand: str | None = "AMD",
                     mpn: str | None = None, gtin: str | None = None) -> int:
    row = session.execute(_t(
        "INSERT INTO unmapped_supplier_items "
        "  (supplier_id, supplier_sku, raw_category, guessed_category, "
        "   brand, mpn, gtin, raw_name, price, currency, stock, transit, "
        "   status, resolved_component_id) "
        "VALUES (:sid, :ssku, :raw_cat, :gcat, :brand, :mpn, :gtin, :name, "
        "        1000, 'RUB', 1, 0, :st, :rcid) "
        "RETURNING id"
    ), {
        "sid": supplier_id, "ssku": supplier_sku,
        "raw_cat": "Комплектующие->Процессоры",
        "gcat": guessed_category,
        "brand": brand, "mpn": mpn, "gtin": gtin, "name": raw_name,
        "st": status, "rcid": resolved_component_id,
    }).scalar()
    session.commit()
    return int(row)


def _cleanup_fixtures_for_test(db_session):
    """test_web/conftest автоматически чистит только users/projects/queries/.
    Таблицы компонентов и unmapped при каждом тесте делать пустыми не обязательно
    в test_web, но нам — нужно: fixture autouse из test_price_loaders здесь
    не срабатывает (другая директория)."""
    db_session.execute(_t(
        "TRUNCATE TABLE unmapped_supplier_items, supplier_prices, "
        "cpus, motherboards, rams, gpus, storages, cases, psus, coolers, "
        "suppliers RESTART IDENTITY CASCADE"
    ))
    db_session.commit()


# ---- access -----------------------------------------------------------


def test_admin_sees_mapping_list(admin_client, db_session):
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")
    _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="X-1",
        raw_name="Ryzen 5 7600 6-core", status="created_new",
    )

    r = admin_client.get("/admin/mapping")
    assert r.status_code == 200
    assert "Ryzen 5 7600 6-core" in r.text
    # Счётчик активных строк видится в заголовке.
    assert "На сопоставлении: 1" in r.text


def test_manager_cannot_access_mapping(manager_client, db_session):
    _cleanup_fixtures_for_test(db_session)
    r = manager_client.get("/admin/mapping")
    assert r.status_code == 403


# ---- merge ------------------------------------------------------------


def test_merge_moves_supplier_prices_and_deletes_skeleton(admin_client, db_session):
    """Сценарий created_new: admin объединяет запись с другим компонентом;
    скелет, созданный автоматически, удаляется; supplier_prices переезжает."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")

    # Реальный компонент (на который админ будет объединять)
    real_cpu = _insert_cpu(
        db_session, model="AMD Ryzen 5 7600", sku="100-000001591",
    )
    # Скелет — будто orchestrator его только что создал для новой строки
    skeleton = _insert_cpu(
        db_session, model="Ryzen 5 7600", sku="TRX-SRMBG-NEW",
    )
    # supplier_prices привязан к скелету
    _insert_price(db_session, supplier_id=sid, component_id=skeleton,
                  supplier_sku="TRX-SRMBG-NEW")
    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="TRX-SRMBG-NEW",
        raw_name="Ryzen 5 7600 6-core",
        status="created_new", resolved_component_id=skeleton,
    )

    r = admin_client.get("/admin/mapping")
    token = extract_csrf(r.text)

    r = admin_client.post(
        f"/admin/mapping/{unmapped_id}/merge",
        data={"target_component_id": real_cpu, "csrf_token": token},
    )
    assert r.status_code == 302

    # supplier_prices теперь на real_cpu.
    row = db_session.execute(_t(
        "SELECT component_id FROM supplier_prices "
        "WHERE supplier_id = :sid AND category = 'cpu'"
    ), {"sid": sid}).first()
    assert row is not None
    assert int(row.component_id) == real_cpu

    # Скелет удалён.
    exists = db_session.execute(_t(
        "SELECT id FROM cpus WHERE id = :id"
    ), {"id": skeleton}).first()
    assert exists is None

    # unmapped → merged.
    row = db_session.execute(_t(
        "SELECT status, resolved_component_id, resolved_by "
        "FROM unmapped_supplier_items WHERE id = :id"
    ), {"id": unmapped_id}).first()
    assert row.status == "merged"
    assert int(row.resolved_component_id) == real_cpu
    assert row.resolved_by is not None


def test_merge_with_same_target_keeps_component(admin_client, db_session):
    """Если админ выбирает тот же компонент, что уже привязан — скелет
    не удаляется, статус становится merged."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Merlion")
    skeleton = _insert_cpu(db_session, model="Новый CPU", sku="NEW-1")
    _insert_price(db_session, supplier_id=sid, component_id=skeleton,
                  supplier_sku="S-1")
    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="S-1",
        raw_name="Новый CPU", status="created_new",
        resolved_component_id=skeleton,
    )

    r = admin_client.get("/admin/mapping")
    token = extract_csrf(r.text)
    r = admin_client.post(
        f"/admin/mapping/{unmapped_id}/merge",
        data={"target_component_id": skeleton, "csrf_token": token},
    )
    assert r.status_code == 302

    # Компонент остался.
    assert db_session.execute(_t("SELECT id FROM cpus WHERE id = :id"),
                              {"id": skeleton}).first() is not None
    # Статус — merged.
    row = db_session.execute(_t(
        "SELECT status FROM unmapped_supplier_items WHERE id = :id"
    ), {"id": unmapped_id}).first()
    assert row.status == "merged"


def test_merge_for_pending_ambiguous_moves_to_other_candidate(admin_client, db_session):
    """Сценарий pending (ambiguous): supplier_prices был привязан к
    первому кандидату, админ выбирает второго — supplier_prices
    переезжает. Оба компонента остаются (они реальные, не скелеты)."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")
    cpu_a = _insert_cpu(db_session, model="CPU A", sku="DUP-1")
    cpu_b = _insert_cpu(db_session, model="CPU B", sku="DUP-1")

    _insert_price(db_session, supplier_id=sid, component_id=cpu_a,
                  supplier_sku="S-AMB")
    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="S-AMB",
        raw_name="Дубликат", status="pending",
        resolved_component_id=cpu_a,
    )

    r = admin_client.get("/admin/mapping")
    token = extract_csrf(r.text)
    r = admin_client.post(
        f"/admin/mapping/{unmapped_id}/merge",
        data={"target_component_id": cpu_b, "csrf_token": token},
    )
    assert r.status_code == 302

    # supplier_prices теперь на cpu_b.
    row = db_session.execute(_t(
        "SELECT component_id FROM supplier_prices WHERE supplier_id = :sid"
    ), {"sid": sid}).first()
    assert int(row.component_id) == cpu_b

    # Оба CPU остались (pending не создаёт скелетов).
    assert db_session.execute(_t("SELECT id FROM cpus WHERE id = :id"),
                              {"id": cpu_a}).first() is not None
    assert db_session.execute(_t("SELECT id FROM cpus WHERE id = :id"),
                              {"id": cpu_b}).first() is not None


# ---- confirm_as_new ---------------------------------------------------


def test_confirm_as_new_changes_status_only(admin_client, db_session):
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Merlion")
    skeleton = _insert_cpu(db_session, model="Уникальный", sku="UNIQ-1")
    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="S-U",
        raw_name="Уникальный", status="created_new",
        resolved_component_id=skeleton,
    )

    r = admin_client.get("/admin/mapping")
    token = extract_csrf(r.text)
    r = admin_client.post(
        f"/admin/mapping/{unmapped_id}/confirm_as_new",
        data={"csrf_token": token},
    )
    assert r.status_code == 302

    row = db_session.execute(_t(
        "SELECT status, resolved_component_id "
        "FROM unmapped_supplier_items WHERE id = :id"
    ), {"id": unmapped_id}).first()
    assert row.status == "confirmed_new"
    # Компонент остался.
    assert db_session.execute(_t(
        "SELECT id FROM cpus WHERE id = :id"
    ), {"id": skeleton}).first() is not None


# ---- defer ------------------------------------------------------------


def test_defer_does_not_change_anything(admin_client, db_session):
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Merlion")
    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="S-D",
        raw_name="Отложенный", status="pending",
    )
    r = admin_client.get("/admin/mapping")
    token = extract_csrf(r.text)
    r = admin_client.post(
        f"/admin/mapping/{unmapped_id}/defer",
        data={"csrf_token": token},
    )
    assert r.status_code == 302

    row = db_session.execute(_t(
        "SELECT status FROM unmapped_supplier_items WHERE id = :id"
    ), {"id": unmapped_id}).first()
    assert row.status == "pending"


# ---- candidates ordering ---------------------------------------------


def test_detail_page_shows_matching_candidates(admin_client, db_session):
    """Детальная страница выдаёт кандидатов, отсортированных по
    релевантности (точное совпадение номера модели — сверху)."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")
    # Два похожих: i5-13400 (точное совпадение) и i5-13400F (близкий).
    _insert_cpu(db_session, model="Intel Core i5-13400",  manufacturer="Intel",
                sku="CM8071512400-NOF")
    exact = _insert_cpu(db_session, model="Intel Core i5-13400F", manufacturer="Intel",
                        sku="CM8071512400F")
    # И посторонний — не должен подняться.
    _insert_cpu(db_session, model="AMD Ryzen 5 7600", manufacturer="AMD", sku="100-000001591")

    # raw_name без лишних чисел (2.5GHz/12Mb и т. п.), чтобы токены были
    # ровно ['I5', '13400F'] — иначе ILIKE по каждому токену сразу
    # отсечёт кандидатов, у которых в model нет «2» или «GHZ».
    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SRMBG",
        raw_name="Intel Core i5-13400F",
        status="created_new", brand="Intel",
    )
    r = admin_client.get(f"/admin/mapping/{unmapped_id}")
    assert r.status_code == 200
    # exact должен быть в HTML.
    assert f'value="{exact}"' in r.text
    # И как минимум первая radio-кнопка должна быть отмечена checked —
    # сначала идёт i5-13400F (exact match по suffix F).
    # Простая проверка: позиция exact в строке раньше позиции другого кандидата.
    body = r.text
    pos_exact = body.find(f'value="{exact}"')
    assert pos_exact > -1
    # Ryzen не должен попасть в кандидаты (разные токены).
    assert "AMD Ryzen 5 7600" not in body
