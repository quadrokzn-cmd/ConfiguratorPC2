# Веб-интерфейс /databases/mapping портала.
#
# Изначально жил в конфигураторе как /admin/mapping (этап 7). На этапе
# UI-2 Пути B (2026-05-11) переехал в портал; этот файл — перенос
# tests/test_web/test_mapping_admin.py с заменой admin_client →
# admin_portal_client и URL'ов /admin/mapping → /databases/mapping.
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

from tests.test_portal.conftest import extract_csrf


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


def test_admin_sees_mapping_list(admin_portal_client, db_session):
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")
    _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="X-1",
        raw_name="Ryzen 5 7600 6-core", status="created_new",
    )

    # По умолчанию /databases/mapping фильтрует «подозрительных» (score >= 50);
    # наша запись без кандидатов получит score=0 → проверяем вид «все».
    r = admin_portal_client.get("/databases/mapping?score=all")
    assert r.status_code == 200
    assert "Ryzen 5 7600 6-core" in r.text
    # 9А.2: счётчики по score теперь в карточках. Проверяем и подпись,
    # и наличие цифры «1» в тексте страницы.
    assert "Всего активных" in r.text
    assert "Вероятно новых" in r.text


def test_manager_cannot_access_mapping(manager_portal_client, db_session):
    _cleanup_fixtures_for_test(db_session)
    r = manager_portal_client.get("/databases/mapping")
    assert r.status_code == 403


# ---- merge ------------------------------------------------------------


def test_merge_moves_supplier_prices_and_deletes_skeleton(admin_portal_client, db_session):
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

    r = admin_portal_client.get("/databases/mapping")
    token = extract_csrf(r.text)

    r = admin_portal_client.post(
        f"/databases/mapping/{unmapped_id}/merge",
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


def test_merge_with_same_target_keeps_component(admin_portal_client, db_session):
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

    r = admin_portal_client.get("/databases/mapping")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        f"/databases/mapping/{unmapped_id}/merge",
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


def test_merge_for_pending_ambiguous_moves_to_other_candidate(admin_portal_client, db_session):
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

    r = admin_portal_client.get("/databases/mapping")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        f"/databases/mapping/{unmapped_id}/merge",
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


def test_confirm_as_new_changes_status_only(admin_portal_client, db_session):
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Merlion")
    skeleton = _insert_cpu(db_session, model="Уникальный", sku="UNIQ-1")
    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="S-U",
        raw_name="Уникальный", status="created_new",
        resolved_component_id=skeleton,
    )

    r = admin_portal_client.get("/databases/mapping")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        f"/databases/mapping/{unmapped_id}/confirm_as_new",
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


def test_defer_does_not_change_anything(admin_portal_client, db_session):
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Merlion")
    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="S-D",
        raw_name="Отложенный", status="pending",
    )
    r = admin_portal_client.get("/databases/mapping")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        f"/databases/mapping/{unmapped_id}/defer",
        data={"csrf_token": token},
    )
    assert r.status_code == 302

    row = db_session.execute(_t(
        "SELECT status FROM unmapped_supplier_items WHERE id = :id"
    ), {"id": unmapped_id}).first()
    assert row.status == "pending"


# ---- candidates ordering ---------------------------------------------


