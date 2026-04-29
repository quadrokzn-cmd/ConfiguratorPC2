# Тесты email_composer (этап 8.3).
#
# Используют фикстуры test_web/conftest.py — реальная тестовая БД.
# exchange_rate мокается, чтобы не ходить к ЦБ РФ.

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import text as _t

from app.services import spec_service
from app.services.export import email_composer


# --- Фикстуры/хелперы ----------------------------------------------------

def _seed_user(db_session, *, login: str = "em-mgr") -> int:
    from app.auth import hash_password
    row = db_session.execute(
        _t(
            "INSERT INTO users (login, password_hash, role, name) "
            "VALUES (:l, :p, 'manager', 'Em Manager') RETURNING id"
        ),
        {"l": login, "p": hash_password("x")},
    ).first()
    db_session.commit()
    return int(row.id)


def _insert_supplier(db_session, *, name: str, email: str | None) -> int:
    # suppliers.name UNIQUE (из миграции 009). Если Merlion/Treolan уже есть
    # от миграций — переиспользуем существующие id, иначе INSERT.
    row = db_session.execute(
        _t("SELECT id FROM suppliers WHERE name = :n"),
        {"n": name},
    ).first()
    if row is None:
        row = db_session.execute(
            _t(
                "INSERT INTO suppliers (name, is_active, email) "
                "VALUES (:n, TRUE, :e) RETURNING id"
            ),
            {"n": name, "e": email},
        ).first()
    else:
        db_session.execute(
            _t("UPDATE suppliers SET email = :e WHERE id = :id"),
            {"e": email, "id": int(row.id)},
        )
    db_session.commit()
    return int(row.id)


def _insert_cpu(db_session, *, sku: str, model: str = "Intel Core i5-12400F") -> int:
    row = db_session.execute(
        _t(
            "INSERT INTO cpus (sku, manufacturer, model, socket, cores, threads, "
            "                  base_clock_ghz, turbo_clock_ghz, tdp_watts) "
            "VALUES (:sku, 'Intel Corporation', :m, 'LGA1700', 6, 12, "
            "        2.5, 4.4, 65) RETURNING id"
        ),
        {"sku": sku, "m": model},
    ).first()
    db_session.commit()
    return int(row.id)


def _insert_ram(db_session, *, sku: str, model: str = "Kingston 8GB DDR4") -> int:
    row = db_session.execute(
        _t(
            "INSERT INTO rams (sku, manufacturer, model, memory_type, form_factor, "
            "                   module_size_gb, modules_count, frequency_mhz) "
            "VALUES (:sku, 'Kingston', :m, 'DDR4', 'DIMM', 8, 2, 3200) "
            "RETURNING id"
        ),
        {"sku": sku, "m": model},
    ).first()
    db_session.commit()
    return int(row.id)


def _insert_supplier_price(
    db_session,
    *,
    supplier_id: int,
    category: str,
    component_id: int,
    price: float,
    currency: str = "RUB",
    supplier_sku: str | None = None,
    stock: int = 10,
) -> None:
    db_session.execute(
        _t(
            "INSERT INTO supplier_prices "
            "  (supplier_id, category, component_id, supplier_sku, "
            "   price, currency, stock_qty) "
            "VALUES (:sid, :cat, :cid, :sku, :p, :c, :st)"
        ),
        {
            "sid": supplier_id, "cat": category, "cid": component_id,
            "sku": supplier_sku, "p": price, "c": currency, "st": stock,
        },
    )
    db_session.commit()


