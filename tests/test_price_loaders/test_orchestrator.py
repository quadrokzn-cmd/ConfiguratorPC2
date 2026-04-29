# End-to-end тесты orchestrator.load_price:
#   - загрузка мокового Merlion-прайса корректно создаёт записи в
#     supplier_prices и unmapped_supplier_items;
#   - повторная загрузка идемпотентна;
#   - загрузка Treolan с Intel CPU находит существующий OCS-компонент
#     через GTIN (и не создаёт дубликат);
#   - ambiguous выдаёт один выбор + запись в unmapped со статусом pending.

from __future__ import annotations

from sqlalchemy import text as _t

from app.services.price_loaders.orchestrator import load_price


# ---- хелперы -----------------------------------------------------------


def _count_supplier_prices(session, supplier_name: str) -> int:
    row = session.execute(_t(
        "SELECT COUNT(*) AS c FROM supplier_prices sp "
        "JOIN suppliers s ON s.id = sp.supplier_id "
        "WHERE s.name = :n"
    ), {"n": supplier_name}).first()
    return int(row.c)


def _count_unmapped(session, *, supplier_name: str, status: str | None = None) -> int:
    sql = (
        "SELECT COUNT(*) AS c FROM unmapped_supplier_items u "
        "JOIN suppliers s ON s.id = u.supplier_id "
        "WHERE s.name = :n"
    )
    params = {"n": supplier_name}
    if status:
        sql += " AND u.status = :st"
        params["st"] = status
    return int(session.execute(_t(sql), params).scalar())


def _insert_cpu(session, *, model, sku, gtin=None, manufacturer="AMD"):
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


def _insert_psu(session, *, model, sku, manufacturer="Corsair"):
    row = session.execute(_t(
        "INSERT INTO psus (model, manufacturer, sku) "
        "VALUES (:m, :mfg, :sku) RETURNING id"
    ), {"m": model, "mfg": manufacturer, "sku": sku}).scalar()
    session.commit()
    return int(row)


# ---- Merlion: базовый сценарий ----------------------------------------


def test_merlion_mix_match_and_new(make_merlion_xlsx, db_session):
    """В Merlion-прайсе 3 строки:
       - мать MPN='PRIME-H610M-E' — уже есть в motherboards → match по MPN;
       - БП MPN='RM750X' — новый товар → created_new + скелет;
       - строка «Телевизор» — вне категорий, skipped.
    """
    # Существующий компонент: motherboard (тест из cpu сделаем через psu/cpu,
    # но у motherboards таблица не создавалась здесь. Давайте вставим
    # motherboard и psu.
    db_session.execute(_t(
        "INSERT INTO motherboards "
        "  (model, manufacturer, sku, socket, chipset, form_factor, "
        "   memory_type, has_m2_slot) "
        "VALUES ('PRIME H610M-E D4', 'ASUS', 'PRIME-H610M-E', 'LGA1700', "
        "        'H610', 'mATX', 'DDR4', TRUE)"
    ))
    db_session.commit()

    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Материнские Платы",
            "g3": "Socket-1700",
            "brand": "ASUS", "number": "M-001", "mpn": "PRIME-H610M-E",
            "name": "ASUS PRIME H610M-E D4", "price_rub": 8500, "stock": 3,
        },
        {
            "g1": "Комплектующие для компьютеров", "g2": "Блоки питания",
            "g3": "Блоки питания",
            "brand": "Corsair", "number": "M-002", "mpn": "RM750X",
            "name": "Corsair RM750x 750W", "price_rub": 12000, "stock": 4,
        },
        {
            "g1": "Техника", "g2": "Телевизоры", "g3": "OLED",
            "brand": "LG", "number": "M-003", "mpn": "OLED77C3",
            "name": "LG OLED 77", "price_rub": 250000, "stock": 1,
        },
    ])

    result = load_price(path, supplier_key="merlion")
    assert result["supplier"] == "Merlion"
    assert result["skipped"] == 1          # телевизор
    assert result["processed"] == 2
    assert result["updated"] == 1          # только match по MPN
    assert result["added"] == 1            # скелет для RM750X
    assert result["unmapped_new"] == 1
    assert result["unmapped_ambiguous"] == 0
    assert result["status"] in ("success", "partial")

    # Проверяем, что в supplier_prices Merlion = 2 записи.
    assert _count_supplier_prices(db_session, "Merlion") == 2
    # В unmapped — одна запись со статусом created_new.
    assert _count_unmapped(db_session, supplier_name="Merlion",
                           status="created_new") == 1
    # Статус pending отсутствует.
    assert _count_unmapped(db_session, supplier_name="Merlion",
                           status="pending") == 0


