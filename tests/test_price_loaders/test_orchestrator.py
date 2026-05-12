# End-to-end тесты orchestrator.load_price:
#   - загрузка мокового Merlion-прайса корректно создаёт записи в
#     supplier_prices и unmapped_supplier_items;
#   - повторная загрузка идемпотентна;
#   - загрузка Treolan с Intel CPU находит существующий OCS-компонент
#     через GTIN (и не создаёт дубликат);
#   - ambiguous выдаёт один выбор + запись в unmapped со статусом pending.

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text as _t

from portal.services.configurator.price_loaders.orchestrator import load_price


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


# ---- 11.4: хелперы для проверок повторной загрузки --------------------


def _ensure_supplier(session, name: str) -> int:
    """Идемпотентно создаёт поставщика и возвращает его id. Используем,
    когда нужно засеять supplier_prices ДО первой загрузки."""
    row = session.execute(_t(
        "INSERT INTO suppliers (name, is_active) VALUES (:n, TRUE) "
        "ON CONFLICT (name) DO UPDATE SET is_active = suppliers.is_active "
        "RETURNING id"
    ), {"n": name}).first()
    session.commit()
    return int(row.id)


def _seed_supplier_price(
    session, *,
    supplier_id: int, category: str, component_id: int,
    supplier_sku: str,
    price: Decimal = Decimal("100.00"),
    currency: str = "RUB",
    stock_qty: int = 10,
    transit_qty: int = 0,
    raw_name: str | None = None,
) -> None:
    """Прямая вставка строки в supplier_prices — для setup'а тестов
    обновления и disappeared. Не идёт через orchestrator, чтобы тест
    проверял именно поведение второй загрузки."""
    session.execute(_t(
        "INSERT INTO supplier_prices "
        "    (supplier_id, category, component_id, supplier_sku, "
        "     price, currency, stock_qty, transit_qty, raw_name, updated_at) "
        "VALUES "
        "    (:sid, :cat, :cid, :sku, :p, :cur, :s, :t, :rn, NOW())"
    ), {
        "sid": supplier_id, "cat": category, "cid": component_id,
        "sku": supplier_sku, "p": price, "cur": currency,
        "s": stock_qty, "t": transit_qty, "rn": raw_name,
    })
    session.commit()


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


# Мини-этап 2026-05-13: classification-фикс fan-разветвителя в категории cooler.

def test_fan_splitter_skeleton_marked_is_hidden(make_merlion_xlsx, db_session):
    """Скелет fan-разветвителя в категории cooler (ID-Cooling FS-04 ARGB)
    автоматически создаётся с is_hidden=TRUE — иначе он попадёт в подбор
    CPU-кулера, как и было до фикса 2026-05-13."""
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров",
            "g2": "Устройства охлаждения",
            "g3": "Универсальные",
            "brand": "ID-Cooling", "number": "M-SPL1", "mpn": "FS-04-ARGB",
            "name": "Разветвитель питания ID-Cooling FS-04 ARGB",
            "price_rub": 300, "stock": 10,
        },
    ])
    result = load_price(path, supplier_key="merlion")
    assert result["added"] == 1

    is_hidden = bool(db_session.execute(_t(
        "SELECT is_hidden FROM coolers WHERE sku = 'FS-04-ARGB'"
    )).scalar())
    assert is_hidden is True


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


# =========================================================================
# Этап 11.4 — повторная загрузка прайсов: обновление и disappeared
# =========================================================================


def test_orchestrator_updates_existing_price(make_merlion_xlsx, db_session):
    """Существующая строка supplier_prices обновляется по тому же
    (supplier_id, supplier_sku): новая цена, новый stock, raw_name тоже
    подменяется. Никаких added — только updated."""
    psu_id = _insert_psu(db_session, model="Corsair RM750X", sku="RM750X")
    supplier_id = _ensure_supplier(db_session, "Merlion")
    _seed_supplier_price(
        db_session,
        supplier_id=supplier_id, category="psu",
        component_id=psu_id, supplier_sku="M-001",
        price=Decimal("100.00"), stock_qty=10, transit_qty=0,
        raw_name="Старое имя из прошлой загрузки",
    )

    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Блоки питания",
            "g3": "Блоки питания",
            "brand": "Corsair", "number": "M-001", "mpn": "RM750X",
            "name": "Corsair RM750x 750W 80+ Gold",
            "price_rub": 12000, "stock": 5,
        },
    ])
    result = load_price(path, supplier_key="merlion")

    assert result["updated"] == 1
    assert result["added"] == 0
    assert result["disappeared"] == 0

    rows = db_session.execute(_t(
        "SELECT supplier_sku, price, stock_qty, transit_qty, currency "
        "FROM supplier_prices WHERE supplier_id = :sid"
    ), {"sid": supplier_id}).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.supplier_sku == "M-001"
    assert int(r.stock_qty) == 5
    assert Decimal(str(r.price)) == Decimal("12000.00")
    assert r.currency == "RUB"