def _make_query(
    db_session,
    *,
    project_id: int,
    user_id: int,
    comps: list[dict],
    manufacturer: str = "Intel",
) -> int:
    """comps = [{category, component_id, model, sku, manufacturer, quantity}, …]."""
    build_result = {
        "status": "ok",
        "variants": [
            {
                "manufacturer": manufacturer,
                "path_used":    "default",
                "used_transit": False,
                "total_usd":    100.0,
                "total_rub":    9000.0,
                "components":   comps,
                "warnings":     [],
            },
        ],
        "refusal_reason": None,
        "usd_rub_rate":   90.0,
        "fx_source":      "fallback",
    }
    row = db_session.execute(
        _t(
            "INSERT INTO queries "
            "  (project_id, user_id, raw_text, build_result_json, status, "
            "   cost_usd, cost_rub) "
            "VALUES (:pid, :uid, :rt, CAST(:br AS JSONB), 'ok', 0, 0) "
            "RETURNING id"
        ),
        {
            "pid": project_id, "uid": user_id, "rt": "тест email",
            "br":  json.dumps(build_result, ensure_ascii=False),
        },
    ).first()
    db_session.commit()
    return int(row.id)


def _mock_rate(rate_str: str = "90"):
    return patch(
        "app.services.export.email_composer.exchange_rate.get_usd_rate",
        return_value=(Decimal(rate_str), date(2026, 4, 24), "cache"),
    )


# --- Тесты ---------------------------------------------------------------


def test_single_winner_supplier_produces_one_draft(db_session):
    """Один поставщик побеждает по всем позициям → 1 email со всеми строками."""
    uid = _seed_user(db_session, login="em-single")
    sid = _insert_supplier(db_session, name="Sup-Only", email="only@sup.ru")
    pid = spec_service.create_empty_project(
        db_session, user_id=uid, name="Проект-один",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-s")
    ram_id = _insert_ram(db_session, sku="ram-s")
    _insert_supplier_price(db_session, supplier_id=sid,
                           category="cpu", component_id=cpu_id,
                           price=18000, supplier_sku="sup-cpu")
    _insert_supplier_price(db_session, supplier_id=sid,
                           category="ram", component_id=ram_id,
                           price=3000, supplier_sku="sup-ram")
    qid = _make_query(db_session, project_id=pid, user_id=uid, comps=[
        {"category": "cpu", "component_id": cpu_id,
         "model": "Intel Core i5-12400F", "sku": "CPU-MPN",
         "manufacturer": "Intel", "quantity": 1},
        {"category": "ram", "component_id": ram_id,
         "model": "Kingston 8GB DDR4", "sku": "RAM-MPN",
         "manufacturer": "Kingston", "quantity": 2},
    ])
    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=1)

    with _mock_rate():
        drafts = email_composer.build_supplier_emails(pid, db_session)

    assert len(drafts) == 1
    d = drafts[0]
    assert d.supplier_id == sid
    assert d.supplier_name == "Sup-Only"
    assert d.to_email == "only@sup.ru"
    assert d.subject == "Проект-один"
    assert d.items_count == 2
    # Таблица с 3 колонками — ищем в теле.
    assert '<th>Артикул</th>' in d.body_html
    assert '<th>Наименование</th>' in d.body_html
    assert '<th>Кол-во</th>' in d.body_html
    # Артикул поставщика (supplier_sku) использован как основной.
    assert 'sup-cpu' in d.body_html
    assert 'sup-ram' in d.body_html
    # Приветствие и подпись.
    assert '<p>Привет!</p>' in d.body_html
    assert 'КВАДРО-ТЕХ' in d.body_html