def test_merlion_second_load_is_idempotent(make_merlion_xlsx, db_session):
    """Повторная загрузка не создаёт новых компонентов и не плодит
    строки в unmapped_supplier_items."""
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Блоки питания",
            "g3": "Блоки питания",
            "brand": "Corsair", "number": "M-100", "mpn": "RM650X",
            "name": "Corsair RM650x", "price_rub": 9500, "stock": 3,
        },
    ])
    r1 = load_price(path, supplier_key="merlion")
    r2 = load_price(path, supplier_key="merlion")

    # После первой загрузки создан 1 скелет; после второй — НЕ должно.
    assert r1["added"] == 1
    assert r2["added"] == 0
    # supplier_prices = 1 (повторный INSERT делает UPDATE).
    assert _count_supplier_prices(db_session, "Merlion") == 1
    # unmapped = 1 (статус остаётся created_new, не плодится).
    assert _count_unmapped(db_session, supplier_name="Merlion") == 1


# ---- Treolan: Intel CPU через GTIN ------------------------------------


def test_treolan_intel_cpu_matches_by_gtin(make_treolan_xlsx, db_session):
    """В БД есть Intel CPU от OCS (sku=OrderCode, gtin=...).
    Treolan загружает тот же CPU со своим артикулом=S-Spec, но тем же GTIN.
    Сопоставление проходит по GTIN, новый компонент НЕ создаётся."""
    ocs_id = _insert_cpu(
        db_session,
        model="Intel Core i5-13400F", manufacturer="Intel",
        sku="CM8071512400F", gtin="5032037260466",
    )

    path = make_treolan_xlsx([
        {"category": "Комплектующие->Процессоры"},
        {
            "article": "SRMBG",
            "name":    "Intel Core i5-13400F 2.5GHz",
            "brand":   "Intel",
            "stock":   3, "price_rub": 18900,
            "gtin":    "5032037260466",
        },
    ])

    result = load_price(path, supplier_key="treolan")
    assert result["added"] == 0
    assert result["unmapped_new"] == 0
    assert result["by_source"].get("gtin", 0) == 1

    # supplier_prices указывает именно на OCS-компонент.
    row = db_session.execute(_t(
        "SELECT component_id FROM supplier_prices sp "
        "JOIN suppliers s ON s.id = sp.supplier_id "
        "WHERE s.name = 'Treolan' AND category = 'cpu'"
    )).first()
    assert int(row.component_id) == ocs_id

    # В cpus всё ещё одна запись (дубликат не создан).
    cnt = int(db_session.execute(_t("SELECT COUNT(*) FROM cpus")).scalar())
    assert cnt == 1


def test_treolan_intel_cpu_without_gtin_in_db_goes_unmapped(make_treolan_xlsx, db_session):
    """Если у OCS-компонента ещё не сделан backfill gtin — Treolan-строка
    уходит в unmapped_supplier_items со статусом created_new."""
    _insert_cpu(
        db_session,
        model="Intel Core i5-13400F", manufacturer="Intel",
        sku="CM8071512400F", gtin=None,
    )

    path = make_treolan_xlsx([
        {"category": "Комплектующие->Процессоры"},
        {
            "article": "SRMBG", "name": "Intel Core i5-13400F", "brand": "Intel",
            "stock": 1, "price_rub": 18900, "gtin": "5032037260466",
        },
    ])
    result = load_price(path, supplier_key="treolan")
    assert result["added"] == 1
    assert result["unmapped_new"] == 1

    row = db_session.execute(_t(
        "SELECT status, notes FROM unmapped_supplier_items u "
        "JOIN suppliers s ON s.id = u.supplier_id "
        "WHERE s.name = 'Treolan'"
    )).first()
    assert row.status == "created_new"
    assert "NO_MATCH" in (row.notes or "")


# ---- Ambiguous --------------------------------------------------------