def test_orchestrator_updates_raw_name_on_existing(make_merlion_xlsx, db_session):
    """raw_name обновляется при повторной загрузке — даже если новое имя
    короче или беднее (бизнес-правило 11.4: «полагаемся на агрегацию из
    других источников и enrichment в 11.6»). Здесь специально берём
    более длинное новое имя — типичный кейс на проде."""
    psu_id = _insert_psu(db_session, model="Corsair RM650X", sku="RM650X")
    supplier_id = _ensure_supplier(db_session, "Merlion")
    _seed_supplier_price(
        db_session,
        supplier_id=supplier_id, category="psu",
        component_id=psu_id, supplier_sku="M-100",
        price=Decimal("9500.00"), stock_qty=3,
        raw_name="Corsair RM650x",
    )

    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Блоки питания",
            "g3": "Блоки питания",
            "brand": "Corsair", "number": "M-100", "mpn": "RM650X",
            "name": "Corsair RM650x 650W 80+ Gold модульный",
            "price_rub": 9500, "stock": 3,
        },
    ])
    load_price(path, supplier_key="merlion")

    raw_name = db_session.execute(_t(
        "SELECT raw_name FROM supplier_prices WHERE supplier_id = :sid"
    ), {"sid": supplier_id}).scalar()
    assert raw_name == "Corsair RM650x 650W 80+ Gold модульный"


def test_orchestrator_marks_disappeared(make_merlion_xlsx, db_session):
    """В БД три активных позиции (stock>0); прайс присылает только одну —
    остальные две должны быть помечены stock=0/transit=0 (disappeared).
    Сама запись supplier_prices не удаляется."""
    psu_a = _insert_psu(db_session, model="A", sku="MPN-A")
    psu_b = _insert_psu(db_session, model="B", sku="MPN-B")
    psu_c = _insert_psu(db_session, model="C", sku="MPN-C")
    supplier_id = _ensure_supplier(db_session, "Merlion")
    for psu_id, sku in [(psu_a, "M-A"), (psu_b, "M-B"), (psu_c, "M-C")]:
        _seed_supplier_price(
            db_session,
            supplier_id=supplier_id, category="psu",
            component_id=psu_id, supplier_sku=sku,
            price=Decimal("100.00"), stock_qty=4, transit_qty=2,
        )

    # В прайсе только один SKU из трёх — M-A; новый stock/transit.
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Блоки питания",
            "g3": "Блоки питания",
            "brand": "Corsair", "number": "M-A", "mpn": "MPN-A",
            "name": "PSU A", "price_rub": 110, "stock": 7, "transit_1": 1,
        },
    ])
    result = load_price(path, supplier_key="merlion")

    assert result["updated"] == 1
    assert result["added"] == 0
    assert result["disappeared"] == 2
    assert set(result["disappeared_skus"]) == {"M-B", "M-C"}
    assert result["disappeared_truncated"] is False

    # M-A — обновлено, M-B и M-C — обнулены, но строки на месте.
    rows = db_session.execute(_t(
        "SELECT supplier_sku, stock_qty, transit_qty "
        "FROM supplier_prices WHERE supplier_id = :sid "
        "ORDER BY supplier_sku"
    ), {"sid": supplier_id}).all()
    by_sku = {r.supplier_sku: (int(r.stock_qty), int(r.transit_qty)) for r in rows}
    assert by_sku == {"M-A": (7, 1), "M-B": (0, 0), "M-C": (0, 0)}