def test_three_suppliers_get_three_drafts(db_session):
    """Три поставщика, у каждого своя победившая позиция → 3 email."""
    uid = _seed_user(db_session, login="em-three")
    s_a = _insert_supplier(db_session, name="A-Sup", email="a@sup.ru")
    s_b = _insert_supplier(db_session, name="B-Sup", email="b@sup.ru")
    s_c = _insert_supplier(db_session, name="C-Sup", email="c@sup.ru")
    pid = spec_service.create_empty_project(
        db_session, user_id=uid, name="Три поставщика",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-3")
    ram_id = _insert_ram(db_session, sku="ram-3")

    # A дешевле всех на CPU; B дешевле всех на RAM; C на обеих дороже всех.
    _insert_supplier_price(db_session, supplier_id=s_a, category="cpu",
                           component_id=cpu_id, price=10000, supplier_sku="a-cpu")
    _insert_supplier_price(db_session, supplier_id=s_b, category="cpu",
                           component_id=cpu_id, price=11000, supplier_sku="b-cpu")
    _insert_supplier_price(db_session, supplier_id=s_c, category="cpu",
                           component_id=cpu_id, price=12000, supplier_sku="c-cpu")
    _insert_supplier_price(db_session, supplier_id=s_a, category="ram",
                           component_id=ram_id, price=5000, supplier_sku="a-ram")
    _insert_supplier_price(db_session, supplier_id=s_b, category="ram",
                           component_id=ram_id, price=3000, supplier_sku="b-ram")
    _insert_supplier_price(db_session, supplier_id=s_c, category="ram",
                           component_id=ram_id, price=4000, supplier_sku="c-ram")
    # Чтобы у C была хоть одна победа, добавим GPU-позицию. Создадим gpu запись
    # (чтобы FK supplier_prices.component_id указывал на существующий gpu).
    db_session.execute(_t(
        "INSERT INTO gpus (sku, manufacturer, model, vram_gb, vram_type, "
        "                  tdp_watts, needs_extra_power, video_outputs, "
        "                  core_clock_mhz, memory_clock_mhz) "
        "VALUES ('G-3','Palit','RTX 3050',8,'GDDR6',115,TRUE,'HDMI',1500,14000)"
    ))
    gpu_id = db_session.execute(_t("SELECT currval('gpus_id_seq')")).scalar()
    db_session.commit()
    _insert_supplier_price(db_session, supplier_id=s_a, category="gpu",
                           component_id=gpu_id, price=30000)
    _insert_supplier_price(db_session, supplier_id=s_b, category="gpu",
                           component_id=gpu_id, price=28000)
    _insert_supplier_price(db_session, supplier_id=s_c, category="gpu",
                           component_id=gpu_id, price=25000, supplier_sku="c-gpu")

    qid = _make_query(db_session, project_id=pid, user_id=uid, comps=[
        {"category": "cpu", "component_id": cpu_id, "model": "CPU-model",
         "sku": "CPU-MPN", "manufacturer": "Intel", "quantity": 1},
        {"category": "ram", "component_id": ram_id, "model": "RAM-model",
         "sku": "RAM-MPN", "manufacturer": "Kingston", "quantity": 1},
        {"category": "gpu", "component_id": int(gpu_id), "model": "RTX 3050",
         "sku": "GPU-MPN", "manufacturer": "Palit", "quantity": 1},
    ])
    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=1)

    with _mock_rate():
        drafts = email_composer.build_supplier_emails(pid, db_session)

    by_name = {d.supplier_name: d for d in drafts}
    assert set(by_name.keys()) == {"A-Sup", "B-Sup", "C-Sup"}
    # A выиграл CPU
    assert by_name["A-Sup"].items_count == 1
    assert "a-cpu" in by_name["A-Sup"].body_html
    # B выиграл RAM
    assert by_name["B-Sup"].items_count == 1
    assert "b-ram" in by_name["B-Sup"].body_html
    # C выиграл GPU
    assert by_name["C-Sup"].items_count == 1
    assert "c-gpu" in by_name["C-Sup"].body_html


