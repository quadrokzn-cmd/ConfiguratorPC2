"""Тесты этапа 9А.2.

Покрывают:
  - /admin/suppliers — список, создание, редактирование, фильтр is_active
    в выборе цен;
  - /admin/components — список с фильтрами, редактирование характеристик,
    is_hidden=True исключает компонент из подбора и NLU-поиска;
  - адаптация хлебных крошек: корневые без «Главная /», вложенные с полным путём;
  - визуальные ассерты (kt-table на /admin/suppliers, card-wrapper на /history).
"""

from __future__ import annotations

import pytest
from sqlalchemy import text


# ----------------------------------------------------------------------
#  Поставщики (этап 9А.2 — закрытие техдолга 8.3)
# ----------------------------------------------------------------------

def _seed_supplier(db, *, name, email="x@example.com",
                   is_active=True, contact_person=None):
    """Создаёт поставщика. ON CONFLICT UPDATE — чтобы не падать
    на seed-записях из миграций 001/009 (OCS/Merlion/Treolan)."""
    row = db.execute(
        text(
            "INSERT INTO suppliers (name, email, contact_person, is_active) "
            "VALUES (:n, :e, :cp, :a) "
            "ON CONFLICT (name) DO UPDATE SET email = EXCLUDED.email, "
            "    contact_person = EXCLUDED.contact_person, "
            "    is_active = EXCLUDED.is_active "
            "RETURNING id"
        ),
        {"n": name, "e": email, "cp": contact_person, "a": is_active},
    ).first()
    db.commit()
    return int(row.id)


def test_suppliers_list_admin_renders(admin_client):
    r = admin_client.get("/admin/suppliers")
    assert r.status_code == 200
    assert "Поставщики" in r.text


def test_suppliers_list_manager_forbidden(manager_client):
    r = manager_client.get("/admin/suppliers")
    assert r.status_code == 403


def test_admin_suppliers_uses_kt_table(admin_client, db_session):
    _seed_supplier(db_session, name="StubSupp9A2")
    r = admin_client.get("/admin/suppliers")
    assert r.status_code == 200
    # На странице используется компонент дизайн-системы .kt-table —
    # критерий «приведено к новой системе таблиц».
    assert 'class="kt-table"' in r.text


def test_supplier_create_works(admin_client, db_session):
    # Берём CSRF из формы создания.
    r = admin_client.get("/admin/suppliers/new")
    assert r.status_code == 200
    import re
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    assert m
    token = m.group(1)

    r = admin_client.post(
        "/admin/suppliers/new",
        data={
            "csrf_token":     token,
            "name":           "NewSupplier9A2",
            "email":          "n@example.com",
            "contact_person": "Иван",
            "contact_phone":  "+7 999",
            "is_active":      "on",
        },
    )
    assert r.status_code in (302, 303)
    # Запись действительно появилась в БД.
    row = db_session.execute(
        text("SELECT id, email, contact_person FROM suppliers WHERE name='NewSupplier9A2'")
    ).first()
    assert row is not None
    assert row.email == "n@example.com"
    assert row.contact_person == "Иван"


def test_supplier_edit_email(admin_client, db_session):
    sid = _seed_supplier(db_session, name="EditSupp9A2", email="old@x.ru")

    r = admin_client.get(f"/admin/suppliers/{sid}/edit")
    assert r.status_code == 200
    import re
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    token = m.group(1)

    r = admin_client.post(
        f"/admin/suppliers/{sid}/edit",
        data={
            "csrf_token": token,
            "name":       "EditSupp9A2",
            "email":      "new@x.ru",
            "is_active":  "on",
        },
    )
    assert r.status_code in (302, 303)

    row = db_session.execute(
        text("SELECT email FROM suppliers WHERE id=:id"), {"id": sid}
    ).first()
    assert row.email == "new@x.ru"


def _seed_cpu_with_supplier(db, *, supplier_id: int, price: float = 100.0):
    """Создаёт один CPU + одну запись в supplier_prices."""
    row = db.execute(
        text(
            "INSERT INTO cpus (model, manufacturer, sku, socket, cores, threads, "
            "                  base_clock_ghz, tdp_watts, has_integrated_graphics, "
            "                  memory_type, package_type) "
            "VALUES ('Test CPU', 'Intel', 'TEST-CPU-1', 'LGA1700', 6, 12, 3.0, 65, "
            "        FALSE, 'DDR5', 'BOX') RETURNING id"
        ),
    ).first()
    cpu_id = int(row.id)
    db.execute(
        text(
            "INSERT INTO supplier_prices "
            "(category, component_id, supplier_id, supplier_sku, price, currency, "
            " stock_qty, transit_qty) "
            "VALUES ('cpu', :cid, :sid, 'SP-1', :p, 'USD', 5, 0)"
        ),
        {"cid": cpu_id, "sid": supplier_id, "p": price},
    )
    db.commit()
    return cpu_id