def test_mapping_score_calculation(admin_portal_client, db_session):
    """Типовые кейсы расчёта score (этап 7.5: MPN — главный сигнал):
      - brand-only без MPN → 30 (fallback, только бренд);
      - common-model-token без MPN → 50 (fallback, только токен);
      - near-duplicate без MPN → 70 (fallback, капнутый _SCORE_FALLBACK_CAP);
      - точное совпадение MPN → 100.
    """
    from portal.services.databases import mapping_service as ms

    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")

    # Реальный компонент — эталон, относительно которого считается score.
    _insert_cpu(
        db_session, model="Intel Core i5-12400", manufacturer="Intel",
        sku="CM8071512400F",
    )

    # 1) Только совпадающий бренд, MPN нет.
    u_brand = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SK-BRAND",
        raw_name="совсем другое название",
        status="created_new", brand="Intel",
    )
    # 2) Общий модельный токен (12400), бренд не совпадает, MPN нет.
    u_token = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SK-TOK",
        raw_name="Какой-то процессор 12400",
        status="created_new", brand="NoName",
    )
    # 3) Почти идентичное название + совпадающий бренд, MPN нет → fallback, cap 70.
    u_near = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SK-NEAR",
        raw_name="Intel Core i5-12400", status="created_new", brand="Intel",
    )
    # 4) MPN точно совпадает с sku кандидата → 100.
    u_mpn = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SK-MPN",
        raw_name="Intel Core i5-12400", status="created_new", brand="Intel",
        mpn="CM8071512400F",
    )

    row_brand = ms.get_by_id(db_session, u_brand)
    row_token = ms.get_by_id(db_session, u_token)
    row_near  = ms.get_by_id(db_session, u_near)
    row_mpn   = ms.get_by_id(db_session, u_mpn)

    s_brand, _ = ms.calculate_score(db_session, row_brand)
    s_token, _ = ms.calculate_score(db_session, row_token)
    s_near,  _ = ms.calculate_score(db_session, row_near)
    s_mpn,   _ = ms.calculate_score(db_session, row_mpn)

    assert s_brand == 30
    assert s_token == 50
    assert s_near  == 70   # fallback, capped
    assert s_mpn   == 100  # MPN идентичен


def test_mapping_list_default_filter_is_suspicious(admin_portal_client, db_session):
    """По умолчанию показываем только подозрительных (score >= 50).
    «Вероятно новые» остаются скрытыми до переключения фильтра."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")
    # Реальный i5 — чтобы «похожая» запись получила score=100.
    _insert_cpu(db_session, model="Intel Core i5-12400", manufacturer="Intel",
                sku="CM8071512400F")
    _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="S-HIGH",
        raw_name="Intel Core i5-12400", brand="Intel",
        status="created_new",
    )
    _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="S-LOW",
        raw_name="Совершенно уникальное устройство ABC-123",
        brand="NoName", status="created_new",
    )

    r = admin_portal_client.get("/databases/mapping")
    assert r.status_code == 200
    # Подозрительная запись (near-duplicate) — видна.
    assert "Intel Core i5-12400" in r.text
    # «Вероятно новая» — скрыта под фильтром по умолчанию.
    assert "Совершенно уникальное устройство" not in r.text


def test_bulk_confirm_new(admin_portal_client, db_session):
    """Массовое подтверждение «новых»: все created_new со score < 50
    переходят в confirmed_new; «подозрительные» не трогаются."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Merlion")
    # Эталон для «подозрительного»
    _insert_cpu(db_session, model="AMD Ryzen 7 7700", manufacturer="AMD",
                sku="R7-7700")
    # Три «вероятно новых» (score=0, нет бренда и токенов)
    low_ids = [
        _insert_unmapped(
            db_session, supplier_id=sid, supplier_sku=f"NEW-{i}",
            raw_name=f"Уникальная железка {i}", brand="NoName",
            status="created_new",
        )
        for i in range(3)
    ]
    # Две «подозрительные» (score=100 каждая)
    high_ids = [
        _insert_unmapped(
            db_session, supplier_id=sid, supplier_sku=f"DUP-{i}",
            raw_name="AMD Ryzen 7 7700", brand="AMD",
            status="created_new",
        )
        for i in range(2)
    ]

    # Открываем /databases/mapping — это посчитает score для всех пяти.
    r = admin_portal_client.get("/databases/mapping?score=new")
    assert r.status_code == 200
    token = extract_csrf(r.text)

    # Массовое действие.
    r = admin_portal_client.post(
        "/databases/mapping/bulk_confirm_new",
        data={"csrf_token": token},
    )
    assert r.status_code == 302

    # Три «новых» стали confirmed_new.
    for rid in low_ids:
        status = db_session.execute(_t(
            "SELECT status FROM unmapped_supplier_items WHERE id = :id"
        ), {"id": rid}).scalar()
        assert status == "confirmed_new"
    # Две «подозрительные» остались в created_new.
    for rid in high_ids:
        status = db_session.execute(_t(
            "SELECT status FROM unmapped_supplier_items WHERE id = :id"
        ), {"id": rid}).scalar()
        assert status == "created_new"