def test_ambiguous_mpn_writes_supplier_prices_and_pending(make_treolan_xlsx, db_session):
    """В БД два компонента с одинаковым sku (технически дубликат).
    Загрузка Treolan привязывает supplier_prices к min(id) и создаёт
    запись в unmapped со статусом pending и notes, содержащим AMBIGUOUS."""
    cid_a = _insert_cpu(db_session, model="A", sku="DUP-MPN")
    cid_b = _insert_cpu(db_session, model="B", sku="DUP-MPN")

    path = make_treolan_xlsx([
        {"category": "Комплектующие->Процессоры"},
        {
            "article": "DUP-MPN", "name": "Дублирующий CPU", "brand": "AMD",
            "stock": 2, "price_rub": 10000,
        },
    ])
    result = load_price(path, supplier_key="treolan")
    assert result["unmapped_ambiguous"] == 1
    assert result["unmapped_new"] == 0

    # supplier_prices привязан к меньшему id.
    cid = int(db_session.execute(_t(
        "SELECT component_id FROM supplier_prices sp "
        "JOIN suppliers s ON s.id = sp.supplier_id "
        "WHERE s.name = 'Treolan'"
    )).scalar())
    assert cid == min(cid_a, cid_b)

    # unmapped: статус pending, notes содержит AMBIGUOUS.
    row = db_session.execute(_t(
        "SELECT status, notes FROM unmapped_supplier_items u "
        "JOIN suppliers s ON s.id = u.supplier_id "
        "WHERE s.name = 'Treolan'"
    )).first()
    assert row.status == "pending"
    assert "AMBIGUOUS" in row.notes


# ---- Поля unmapped -----------------------------------------------------


# ---- Этап 9Г.1: автоматическое скрытие корпусных вентиляторов --------


def test_case_fan_skeleton_marked_is_hidden(make_merlion_xlsx, db_session):
    """Скелет корпусного вентилятора в категории cooler автоматически
    создаётся с is_hidden=TRUE — иначе он попадёт в подбор CPU-кулера."""
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров",
            "g2": "Устройства охлаждения",
            "g3": "Универсальные",
            "brand": "Corsair", "number": "M-FAN1", "mpn": "AF120-ELITE",
            "name": "Corsair AF120 ELITE PWM 120mm Case Fan",
            "price_rub": 1500, "stock": 5,
        },
    ])
    result = load_price(path, supplier_key="merlion")
    assert result["added"] == 1

    is_hidden = bool(db_session.execute(_t(
        "SELECT is_hidden FROM coolers WHERE sku = 'AF120-ELITE'"
    )).scalar())
    assert is_hidden is True


def test_cpu_cooler_skeleton_remains_visible(make_merlion_xlsx, db_session):
    """CPU-кулер с явными маркерами (tower / процессор) НЕ помечается
    is_hidden — даже если он создаётся как скелет."""
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров",
            "g2": "Устройства охлаждения",
            "g3": "Все кулеры",
            "brand": "DeepCool", "number": "M-FAN2", "mpn": "AK620",
            "name": "DeepCool AK620 Tower CPU Cooler 260W",
            "price_rub": 6500, "stock": 2,
        },
    ])
    result = load_price(path, supplier_key="merlion")
    assert result["added"] == 1

    is_hidden = bool(db_session.execute(_t(
        "SELECT is_hidden FROM coolers WHERE sku = 'AK620'"
    )).scalar())
    assert is_hidden is False


def test_unmapped_stores_raw_and_guessed_category(make_merlion_xlsx, db_session):
    """raw_category — строго путь от поставщика, guessed_category — наш код."""
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Блоки питания",
            "g3": "Блоки питания",
            "brand": "BQ", "number": "M-X1", "mpn": "ZX-PSU",
            "name": "BeQuiet ZX PSU", "price_rub": 5000, "stock": 1,
        },
    ])
    load_price(path, supplier_key="merlion")

    row = db_session.execute(_t(
        "SELECT raw_category, guessed_category, supplier_sku, status, notes "
        "FROM unmapped_supplier_items u "
        "JOIN suppliers s ON s.id = u.supplier_id "
        "WHERE s.name = 'Merlion'"
    )).first()
    assert row is not None
    assert row.raw_category == "Комплектующие для компьютеров | Блоки питания | Блоки питания"
    assert row.guessed_category == "psu"
    assert row.supplier_sku == "M-X1"
    assert row.status == "created_new"
