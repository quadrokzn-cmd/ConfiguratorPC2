"""Тесты фильтров и сортировки /databases/components (Блок C 9А.2.2).

Изначально тесты были на /admin/components в конфигураторе. На этапе
UI-2 Пути B (2026-05-11) страница и тесты переехали в портал.

Покрывают:
  - новая панель фильтров /databases/components — селект «Статус»
    вместо двух чекбоксов, нет кнопки «Применить»;
  - фильтрация и сортировка через query-параметры;
  - активные фильтры рендерятся как chip'ы с крестиком;
  - partial=1 отдаёт partial-фрагмент без layout-обвязки.
"""

from __future__ import annotations

import re

from sqlalchemy import text


def _seed_supplier(db, *, name="SupC", is_active=True) -> int:
    row = db.execute(
        text(
            "INSERT INTO suppliers (name, is_active) VALUES (:n, :a) "
            "ON CONFLICT (name) DO UPDATE SET is_active = EXCLUDED.is_active "
            "RETURNING id"
        ),
        {"n": name, "a": is_active},
    ).first()
    db.commit()
    return int(row.id)


def _seed_cooler(db, *, model="Cool C1", max_tdp=None, hidden=False) -> int:
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


def test_components_filter_status_select_present(admin_portal_client):
    """В шаблоне /databases/components есть селект id=f-status вместо
    чекбоксов «Только скелеты» / «Только скрытые»."""
    r = admin_portal_client.get("/databases/components")
    assert r.status_code == 200
    assert 'id="f-status"' in r.text
    assert "Полные карточки" in r.text
    assert "Скелеты" in r.text
    assert "С ценами" in r.text
    assert "Без цен" in r.text
    # Старых чекбоксов нет.
    assert 'name="skeletons"' not in r.text
    assert 'name="hidden"' not in r.text
    assert "Только скелеты" not in r.text
    assert "Только скрытые" not in r.text


def test_components_no_apply_button(admin_portal_client):
    """Нет кнопки «Применить» — фильтры применяются мгновенно."""
    r = admin_portal_client.get("/databases/components")
    assert r.status_code == 200
    # Простейшая проверка: нет <button type="submit"> с текстом «Применить».
    assert not re.search(
        r'<button[^>]*type="submit"[^>]*>\s*[^<]*?Применить', r.text
    ), "Кнопка submit «Применить» должна быть удалена"
    # И нет ссылки «сбросить» к /databases/components как кнопки очистки.
    assert ">сбросить</a>" not in r.text


def test_components_filter_via_status_query_param(admin_portal_client, db_session):
    """GET /databases/components?status=skeleton возвращает только скелеты
    (max_tdp_watts NULL = скелет для cooler)."""
    full_id = _seed_cooler(
        db_session, model="Full Cool UI2", max_tdp=180,
    )
    skel_id = _seed_cooler(
        db_session, model="Skel Cool UI2", max_tdp=None,
    )
    hide_id = _seed_cooler(
        db_session, model="Hidden Cool UI2", max_tdp=200, hidden=True,
    )

    r = admin_portal_client.get("/databases/components?category=cooler&status=skeleton")
    assert r.status_code == 200
    assert "Skel Cool UI2" in r.text
    assert "Full Cool UI2" not in r.text
    # Скрытый кулер с max_tdp_watts=200 (не скелет) — не входит.
    assert "Hidden Cool UI2" not in r.text


def test_components_filter_status_hidden(admin_portal_client, db_session):
    """status=hidden возвращает только is_hidden=TRUE."""
    _seed_cooler(db_session, model="Plain Cool UI2", max_tdp=180)
    _seed_cooler(db_session, model="Hidden A UI2", max_tdp=180, hidden=True)
    r = admin_portal_client.get("/databases/components?category=cooler&status=hidden")
    assert r.status_code == 200
    assert "Hidden A UI2" in r.text
    assert "Plain Cool UI2" not in r.text