def test_supplier_inactive_excluded_from_prices(db_session):
    """Поставщик is_active=False не появляется в fetch_offers."""
    from app.services.configurator.prices import fetch_offers

    sid_off = _seed_supplier(db_session, name="OffSupplier9A2", is_active=False)
    sid_on = _seed_supplier(db_session, name="OnSupplier9A2", is_active=True)
    cpu_id = _seed_cpu_with_supplier(db_session, supplier_id=sid_off)
    # Также добавим строку для активного поставщика.
    db_session.execute(
        text(
            "INSERT INTO supplier_prices "
            "(category, component_id, supplier_id, supplier_sku, price, currency, "
            " stock_qty, transit_qty) "
            "VALUES ('cpu', :cid, :sid, 'SP-2', 90, 'USD', 3, 0)"
        ),
        {"cid": cpu_id, "sid": sid_on},
    )
    db_session.commit()

    offers = fetch_offers(
        db_session, category="cpu", component_id=cpu_id,
        usd_rub=90.0, allow_transit=False,
    )
    suppliers = {o.supplier for o in offers}
    assert "OnSupplier9A2" in suppliers
    assert "OffSupplier9A2" not in suppliers


# ----------------------------------------------------------------------
#  Компоненты (этап 9А.2 — закрытие техдолга обогащения)
# ----------------------------------------------------------------------

def _seed_cooler(db, *, model="Cool 1", max_tdp=None, hidden=False):
    row = db.execute(
        text(
            "INSERT INTO coolers (model, manufacturer, sku, max_tdp_watts, "
            "                     supported_sockets, is_hidden) "
            "VALUES (:m, 'Test', :sku, :tdp, ARRAY['LGA1700'], :h) RETURNING id"
        ),
        {"m": model, "sku": f"CL-{model}", "tdp": max_tdp, "h": hidden},
    ).first()
    db.commit()
    return int(row.id)


def test_components_list_filters(admin_client, db_session):
    _seed_cooler(db_session, model="Noctua NH-U12S")
    _seed_cooler(db_session, model="DeepCool AK620")

    # Фильтр по категории.
    r = admin_client.get("/admin/components?category=cooler")
    assert r.status_code == 200
    assert "Noctua NH-U12S" in r.text
    assert "DeepCool AK620" in r.text

    # Поиск по подстроке model.
    r = admin_client.get("/admin/components?category=cooler&q=Noctua")
    assert r.status_code == 200
    assert "Noctua NH-U12S" in r.text
    assert "DeepCool AK620" not in r.text


def test_component_edit_max_tdp(admin_client, db_session):
    cid = _seed_cooler(db_session, model="Cool X", max_tdp=None)

    r = admin_client.get(f"/admin/components/cooler/{cid}")
    assert r.status_code == 200
    import re
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
    token = m.group(1)

    r = admin_client.post(
        f"/admin/components/cooler/{cid}/edit",
        data={
            "csrf_token":         token,
            "max_tdp_watts":      "180",
            "supported_sockets":  "LGA1700, AM5",
        },
    )
    assert r.status_code in (302, 303)

    row = db_session.execute(
        text("SELECT max_tdp_watts, supported_sockets FROM coolers WHERE id=:id"),
        {"id": cid},
    ).first()
    assert row.max_tdp_watts == 180
    assert "LGA1700" in row.supported_sockets
    assert "AM5" in row.supported_sockets


def test_hidden_component_not_in_search(db_session):
    """Скрытый компонент не находится через NLU fuzzy_lookup."""
    from app.services.nlu.fuzzy_lookup import find
    from app.services.nlu.schema import ModelMention

    # Видимый кулер — должен находиться по запросу.
    cid_visible = _seed_cooler(db_session, model="DeepCool AK620 visible")
    # Скрытый кулер — не должен.
    cid_hidden = _seed_cooler(db_session, model="DeepCool AK620 hidden", hidden=True)

    # Поиск общим запросом «AK620»: видимый найдётся, скрытый — нет.
    r = find(db_session, ModelMention(category="cooler", query="AK620"))
    # Есть ли совпадение на видимый?
    if r.found_id is not None:
        assert r.found_id != cid_hidden
        # Хорошо, если попался видимый — но не обязательно (поиск может
        # вернуть None если ни одного компонента нет в стоке). Главное:
        # скрытого id точно нет в результате.

    # Точечный поиск по SKU скрытого: не должен найтись.
    r2 = find(db_session, ModelMention(category="cooler", query="CL-DeepCool AK620 hidden"))
    assert r2.found_id != cid_hidden