def test_orchestrator_does_not_mark_disappeared_on_failed(
    make_merlion_xlsx, db_session,
):
    """При status='failed' (rows_matched=0, при этом был не пустой файл)
    disappeared НЕ применяется — иначе кривая загрузка обнулит остатки.
    Здесь имитируем failed «естественно»: все строки прайса оказываются
    вне наших категорий → updated=0, added=0, total_rows>0 → 'failed'."""
    psu_a = _insert_psu(db_session, model="A", sku="MPN-A")
    supplier_id = _ensure_supplier(db_session, "Merlion")
    _seed_supplier_price(
        db_session,
        supplier_id=supplier_id, category="psu",
        component_id=psu_a, supplier_sku="M-A",
        price=Decimal("100.00"), stock_qty=4, transit_qty=2,
    )

    # Все строки — телевизоры/смартфоны: вне наших категорий, skipped.
    path = make_merlion_xlsx([
        {
            "g1": "Техника", "g2": "Телевизоры", "g3": "OLED",
            "brand": "LG", "number": "TV-1", "mpn": "OLED77C3",
            "name": "LG OLED 77", "price_rub": 250000, "stock": 1,
        },
        {
            "g1": "Техника", "g2": "Телефоны", "g3": "Смартфоны",
            "brand": "Apple", "number": "TV-2", "mpn": "IP15",
            "name": "iPhone 15", "price_rub": 100000, "stock": 1,
        },
    ])
    result = load_price(path, supplier_key="merlion")

    assert result["status"] == "failed"
    assert result["disappeared"] == 0
    assert result["disappeared_skus"] == []

    # Существующая M-A осталась нетронутой.
    row = db_session.execute(_t(
        "SELECT stock_qty, transit_qty FROM supplier_prices "
        "WHERE supplier_id = :sid AND supplier_sku = 'M-A'"
    ), {"sid": supplier_id}).first()
    assert int(row.stock_qty) == 4
    assert int(row.transit_qty) == 2


def test_orchestrator_zero_stock_in_price_is_not_disappeared(
    make_merlion_xlsx, db_session,
):
    """Если поставщик прислал позицию с явным stock=0 — это нормальный
    UPDATE существующей строки, не disappeared (SKU присутствует в файле)."""
    psu_a = _insert_psu(db_session, model="A", sku="RM750X")
    supplier_id = _ensure_supplier(db_session, "Merlion")
    _seed_supplier_price(
        db_session,
        supplier_id=supplier_id, category="psu",
        component_id=psu_a, supplier_sku="M-A",
        price=Decimal("100.00"), stock_qty=4, transit_qty=2,
    )

    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Блоки питания",
            "g3": "Блоки питания",
            "brand": "Corsair", "number": "M-A", "mpn": "RM750X",
            "name": "Corsair RM750x", "price_rub": 100,
            "stock": 0, "transit_1": 0,
        },
    ])
    result = load_price(path, supplier_key="merlion")

    assert result["updated"] == 1
    assert result["disappeared"] == 0
    # SKU был в файле, поэтому в disappeared_skus не попадает.
    assert "M-A" not in (result.get("disappeared_skus") or [])

    row = db_session.execute(_t(
        "SELECT stock_qty, transit_qty FROM supplier_prices "
        "WHERE supplier_id = :sid AND supplier_sku = 'M-A'"
    ), {"sid": supplier_id}).first()
    assert int(row.stock_qty) == 0
    assert int(row.transit_qty) == 0


def test_disappeared_truncated_in_report(make_merlion_xlsx, db_session):
    """При >50 disappeared report.disappeared_skus содержит ровно 50,
    флаг disappeared_truncated=True. Само число disappeared — точное."""
    supplier_id = _ensure_supplier(db_session, "Merlion")
    # 60 «активных» позиций, у каждой свой компонент и SKU.
    for i in range(60):
        psu_id = _insert_psu(
            db_session, model=f"PSU-{i}", sku=f"MPN-{i:03d}",
        )
        _seed_supplier_price(
            db_session,
            supplier_id=supplier_id, category="psu",
            component_id=psu_id, supplier_sku=f"M-{i:03d}",
            price=Decimal("100.00"), stock_qty=1,
        )

    # Прайс с одной строкой, SKU не пересекается ни с одним из 60.
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Блоки питания",
            "g3": "Блоки питания",
            "brand": "Corsair", "number": "NEW-1", "mpn": "BRAND-NEW",
            "name": "Совершенно новая позиция", "price_rub": 5000, "stock": 2,
        },
    ])
    result = load_price(path, supplier_key="merlion")

    assert result["disappeared"] == 60
    assert len(result["disappeared_skus"]) == 50
    assert result["disappeared_truncated"] is True