def test_tiebreak_by_min_supplier_id(db_session):
    """Одинаковая цена у двух поставщиков → победитель с меньшим id."""
    uid = _seed_user(db_session, login="em-tie")
    s_low = _insert_supplier(db_session, name="Low-ID", email="l@sup.ru")
    s_hi  = _insert_supplier(db_session, name="Hi-ID",  email="h@sup.ru")
    assert s_low < s_hi
    pid = spec_service.create_empty_project(
        db_session, user_id=uid, name="Тайбрейк",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-tie")
    _insert_supplier_price(db_session, supplier_id=s_low, category="cpu",
                           component_id=cpu_id, price=10000, supplier_sku="low-cpu")
    _insert_supplier_price(db_session, supplier_id=s_hi,  category="cpu",
                           component_id=cpu_id, price=10000, supplier_sku="hi-cpu")
    qid = _make_query(db_session, project_id=pid, user_id=uid, comps=[
        {"category": "cpu", "component_id": cpu_id, "model": "Intel CPU",
         "sku": "CPU-MPN", "manufacturer": "Intel", "quantity": 1},
    ])
    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=1)

    with _mock_rate():
        drafts = email_composer.build_supplier_emails(pid, db_session)

    assert len(drafts) == 1
    assert drafts[0].supplier_id == s_low
    assert "low-cpu" in drafts[0].body_html