def test_hidden_cooler_not_in_configurator_candidates(db_session):
    """Скрытый кулер не появляется в configurator.get_cheapest_cooler."""
    from app.services.configurator.candidates import get_cheapest_cooler

    sid = _seed_supplier(db_session, name="HiddenTestSupp9A2")
    cid_hidden = _seed_cooler(db_session, model="HCool", max_tdp=200, hidden=True)
    # Цена для скрытого
    db_session.execute(
        text(
            "INSERT INTO supplier_prices "
            "(category, component_id, supplier_id, supplier_sku, price, currency, "
            " stock_qty, transit_qty) "
            "VALUES ('cooler', :cid, :sid, 'HSP', 50, 'USD', 5, 0)"
        ),
        {"cid": cid_hidden, "sid": sid},
    )
    db_session.commit()

    res = get_cheapest_cooler(
        db_session, cpu_socket="LGA1700", required_tdp=65,
        fixed=None, usd_rub=90.0, allow_transit=False,
    )
    # Скрытого кулера в результате быть не должно (других нет → None).
    assert res is None


# ----------------------------------------------------------------------
#  Хлебные крошки (этап 9А.2 — корневые / вложенные)
# ----------------------------------------------------------------------

def test_breadcrumbs_root_pages_no_home_prefix(manager_client):
    """На корневых страницах (Проекты, История) в крошках НЕТ слова
    «Главная» и нет crumb-sep — только текущий раздел одним словом."""
    for url in ("/projects", "/history"):
        r = manager_client.get(url)
        assert r.status_code == 200, f"{url} → {r.status_code}"
        # Извлечём содержимое блока breadcrumbs <nav class="breadcrumbs ...">…</nav>.
        import re
        m = re.search(
            r'<nav class="breadcrumbs[^"]*"[^>]*>(.*?)</nav>',
            r.text, re.DOTALL,
        )
        assert m, f"breadcrumbs nav не найден на {url}"
        crumbs = m.group(1)
        assert "Главная" not in crumbs, f"{url}: «Главная» не должна быть в крошках"
        assert 'class="crumb-sep"' not in crumbs, (
            f"{url}: на корневой странице слэш-разделителя быть не должно"
        )


def test_breadcrumbs_nested_pages_full_path(manager_client, db_session, manager_user):
    """На вложенной /project/{id} в крошках виден полный путь:
    «Проекты / {имя проекта}»."""
    pid = db_session.execute(
        text(
            "INSERT INTO projects (user_id, name) VALUES (:u, 'Тест проект') RETURNING id"
        ),
        {"u": manager_user["id"]},
    ).first()
    db_session.commit()
    pid = int(pid.id)

    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200
    import re
    m = re.search(
        r'<nav class="breadcrumbs[^"]*"[^>]*>(.*?)</nav>',
        r.text, re.DOTALL,
    )
    assert m
    crumbs = m.group(1)
    # Ссылка на корневой раздел Проекты.
    assert 'href="/projects"' in crumbs
    assert "Проекты" in crumbs
    # Имя текущего проекта.
    assert "Тест проект" in crumbs
    # Слэш-разделитель.
    assert 'class="crumb-sep"' in crumbs


# ----------------------------------------------------------------------
#  Визуальные ассерты дизайн-системы
# ----------------------------------------------------------------------

def test_history_page_has_card_wrapper(manager_client, db_session, manager_user):
    """Страница /history рендерится в карточке дизайн-системы.

    Раньше использовалась устаревшая разметка bg-zinc-800/border-zinc-700.
    После 9А.2 — компонент .card / .card-pad-lg / .card-pad."""
    r = manager_client.get("/history")
    assert r.status_code == 200
    # Хотя бы один контейнер класса card должен быть.
    assert 'class="card' in r.text
    # И никаких остаточных bg-zinc-* / border-zinc-* — они должны были
    # быть полностью заменены.
    assert "bg-zinc-800" not in r.text
    assert "border-zinc-700" not in r.text