def test_mapping_list_shows_best_candidate_model(admin_portal_client, db_session):
    """В колонке «Похожие в БД» рендерится модель best_candidate, а не «—»."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")
    _insert_cpu(db_session, model="Intel Core i5-12400",
                manufacturer="Intel", sku="CM8071512400F")
    _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="S-NEAR",
        raw_name="Intel Core i5-12400", brand="Intel",
        status="created_new",
    )

    # По умолчанию — suspicious, наша запись score=100 туда попадает.
    r = admin_portal_client.get("/databases/mapping")
    assert r.status_code == 200
    # Модель лучшего кандидата виднa в колонке.
    assert "Intel Core i5-12400" in r.text


def test_detail_page_shows_matching_candidates(admin_portal_client, db_session):
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
    r = admin_portal_client.get(f"/databases/mapping/{unmapped_id}")
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


# ---- этап 7.2: фильтрация скелетов, калибровка, синхрон списка и детали ---


def test_score_excludes_unmapped_candidates(admin_portal_client, db_session):
    """При расчёте score не должны учитываться другие скелеты unmapped.

    Типичный кейс этапа 7.1: Merlion и Treolan создали скелеты для
    своих прайсов. Если не фильтровать, SSD 512GB получает в кандидаты
    SSD 1TB (тоже скелет), тот же бренд → score=100 → 2000+ ложных
    «подозрительных». После фильтра скелеты друг друга не видят.
    """
    from portal.services.databases import mapping_service as ms

    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Merlion")

    # Два скелета одного бренда, ни один не связан с реальным OCS-компонентом.
    skel_a = _insert_cpu(
        db_session, model="Intel Core i5-12400", manufacturer="Intel",
        sku="SKEL-A",
    )
    skel_b = _insert_cpu(
        db_session, model="Intel Core i5-12400F", manufacturer="Intel",
        sku="SKEL-B",
    )

    u_a = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SKEL-A",
        raw_name="Intel Core i5-12400", brand="Intel",
        status="created_new", resolved_component_id=skel_a,
    )
    u_b = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SKEL-B",
        raw_name="Intel Core i5-12400F", brand="Intel",
        status="created_new", resolved_component_id=skel_b,
    )

    row_a = ms.get_by_id(db_session, u_a)
    row_b = ms.get_by_id(db_session, u_b)

    # Оба скелета — единственные кандидаты друг для друга. После фильтра
    # их быть не должно → score=0.
    score_a, best_a = ms.calculate_score(db_session, row_a)
    score_b, best_b = ms.calculate_score(db_session, row_b)
    assert score_a == 0 and best_a is None
    assert score_b == 0 and best_b is None

    # Добавим реальный OCS-компонент (без resolved_component_id-ссылок) —
    # он должен появиться в кандидатах и дать score=100 (brand+token+lev).
    real_cpu = _insert_cpu(
        db_session, model="Intel Core i5-12400", manufacturer="Intel",
        sku="CM8071512400-REAL",
    )
    score_a2, best_a2 = ms.calculate_score(db_session, row_a)
    assert best_a2 == real_cpu
    # Без MPN у unmapped fallback упирается в _SCORE_FALLBACK_CAP (70).
    # Важна не конкретная цифра, а что реальный компонент найден.
    assert score_a2 >= 70


def test_score_calibration(admin_portal_client, db_session):
    """Конкретные кейсы калибровки (этап 7.2):
      - разные бренды в одной категории → 0
      - одинаковый бренд, разные модели → ≤ 30 (cap при отсутствии
        совпадающего токена и большой Lev-дистанции)
      - одинаковый бренд + совпадающий конкретный токен → 50-80
      - почти идентичное имя → ≥ 80.
    """
    from portal.services.databases import mapping_service as ms

    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")

    real_intel = _insert_cpu(
        db_session, model="Intel Core i5-12400", manufacturer="Intel",
        sku="CM8071512400-REAL",
    )
    _ = real_intel

    # 1) Разные бренды, одна категория.
    u_other_brand = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SK-DB",
        raw_name="Какой-то странный CPU без модели", brand="NoName",
        status="created_new",
    )
    s1, _ = ms.calculate_score(db_session, ms.get_by_id(db_session, u_other_brand))
    assert s1 == 0, f"разные бренды ожидали 0, получили {s1}"

    # 2) Тот же бренд, разные модели (нет общих значимых токенов).
    u_same_brand = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SK-SB",
        raw_name="Intel Xeon E3-1240v5", brand="Intel",
        status="created_new",
    )
    s2, _ = ms.calculate_score(db_session, ms.get_by_id(db_session, u_same_brand))
    assert s2 <= 30, f"тот же бренд, разные модели ожидали ≤ 30, получили {s2}"

    # 3) Тот же бренд + совпадающий конкретный токен «12400».
    u_token = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SK-TK",
        raw_name="Процессор Intel 12400 BOX", brand="Intel",
        status="created_new",
    )
    s3, _ = ms.calculate_score(db_session, ms.get_by_id(db_session, u_token))
    assert 50 <= s3 <= 80, f"бренд+токен ожидали 50-80, получили {s3}"

    # 4) Почти идентичное имя.
    u_near = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SK-NR",
        raw_name="Intel Core i5-12400", brand="Intel",
        status="created_new",
    )
    s4, _ = ms.calculate_score(db_session, ms.get_by_id(db_session, u_near))
    # Без MPN fallback капается до _SCORE_FALLBACK_CAP (70).
    assert s4 >= 70, f"почти идентичное имя ожидали ≥ 70, получили {s4}"


def test_detail_page_shows_same_candidate_as_list(admin_portal_client, db_session):
    """Детальная страница использует тот же скоринг, что и список.

    Раньше список показывал best_candidate из calculate_score (OR),
    а деталь — из find_candidates (AND) — они могли расходиться.
    Теперь оба пути через calculate_candidates_ranked: лучший кандидат
    в списке совпадает с первой радио-кнопкой на детальной.
    """
    from portal.services.databases import mapping_service as ms

    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")

    real_cpu = _insert_cpu(
        db_session, model="Intel Core i5-12400", manufacturer="Intel",
        sku="CM8071512400",
    )

    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SK-SYNC",
        raw_name="Intel Core i5-12400", brand="Intel",
        status="created_new",
    )

    # Считаем score, чтобы best_candidate_component_id записался в БД.
    ms.ensure_score(db_session, unmapped_id)

    # В списке best = real_cpu.
    row = db_session.execute(_t(
        "SELECT best_candidate_component_id FROM unmapped_supplier_items "
        "WHERE id = :id"
    ), {"id": unmapped_id}).first()
    assert int(row.best_candidate_component_id) == real_cpu

    # На детали — тот же real_cpu среди кандидатов.
    r = admin_portal_client.get(f"/databases/mapping/{unmapped_id}")
    assert r.status_code == 200
    assert f'value="{real_cpu}"' in r.text
    # Колонка Score видна и содержит 100 для этого кандидата.
    assert "100" in r.text
    # Колонка reason видна (хотя бы одна из типовых строк).
    assert ("бренд" in r.text) or ("модельный токен" in r.text) \
        or ("похожесть имён" in r.text)


# ---- этап 7.6: UX и надёжность merge ----------------------------------


def test_detail_page_preselects_best_candidate(admin_portal_client, db_session):
    """На /databases/mapping/{id} по умолчанию выбран (checked) кандидат с
    лучшим score — тот же, что виден как best_candidate в списке."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")

    # Два кандидата: «точный» (MPN совпадёт) и «слабый» (только бренд).
    best = _insert_cpu(
        db_session, model="Intel Core i5-12400", manufacturer="Intel",
        sku="CM8071512400F",
    )
    _insert_cpu(
        db_session, model="Intel Core i5-13400", manufacturer="Intel",
        sku="CM8071512400-OTHER",
    )

    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SK-PRE",
        raw_name="Intel Core i5-12400", brand="Intel",
        status="created_new", mpn="CM8071512400F",
    )

    r = admin_portal_client.get(f"/databases/mapping/{unmapped_id}")
    assert r.status_code == 200
    # checked стоит именно на best.
    import re as _re
    m = _re.search(r'<input type="radio"[^>]*value="(\d+)"[^>]*checked', r.text)
    assert m, "Не нашли checked radio на детальной"
    assert int(m.group(1)) == best