def test_components_filter_status_with_price(admin_portal_client, db_session):
    """status=with_price оставляет только компоненты с supplier_count > 0."""
    _seed_supplier(db_session, name="SupCWP")
    sid = db_session.execute(
        text("SELECT id FROM suppliers WHERE name='SupCWP'")
    ).first().id
    cooler_with = _seed_cooler(db_session, model="WithPrice UI2", max_tdp=120)
    db_session.execute(
        text(
            "INSERT INTO supplier_prices "
            "(category, component_id, supplier_id, supplier_sku, price, currency, "
            " stock_qty, transit_qty) "
            "VALUES ('cooler', :cid, :sid, 'SP1', 50, 'USD', 5, 0)"
        ),
        {"cid": cooler_with, "sid": sid},
    )
    _seed_cooler(db_session, model="NoPrice UI2", max_tdp=120)
    db_session.commit()

    r = admin_portal_client.get("/databases/components?category=cooler&status=with_price")
    assert r.status_code == 200
    assert "WithPrice UI2" in r.text
    assert "NoPrice UI2" not in r.text


def test_components_sort_by_model_asc(admin_portal_client, db_session):
    """sort=model,asc — компоненты упорядочены по модели."""
    _seed_cooler(db_session, model="Zeta Cool UI2", max_tdp=120)
    _seed_cooler(db_session, model="Alpha Cool UI2", max_tdp=120)

    r = admin_portal_client.get("/databases/components?category=cooler&sort=model,asc")
    assert r.status_code == 200
    idx_a = r.text.find("Alpha Cool UI2")
    idx_z = r.text.find("Zeta Cool UI2")
    assert idx_a > 0 and idx_z > 0
    assert idx_a < idx_z, "При sort=model,asc Alpha должна быть выше Zeta"


def test_components_sort_by_model_desc(admin_portal_client, db_session):
    """sort=model,desc — обратный порядок."""
    _seed_cooler(db_session, model="Zulu Cool UI2", max_tdp=120)
    _seed_cooler(db_session, model="Bravo Cool UI2", max_tdp=120)

    r = admin_portal_client.get("/databases/components?category=cooler&sort=model,desc")
    assert r.status_code == 200
    idx_b = r.text.find("Bravo Cool UI2")
    idx_z = r.text.find("Zulu Cool UI2")
    assert idx_b > 0 and idx_z > 0
    assert idx_z < idx_b, "При sort=model,desc Zulu должна быть выше Bravo"


def test_components_active_filter_chips(admin_portal_client):
    """Активные фильтры рендерятся как chip'ы с кнопкой-крестиком."""
    r = admin_portal_client.get("/databases/components?category=cooler&status=skeleton")
    assert r.status_code == 200
    assert 'id="kt-active-filters"' in r.text
    assert 'class="kt-chip"' in r.text
    assert 'data-clear="category"' in r.text
    assert 'data-clear="status"' in r.text
    assert 'id="kt-clear-all"' in r.text


def test_components_no_active_chips_when_no_filters(admin_portal_client):
    """Если фильтров нет — кнопка «Сбросить все» не рендерится."""
    r = admin_portal_client.get("/databases/components")
    assert r.status_code == 200
    assert 'id="kt-clear-all"' not in r.text


def test_components_partial_returns_only_table(admin_portal_client):
    """?partial=1 отдаёт partial без <head>/<aside>/<header>."""
    r = admin_portal_client.get("/databases/components?partial=1")
    assert r.status_code == 200
    assert "<html" not in r.text.lower()
    assert "kt-sidebar" not in r.text
    assert ('class="kt-table' in r.text) or ('Ничего не найдено' in r.text) \
        or ('id="kt-active-filters"' in r.text)


def test_components_sortable_column_buttons_present(admin_portal_client):
    """В заголовках столбцов есть кнопки .kt-sort-btn."""
    r = admin_portal_client.get("/databases/components")
    assert r.status_code == 200
    assert 'class="kt-sort-btn' in r.text
    n = len(re.findall(r'class="kt-sort-btn', r.text))
    assert n >= 5, f"Ожидалось ≥5 sort-кнопок, найдено {n}"