# ---- 12.3-fix: пустой вход → failed + защита от disappeared ----------


def test_orchestrator_marks_failed_on_zero_total_rows(db_session):
    """Если loader/fetcher отдал 0 строк (rows=[]), upload фиксируется
    как failed, disappeared НЕ применяется. До 12.3-fix run #17 в этой
    ситуации фиксировался success и обнулял остатки поставщика."""
    from portal.services.configurator.price_loaders.orchestrator import save_price_rows

    psu_a = _insert_psu(db_session, model="A", sku="MPN-A")
    supplier_id = _ensure_supplier(db_session, "Treolan")
    _seed_supplier_price(
        db_session,
        supplier_id=supplier_id, category="psu",
        component_id=psu_a, supplier_sku="T-A",
        price=Decimal("100.00"), stock_qty=4, transit_qty=2,
    )

    result = save_price_rows(
        supplier_name="Treolan",
        source="empty.json",
        rows=[],
    )

    assert result["status"] == "failed"
    assert result["total_rows"] == 0
    assert result["disappeared"] == 0
    assert result["disappeared_skus"] == []

    # Засеянная строка осталась нетронутой.
    row = db_session.execute(_t(
        "SELECT stock_qty, transit_qty FROM supplier_prices "
        "WHERE supplier_id = :sid AND supplier_sku = 'T-A'"
    ), {"sid": supplier_id}).first()
    assert int(row.stock_qty) == 4
    assert int(row.transit_qty) == 2

    # В price_uploads.notes — явная пометка про адаптер.
    notes = db_session.execute(_t(
        "SELECT notes FROM price_uploads WHERE id = :id"
    ), {"id": result["upload_id"]}).scalar()
    assert "0 строк" in (notes or "")
    assert "disappeared не применялся" in (notes or "")


def test_orchestrator_does_not_call_mark_disappeared_on_zero_rows(
    monkeypatch, db_session,
):
    """Тот же сценарий, но с шпионом на _mark_disappeared — он не должен
    быть вызван. Это «защита первой очереди»: даже если в counters что-то
    разъедется, сама функция-обнулитель не должна стартовать."""
    from portal.services.configurator.price_loaders import orchestrator as orch_mod
    from portal.services.configurator.price_loaders.orchestrator import save_price_rows

    psu_a = _insert_psu(db_session, model="B", sku="MPN-B")
    supplier_id = _ensure_supplier(db_session, "Treolan")
    _seed_supplier_price(
        db_session,
        supplier_id=supplier_id, category="psu",
        component_id=psu_a, supplier_sku="T-B",
        price=Decimal("100.00"), stock_qty=10, transit_qty=0,
    )

    calls: list[set[str]] = []

    def spy(session, *, supplier_id, missing_skus, counters):
        calls.append(set(missing_skus))

    monkeypatch.setattr(orch_mod, "_mark_disappeared", spy)

    result = save_price_rows(
        supplier_name="Treolan", source="empty.json", rows=[],
    )

    assert calls == [], f"_mark_disappeared не должен был вызваться, но был: {calls}"
    assert result["status"] == "failed"


# =========================================================================
# Этап 6 слияния (2026-05-08) — printer/mfu пишутся в printers_mfu
# (раньше Этапа 4 они пропускались со счётчиком pending_printers_mfu;
#  сейчас идут штатным путём через CATEGORY_TO_TABLE → printers_mfu).
# =========================================================================