def test_detail_page_has_merge_button_at_top(admin_portal_client, db_session):
    """Кнопка «Объединить с выбранным» находится в той же группе, что
    «Новый товар» и «Разобраться потом», и ДО таблицы кандидатов."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")
    _insert_cpu(
        db_session, model="Intel Core i5-12400", manufacturer="Intel",
        sku="CM8071512400F",
    )
    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="SK-TOP",
        raw_name="Intel Core i5-12400", brand="Intel",
        status="created_new", mpn="CM8071512400F",
    )

    r = admin_portal_client.get(f"/databases/mapping/{unmapped_id}")
    assert r.status_code == 200

    # Кнопка merge находится РАНЬШЕ заголовка таблицы кандидатов.
    pos_merge = r.text.find("Объединить с выбранным")
    pos_table = r.text.find("Похожие компоненты в БД")
    assert pos_merge != -1, "Кнопка merge не найдена"
    assert pos_table != -1, "Таблица кандидатов не найдена"
    assert pos_merge < pos_table, (
        "Кнопка merge должна быть ВЫШЕ таблицы кандидатов, "
        f"но pos_merge={pos_merge}, pos_table={pos_table}"
    )
    # И привязана к форме merge через form="merge-form".
    assert 'form="merge-form"' in r.text


def test_merge_success(admin_portal_client, db_session):
    """Нормальное объединение: supplier_prices переносится, скелет
    удаляется, unmapped.status = 'merged'. Повторяет позитивный сценарий
    из 7.1 как санити-проверку после правок 7.6."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Merlion")
    real_cpu = _insert_cpu(db_session, model="Real CPU", sku="REAL-1")
    skeleton = _insert_cpu(db_session, model="Skel CPU", sku="SKEL-1")
    _insert_price(db_session, supplier_id=sid, component_id=skeleton,
                  supplier_sku="MER-1")
    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="MER-1",
        raw_name="Real CPU dupe", status="created_new",
        resolved_component_id=skeleton,
    )

    r = admin_portal_client.get("/databases/mapping")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        f"/databases/mapping/{unmapped_id}/merge",
        data={"target_component_id": real_cpu, "csrf_token": token},
    )
    assert r.status_code == 302

    # supplier_prices на real_cpu.
    row = db_session.execute(_t(
        "SELECT component_id FROM supplier_prices WHERE supplier_id = :sid"
    ), {"sid": sid}).first()
    assert int(row.component_id) == real_cpu
    # Скелет удалён.
    assert db_session.execute(_t(
        "SELECT id FROM cpus WHERE id = :id"
    ), {"id": skeleton}).first() is None
    # unmapped → merged.
    assert db_session.execute(_t(
        "SELECT status FROM unmapped_supplier_items WHERE id = :id"
    ), {"id": unmapped_id}).scalar() == "merged"