def test_qty_sums_across_configurations(db_session):
    """Один компонент в двух конфигурациях проекта → qty суммируется.

    Две spec_items на одну и ту же CPU: quantity=2 и quantity=3, компонент
    в варианте quantity=1 → 2+3 = 5 в письме.
    """
    uid = _seed_user(db_session, login="em-qty")
    sid = _insert_supplier(db_session, name="Qty-Sup", email="q@sup.ru")
    pid = spec_service.create_empty_project(
        db_session, user_id=uid, name="QtySum",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-qty")
    _insert_supplier_price(db_session, supplier_id=sid, category="cpu",
                           component_id=cpu_id, price=10000, supplier_sku="qty-cpu")

    # Две разные конфигурации (query с разным manufacturer) — на самом деле
    # variant_manufacturer может совпадать только если это разные query_id.
    q1 = _make_query(db_session, project_id=pid, user_id=uid, comps=[
        {"category": "cpu", "component_id": cpu_id, "model": "Intel CPU",
         "sku": "CPU-MPN", "manufacturer": "Intel", "quantity": 1},
    ])
    q2 = _make_query(db_session, project_id=pid, user_id=uid, comps=[
        {"category": "cpu", "component_id": cpu_id, "model": "Intel CPU",
         "sku": "CPU-MPN", "manufacturer": "Intel", "quantity": 1},
    ])
    spec_service.select_variant(db_session, project_id=pid, query_id=q1,
                                manufacturer="Intel", quantity=2)
    spec_service.select_variant(db_session, project_id=pid, query_id=q2,
                                manufacturer="Intel", quantity=3)

    with _mock_rate():
        drafts = email_composer.build_supplier_emails(pid, db_session)

    assert len(drafts) == 1
    d = drafts[0]
    assert d.items_count == 1
    # Ожидаем число «5» в ячейке количества. Ищем как tcd с 5 среди кол-во.
    assert 'text-align:center;">5</td>' in d.body_html


def test_supplier_without_email_still_returns_draft(db_session):
    """Поставщик без email → to_email=None, но драфт всё равно отдан."""
    uid = _seed_user(db_session, login="em-noem")
    sid = _insert_supplier(db_session, name="NoEmail-Sup", email=None)
    pid = spec_service.create_empty_project(
        db_session, user_id=uid, name="NoEmail",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-ne")
    _insert_supplier_price(db_session, supplier_id=sid, category="cpu",
                           component_id=cpu_id, price=10000)
    qid = _make_query(db_session, project_id=pid, user_id=uid, comps=[
        {"category": "cpu", "component_id": cpu_id, "model": "Intel CPU",
         "sku": "CPU-MPN", "manufacturer": "Intel", "quantity": 1},
    ])
    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=1)

    with _mock_rate():
        drafts = email_composer.build_supplier_emails(pid, db_session)

    assert len(drafts) == 1
    assert drafts[0].to_email is None


def test_body_html_has_greeting_table_and_signature(db_session):
    """Контент письма: приветствие, таблица, подпись."""
    uid = _seed_user(db_session, login="em-content")
    sid = _insert_supplier(db_session, name="Content-Sup", email="c@sup.ru")
    pid = spec_service.create_empty_project(
        db_session, user_id=uid, name="Контент",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-ct")
    _insert_supplier_price(db_session, supplier_id=sid, category="cpu",
                           component_id=cpu_id, price=1, supplier_sku="ct-cpu")
    qid = _make_query(db_session, project_id=pid, user_id=uid, comps=[
        {"category": "cpu", "component_id": cpu_id, "model": "Intel CPU",
         "sku": "CPU-MPN", "manufacturer": "Intel", "quantity": 1},
    ])
    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=1)

    with _mock_rate():
        drafts = email_composer.build_supplier_emails(pid, db_session)

    html_body = drafts[0].body_html
    # Приветствие
    assert '<p>Привет!</p>' in html_body
    # Таблица
    assert '<table' in html_body and '</table>' in html_body
    # Три заголовка ровно
    assert html_body.count('<th>') == 3
    # Подпись
    assert 'КВАДРО-ТЕХ' in html_body
    assert 'Казань' in html_body
    assert 'https://www.quadro.tatar' in html_body


def test_supplier_sku_null_falls_back_to_mpn(db_session):
    """Если supplier_sku = NULL, первая колонка = sku компонента (MPN)."""
    uid = _seed_user(db_session, login="em-mpn")
    sid = _insert_supplier(db_session, name="MPN-Sup", email="m@sup.ru")
    pid = spec_service.create_empty_project(
        db_session, user_id=uid, name="MPN fallback",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-mpn")
    _insert_supplier_price(db_session, supplier_id=sid, category="cpu",
                           component_id=cpu_id, price=1, supplier_sku=None)
    qid = _make_query(db_session, project_id=pid, user_id=uid, comps=[
        {"category": "cpu", "component_id": cpu_id, "model": "Intel CPU",
         "sku": "MY-MPN-XYZ", "manufacturer": "Intel", "quantity": 1},
    ])
    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=1)

    with _mock_rate():
        drafts = email_composer.build_supplier_emails(pid, db_session)

    assert "MY-MPN-XYZ" in drafts[0].body_html


def test_empty_project_returns_no_drafts(db_session):
    """Пустой проект (без спецификации) → пустой список драфтов."""
    uid = _seed_user(db_session, login="em-empty")
    pid = spec_service.create_empty_project(
        db_session, user_id=uid, name="Empty",
    )
    with _mock_rate():
        drafts = email_composer.build_supplier_emails(pid, db_session)
    assert drafts == []


# --- Этап 9Г.1: регрессия на хардкод http:// в письмах ------------------


def test_supplier_email_no_hardcoded_http(db_session):
    """В теле письма поставщику не должно быть подстрок http://config…,
    http://app… или http://localhost… — все ссылки должны быть https://
    (либо вычисляться из settings.configurator_url на проде).

    Текущая реализация email_composer ставит в тело только подпись со
    ссылкой https://www.quadro.tatar и таблицу позиций; этот тест ловит
    регресс, если кто-то добавит ссылку на проект/конфигуратор и забудет
    про https.
    """
    uid = _seed_user(db_session, login="em-https")
    sid = _insert_supplier(db_session, name="HTTPS-Sup", email="https@sup.ru")
    pid = spec_service.create_empty_project(
        db_session, user_id=uid, name="HTTPS Test",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-https")
    _insert_supplier_price(db_session, supplier_id=sid, category="cpu",
                           component_id=cpu_id, price=1, supplier_sku="https-cpu")
    qid = _make_query(db_session, project_id=pid, user_id=uid, comps=[
        {"category": "cpu", "component_id": cpu_id, "model": "Intel CPU",
         "sku": "CPU-HTTPS", "manufacturer": "Intel", "quantity": 1},
    ])
    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=1)

    with _mock_rate():
        drafts = email_composer.build_supplier_emails(pid, db_session)

    body = drafts[0].body_html
    for forbidden in ("http://config", "http://app", "http://localhost"):
        assert forbidden not in body, (
            f"В теле письма не должно быть {forbidden!r}"
        )