def test_orchestrator_writes_printer_mfu_to_printers_mfu(
    make_merlion_xlsx, db_session,
):
    """Печатные строки попадают в printers_mfu (NO_MATCH → создаётся
    скелет с category из адаптера, sku вида brand:mpn).

    Этап 9a-enrich (2026-05-10): для новых SKU при создании сразу
    применяется regex-парсер `parse_printer_attrs(name)`. Если парсер
    нашёл хоть один атрибут — attrs_jsonb наполняется значениями + n/a
    для остальных, attrs_source='regex_name'. Если ничего не нашёл —
    attrs_jsonb остаётся пустым ({}) и attrs_source=NULL.

    ПК-строка в той же загрузке обрабатывается обычным путём.
    pending_printers_mfu остаётся 0 — stub-ветка Этапа 4 убрана."""
    path = make_merlion_xlsx([
        # ПК-строка — обычный сценарий (created_new + skeleton в psus).
        {
            "g1": "Комплектующие для компьютеров", "g2": "Блоки питания",
            "g3": "Блоки питания",
            "brand": "Corsair", "number": "M-PSU", "mpn": "RM750X",
            "name": "Corsair RM750x 750W", "price_rub": 12000, "stock": 4,
        },
        # Печатные — тоже NO_MATCH, идут в printers_mfu со своими
        # category='printer' / 'mfu'.
        {
            "g1": "Периферия и аксессуары", "g2": "Принтеры",
            "g3": "Лазерные",
            "brand": "HP", "number": "M-PR1", "mpn": "M404n",
            "name": "HP LaserJet Pro M404n", "price_rub": 35000, "stock": 2,
        },
        {
            "g1": "Периферия и аксессуары", "g2": "Принтеры",
            "g3": "МФУ лазерные",
            "brand": "Pantum", "number": "M-MF1", "mpn": "M6500W",
            "name": "Pantum M6500W", "price_rub": 18000, "stock": 1,
        },
        # Явно «ignore»-подкатегория печати — наша категория None → skipped.
        {
            "g1": "Периферия и аксессуары", "g2": "Принтеры",
            "g3": "Термопринтеры",
            "brand": "Sharp", "number": "M-IGN", "mpn": "TP-1",
            "name": "Sharp Thermal", "price_rub": 5000, "stock": 0,
        },
    ])

    result = load_price(path, supplier_key="merlion")

    # 1 PSU + 2 printer/mfu — все три processed; ignore — skipped.
    assert result["pending_printers_mfu"] == 0
    assert result["processed"] == 3
    assert result["added"] == 3              # PSU + HP printer + Pantum mfu
    assert result["printers_mfu_added"] == 2
    assert result["printers_mfu_updated"] == 0
    assert result["updated"] == 0
    assert result["skipped"] == 1            # Термопринтер
    assert result["errors"] == 0
    assert result["status"] in ("success", "partial")

    # supplier_prices: 3 записи у Merlion (1 psu + 1 printer + 1 mfu).
    assert _count_supplier_prices(db_session, "Merlion") == 3

    # printers_mfu: 2 строки, у каждой category и sku корректно заполнены.
    # Этап 9a-enrich: для HP LaserJet — парсер найдёт `print_technology=лазерная`
    # (по слову LaserJet); для Pantum M6500W — ни одного атрибута → attrs={}.
    rows = db_session.execute(_t(
        "SELECT sku, mpn, brand, name, category, attrs_jsonb, attrs_source "
        "  FROM printers_mfu ORDER BY id"
    )).all()
    assert len(rows) == 2
    by_mpn = {r.mpn: r for r in rows}
    assert by_mpn["M404n"].category == "printer"
    assert by_mpn["M404n"].sku == "hp:M404n"
    assert by_mpn["M404n"].brand == "HP"
    # parsed нашёл «laser» в LaserJet → attrs_jsonb наполняется
    assert by_mpn["M404n"].attrs_jsonb["print_technology"] == "лазерная"
    assert by_mpn["M404n"].attrs_source == "regex_name"
    assert by_mpn["M6500W"].category == "mfu"
    assert by_mpn["M6500W"].sku == "pantum:M6500W"
    assert by_mpn["M6500W"].brand == "Pantum"
    # parsed ничего не нашёл (только модель и бренд) → attrs={}, source=None
    assert by_mpn["M6500W"].attrs_jsonb == {}
    assert by_mpn["M6500W"].attrs_source is None