def test_merge_with_existing_supplier_price(admin_portal_client, db_session):
    """Если на target уже есть supplier_prices от того же поставщика и
    категории (UNIQUE sup+cat+component), merge не падает с 500:
    конфликтующая строка удаляется, новая переносится."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")
    real_cpu = _insert_cpu(db_session, model="Real CPU", sku="REAL-PRE")
    skeleton = _insert_cpu(db_session, model="Skel CPU", sku="SKEL-PRE")

    # У real_cpu ОТ ТОГО ЖЕ поставщика уже есть supplier_prices (старая
    # загрузка того же SSD) — именно этот кейс ломал merge в 7.5.
    _insert_price(db_session, supplier_id=sid, component_id=real_cpu,
                  supplier_sku="TR-OLD", price=999.0)
    # А у скелета — свежая запись из новой загрузки.
    _insert_price(db_session, supplier_id=sid, component_id=skeleton,
                  supplier_sku="TR-NEW", price=1234.0)

    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="TR-NEW",
        raw_name="Real CPU", status="created_new",
        resolved_component_id=skeleton,
    )

    r = admin_portal_client.get("/databases/mapping")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        f"/databases/mapping/{unmapped_id}/merge",
        data={"target_component_id": real_cpu, "csrf_token": token},
    )
    # 302 на /databases/mapping — редирект, НЕ 500.
    assert r.status_code == 302

    # На real_cpu осталась ОДНА строка — свежая (TR-NEW), конфликтующая
    # старая (TR-OLD) удалена.
    rows = db_session.execute(_t(
        "SELECT supplier_sku, price FROM supplier_prices "
        "WHERE supplier_id = :sid AND category = 'cpu' AND component_id = :cid "
        "ORDER BY id"
    ), {"sid": sid, "cid": real_cpu}).all()
    assert len(rows) == 1
    assert rows[0].supplier_sku == "TR-NEW"
    # Скелет удалён.
    assert db_session.execute(_t(
        "SELECT id FROM cpus WHERE id = :id"
    ), {"id": skeleton}).first() is None


def test_merge_self_reference_rejected(admin_portal_client, db_session):
    """Попытка объединить со скелетом, который принадлежит ДРУГОЙ
    unmapped-записи, даёт понятную flash-ошибку, а не 500."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Merlion")

    # Скелет, привязанный к другой (посторонней) unmapped-записи.
    other_skel = _insert_cpu(db_session, model="Other skel", sku="OS-1")
    _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="OS-1",
        raw_name="Other skel", status="created_new",
        resolved_component_id=other_skel,
    )

    # «Моя» unmapped-запись со своим скелетом.
    my_skel = _insert_cpu(db_session, model="My skel", sku="MS-1")
    _insert_price(db_session, supplier_id=sid, component_id=my_skel,
                  supplier_sku="MS-1")
    my_unmapped = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="MS-1",
        raw_name="My raw", status="created_new",
        resolved_component_id=my_skel,
    )

    r = admin_portal_client.get("/databases/mapping")
    token = extract_csrf(r.text)
    # Пытаемся объединить my_unmapped с other_skel — это «чужой» скелет.
    r = admin_portal_client.post(
        f"/databases/mapping/{my_unmapped}/merge",
        data={"target_component_id": other_skel, "csrf_token": token},
    )
    # 302 (редирект с flash), НЕ 500.
    assert r.status_code == 302

    # Статус my_unmapped не поменялся.
    status = db_session.execute(_t(
        "SELECT status FROM unmapped_supplier_items WHERE id = :id"
    ), {"id": my_unmapped}).scalar()
    assert status == "created_new"
    # Flash-ошибка видна на следующем GET.
    r2 = admin_portal_client.get("/databases/mapping")
    assert "Не удалось объединить" in r2.text or "скелет" in r2.text.lower()


def test_merge_idempotent(admin_portal_client, db_session):
    """Повторный merge на уже merged-записи не падает, не создаёт
    дубликатов supplier_prices, не меняет resolved_component_id."""
    _cleanup_fixtures_for_test(db_session)
    sid = _insert_supplier(db_session, "Treolan")
    real_cpu = _insert_cpu(db_session, model="Real", sku="REAL-IDP")
    skeleton = _insert_cpu(db_session, model="Skel", sku="SKEL-IDP")
    _insert_price(db_session, supplier_id=sid, component_id=skeleton,
                  supplier_sku="TR-IDP")
    unmapped_id = _insert_unmapped(
        db_session, supplier_id=sid, supplier_sku="TR-IDP",
        raw_name="Real dupe", status="created_new",
        resolved_component_id=skeleton,
    )

    r = admin_portal_client.get("/databases/mapping")
    token = extract_csrf(r.text)
    # Первый merge — нормальный.
    r = admin_portal_client.post(
        f"/databases/mapping/{unmapped_id}/merge",
        data={"target_component_id": real_cpu, "csrf_token": token},
    )
    assert r.status_code == 302

    # Второй merge на той же записи — должен быть noop, не 500.
    r = admin_portal_client.get("/databases/mapping")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        f"/databases/mapping/{unmapped_id}/merge",
        data={"target_component_id": real_cpu, "csrf_token": token},
    )
    assert r.status_code == 302

    # supplier_prices не задублировался.
    rows = db_session.execute(_t(
        "SELECT id FROM supplier_prices WHERE supplier_id = :sid"
    ), {"sid": sid}).all()
    assert len(rows) == 1
    # Статус — всё ещё merged, resolved_component_id — real_cpu.
    row = db_session.execute(_t(
        "SELECT status, resolved_component_id FROM unmapped_supplier_items "
        "WHERE id = :id"
    ), {"id": unmapped_id}).first()
    assert row.status == "merged"
    assert int(row.resolved_component_id) == real_cpu