def test_orchestrator_only_printer_mfu_writes_skeletons(
    make_merlion_xlsx, db_session,
):
    """Прайс только из printer/mfu-позиций: всё пишется в printers_mfu;
    status — success (added>0). pending_printers_mfu=0."""
    path = make_merlion_xlsx([
        {
            "g1": "Периферия и аксессуары", "g2": "Принтеры",
            "g3": "Лазерные",
            "brand": "Brother", "number": "M-PR2", "mpn": "HL-L2375DW",
            "name": "Brother HL-L2375DW", "price_rub": 22000, "stock": 1,
        },
        {
            "g1": "Периферия и аксессуары", "g2": "Принтеры",
            "g3": "МФУ струйные",
            "brand": "Epson", "number": "M-MF2", "mpn": "L3210",
            "name": "Epson L3210", "price_rub": 14000, "stock": 1,
        },
    ])

    result = load_price(path, supplier_key="merlion")

    assert result["pending_printers_mfu"] == 0
    assert result["processed"] == 2
    assert result["added"] == 2
    assert result["printers_mfu_added"] == 2
    assert result["status"] in ("success", "partial")
    assert _count_supplier_prices(db_session, "Merlion") == 2

    cnt = int(db_session.execute(_t(
        "SELECT count(*) FROM printers_mfu"
    )).scalar())
    assert cnt == 2


# ---- 9a-uncenka (2026-05-10): фильтр уценок ---------------------------


def test_orchestrator_skips_uncenka_printer_row(make_merlion_xlsx, db_session):
    """Позиция с маркером уценки/повреждения коробки в имени НЕ создаёт
    skeleton в printers_mfu, не пишется в supplier_prices и не уходит
    в unmapped — она просто инкрементит counters.skipped_uncenka."""
    path = make_merlion_xlsx([
        {
            "g1": "Периферия и аксессуары", "g2": "Принтеры",
            "g3": "Лазерные",
            "brand": "G&G", "number": "M-UNC1", "mpn": "P2022W-DAMAGED",
            "name": "G&G P2022W (повреждение коробки)",
            "price_rub": 6500, "stock": 2,
        },
        {
            "g1": "Периферия и аксессуары", "g2": "Принтеры",
            "g3": "Лазерные",
            "brand": "Pantum", "number": "M-NORM1", "mpn": "P2500W",
            "name": "Pantum P2500W лазерный 22 ppm",
            "price_rub": 7800, "stock": 3,
        },
    ])
    result = load_price(path, supplier_key="merlion")

    assert result["skipped_uncenka"] == 1
    assert result["added"] == 1
    assert result["printers_mfu_added"] == 1
    assert _count_supplier_prices(db_session, "Merlion") == 1

    # Уценочный SKU не создан.
    cnt_unc = int(db_session.execute(_t(
        "SELECT count(*) FROM printers_mfu WHERE mpn = 'P2022W-DAMAGED'"
    )).scalar())
    assert cnt_unc == 0
    # Нормальный SKU создан.
    cnt_norm = int(db_session.execute(_t(
        "SELECT count(*) FROM printers_mfu WHERE mpn = 'P2500W'"
    )).scalar())
    assert cnt_norm == 1
    # В unmapped уценка не уходит.
    cnt_unmapped = int(db_session.execute(_t(
        "SELECT count(*) FROM unmapped_supplier_items "
        "WHERE supplier_sku = 'M-UNC1'"
    )).scalar())
    assert cnt_unmapped == 0


def test_orchestrator_skips_uncenka_pc_component(make_merlion_xlsx, db_session):
    """Та же логика для ПК-категорий: уценочный БП не создаёт skeleton
    в `psus`, не пишется в supplier_prices."""
    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Блоки питания",
            "g3": "Блоки питания",
            "brand": "Corsair", "number": "M-PSU-UNC", "mpn": "RM850X-USED",
            "name": "Corsair RM850X 850W (б/у)",
            "price_rub": 4500, "stock": 1,
        },
    ])
    result = load_price(path, supplier_key="merlion")
    assert result["skipped_uncenka"] == 1
    assert result["added"] == 0
    assert _count_supplier_prices(db_session, "Merlion") == 0

    cnt = int(db_session.execute(_t(
        "SELECT count(*) FROM psus WHERE sku = 'RM850X-USED'"
    )).scalar())
    assert cnt == 0
