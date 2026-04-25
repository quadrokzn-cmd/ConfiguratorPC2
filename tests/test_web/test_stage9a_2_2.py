"""Тесты этапа 9А.2.2 — финальная полировка.

Покрывают:
  - Блок A: тугой viewBox у quadro-logo.svg + класс .kt-brand-logo
    больше не имеет жёсткого max-height (логотип заполняет
    всю ширину обёртки).
  - Блок B: на странице /project/{id} с непустой спецификацией
    рендерится кнопка «Пересчитать цены» и точечные иконки
    refresh у каждой строки.
  - Блок C: новая панель фильтров /admin/components — селект
    «Статус» вместо двух чекбоксов, нет кнопки «Применить»,
    фильтрация и сортировка через query-параметры, активные
    фильтры рендерятся как chip'ы с крестиком, partial=1
    отдаёт partial-фрагмент.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from sqlalchemy import text


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


# =====================================================================
# Блок A — логотип
# =====================================================================

def test_logo_svg_has_tight_viewbox():
    """ViewBox SVG-логотипа после 9А.2.2 — тугой, обнимает только
    содержимое (а не дефолтный 0 0 526 421)."""
    p = _project_root() / "static" / "img" / "brand" / "quadro-logo.svg"
    text_data = p.read_text(encoding="utf-8")
    m = re.search(r'viewBox="([^"]+)"', text_data)
    assert m, "viewBox не найден в SVG"
    parts = m.group(1).split()
    assert len(parts) == 4, f"viewBox должен иметь 4 числа, получено {parts}"
    min_x, min_y, w, h = (float(x) for x in parts)
    # До 9А.2.2 viewBox был 0 0 526 421 — слишком широкий, контент
    # занимал ~63% ширины.  Теперь должен быть 100..120 .. 320..360.
    assert 100 <= min_x <= 120, f"min_x={min_x}: viewBox не сдвинут к контенту"
    assert 150 <= min_y <= 170, f"min_y={min_y}: viewBox не сдвинут к контенту"
    assert 300 <= w <= 360, f"width={w}: viewBox не обрезан"
    assert 80 <= h <= 110, f"height={h}: viewBox не обрезан"


def test_logo_svg_parses_as_xml():
    """SVG-файл валидный XML."""
    import xml.etree.ElementTree as ET
    p = _project_root() / "static" / "img" / "brand" / "quadro-logo.svg"
    tree = ET.parse(str(p))
    root = tree.getroot()
    assert root.tag.endswith("svg"), f"Корневой тег не svg: {root.tag}"
    assert "viewBox" in root.attrib


def test_brand_logo_class_no_max_height():
    """Класс .kt-brand-logo больше не ограничен max-height — после
    исправления тугого viewBox логотип сам ужмётся в обёртке 180px."""
    css_path = _project_root() / "static" / "dist" / "main.css"
    css = css_path.read_text(encoding="utf-8")
    # Достаём блок .kt-brand-logo (минифицированный CSS — одна строка).
    m = re.search(r"\.kt-brand-logo\{([^}]*)\}", css)
    assert m, ".kt-brand-logo не найден в собранном CSS"
    block = m.group(1)
    # 9А.2.2: убрали max-height (тугой viewBox теперь сам определяет
    # высоту, лишний потолок только мешал).
    assert "max-height" not in block, (
        "После 9А.2.2 в .kt-brand-logo не должно быть max-height: "
        + block
    )


# =====================================================================
# Блок B — кнопки пересчёта цен
# =====================================================================

def _seed_supplier(db, *, name="SupB", is_active=True) -> int:
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


def _seed_cpu(db, *, model, supplier_id, price_usd=100.0) -> int:
    row = db.execute(
        text(
            "INSERT INTO cpus (model, manufacturer, sku, socket, cores, threads, "
            "                  base_clock_ghz, tdp_watts, has_integrated_graphics, "
            "                  memory_type, package_type) "
            "VALUES (:m, 'Intel', :sku, 'LGA1700', 6, 12, 3.0, 65, FALSE, "
            "        'DDR5', 'BOX') RETURNING id"
        ),
        {"m": model, "sku": f"SKU-{model}"},
    ).first()
    cpu_id = int(row.id)
    db.execute(
        text(
            "INSERT INTO supplier_prices "
            "(category, component_id, supplier_id, supplier_sku, price, currency, "
            " stock_qty, transit_qty) "
            "VALUES ('cpu', :cid, :sid, :ssku, :p, 'USD', 5, 0)"
        ),
        {"cid": cpu_id, "sid": supplier_id, "ssku": f"SUP-{model}", "p": price_usd},
    )
    db.commit()
    return cpu_id


def _make_query_with_cpu(db, *, project_id, user_id, cpu_id, model, price_usd=100.0):
    build_result = {
        "status": "ok",
        "variants": [
            {
                "manufacturer": "Intel",
                "path_used":    "default",
                "used_transit": False,
                "total_usd":    price_usd,
                "total_rub":    price_usd * 90.0,
                "components": [
                    {
                        "category":     "cpu",
                        "component_id": cpu_id,
                        "model":        model,
                        "sku":          f"SKU-{model}",
                        "manufacturer": "Intel",
                        "quantity":     1,
                        "supplier":     "SupB",
                        "supplier_sku": f"SUP-{model}",
                        "price_usd":    price_usd,
                        "price_rub":    price_usd * 90.0,
                        "stock":        5,
                        "in_transit":   False,
                        "also_available_at": [],
                    }
                ],
                "warnings": [],
            }
        ],
        "refusal_reason": None,
        "usd_rub_rate":   90.0,
        "fx_source":      "fallback",
    }
    row = db.execute(
        text(
            "INSERT INTO queries "
            "  (project_id, user_id, raw_text, build_result_json, status, "
            "   cost_usd, cost_rub) "
            "VALUES (:pid, :uid, 'тест', CAST(:br AS JSONB), 'ok', 0, 0) "
            "RETURNING id"
        ),
        {
            "pid": project_id, "uid": user_id,
            "br": json.dumps(build_result, ensure_ascii=False),
        },
    ).first()
    db.commit()
    return int(row.id)


def _seed_project_with_spec(db, manager_user, *, n_items=1) -> int:
    """Создаёт проект с n_items позициями в спецификации."""
    from app.services import spec_service
    uid = manager_user["id"]
    pid = spec_service.create_empty_project(db, user_id=uid, name="P-recalc-ui")
    sid = _seed_supplier(db, name=f"SupRC-{pid}")
    for i in range(n_items):
        model = f"CPU-RC-{pid}-{i}"
        cpu_id = _seed_cpu(db, model=model, supplier_id=sid, price_usd=100.0 + i)
        qid = _make_query_with_cpu(
            db, project_id=pid, user_id=uid, cpu_id=cpu_id,
            model=model, price_usd=100.0 + i,
        )
        spec_service.select_variant(
            db, project_id=pid, query_id=qid,
            manufacturer="Intel", quantity=1,
        )
    return pid


def test_project_page_has_recalc_button(manager_client, db_session, manager_user):
    """На странице проекта со спецификацией виден id="kt-spec-recalc-btn"
    и текст «Пересчитать цены»."""
    pid = _seed_project_with_spec(db_session, manager_user, n_items=1)
    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200, r.text
    assert 'id="kt-spec-recalc-btn"' in r.text, (
        "Кнопка #kt-spec-recalc-btn должна рендериться при непустой спецификации"
    )
    assert "Пересчитать цены" in r.text


def test_project_page_has_per_row_recalc_icons(
    manager_client, db_session, manager_user
):
    """В таблице спецификации число иконок refresh у строк = числу
    позиций (kt-spec-recalc-row у каждой строки)."""
    n = 3
    pid = _seed_project_with_spec(db_session, manager_user, n_items=n)
    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200, r.text
    matches = re.findall(r'class="kt-spec-recalc-row\b', r.text)
    assert len(matches) == n, (
        f"Ожидалось {n} иконок per-row recalc, найдено {len(matches)}"
    )


def test_project_page_no_recalc_button_when_empty(
    manager_client, db_session, manager_user
):
    """В пустом проекте кнопка пересчёта не должна рендериться."""
    from app.services import spec_service
    pid = spec_service.create_empty_project(
        db_session, user_id=manager_user["id"], name="P-empty"
    )
    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200
    assert 'id="kt-spec-recalc-btn"' not in r.text


# =====================================================================
# Блок C — редизайн фильтров /admin/components
# =====================================================================

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


def test_components_filter_status_select_present(admin_client):
    """В шаблоне /admin/components есть селект id=f-status вместо
    чекбоксов «Только скелеты» / «Только скрытые»."""
    r = admin_client.get("/admin/components")
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


def test_components_no_apply_button(admin_client):
    """Нет кнопки «Применить» — фильтры применяются мгновенно."""
    r = admin_client.get("/admin/components")
    assert r.status_code == 200
    # Допускаем «Применить» в каких-то посторонних местах, но НЕ в
    # фильтрах и НЕ в кнопке submit.
    # Простейшая проверка: нет <button type="submit"> с текстом «Применить».
    assert not re.search(
        r'<button[^>]*type="submit"[^>]*>\s*[^<]*?Применить', r.text
    ), "Кнопка submit «Применить» должна быть удалена"
    # И нет ссылки «сбросить» к /admin/components как кнопки очистки.
    assert ">сбросить</a>" not in r.text


def test_components_filter_via_status_query_param(admin_client, db_session):
    """GET /admin/components?status=skeleton возвращает только скелеты
    (max_tdp_watts NULL = скелет для cooler)."""
    full_id = _seed_cooler(
        db_session, model="Full Cool 9A22", max_tdp=180,
    )
    skel_id = _seed_cooler(
        db_session, model="Skel Cool 9A22", max_tdp=None,
    )
    hide_id = _seed_cooler(
        db_session, model="Hidden Cool 9A22", max_tdp=200, hidden=True,
    )

    r = admin_client.get("/admin/components?category=cooler&status=skeleton")
    assert r.status_code == 200
    assert "Skel Cool 9A22" in r.text
    assert "Full Cool 9A22" not in r.text
    # Скрытый кулер с max_tdp_watts=200 (не скелет) — не входит.
    assert "Hidden Cool 9A22" not in r.text


def test_components_filter_status_hidden(admin_client, db_session):
    """status=hidden возвращает только is_hidden=TRUE."""
    _seed_cooler(db_session, model="Plain Cool 9A22", max_tdp=180)
    _seed_cooler(db_session, model="Hidden A 9A22", max_tdp=180, hidden=True)
    r = admin_client.get("/admin/components?category=cooler&status=hidden")
    assert r.status_code == 200
    assert "Hidden A 9A22" in r.text
    assert "Plain Cool 9A22" not in r.text


def test_components_filter_status_with_price(admin_client, db_session):
    """status=with_price оставляет только компоненты с supplier_count > 0."""
    _seed_supplier(db_session, name="SupCWP")
    sid = db_session.execute(
        text("SELECT id FROM suppliers WHERE name='SupCWP'")
    ).first().id
    cooler_with = _seed_cooler(db_session, model="WithPrice 9A22", max_tdp=120)
    db_session.execute(
        text(
            "INSERT INTO supplier_prices "
            "(category, component_id, supplier_id, supplier_sku, price, currency, "
            " stock_qty, transit_qty) "
            "VALUES ('cooler', :cid, :sid, 'SP1', 50, 'USD', 5, 0)"
        ),
        {"cid": cooler_with, "sid": sid},
    )
    _seed_cooler(db_session, model="NoPrice 9A22", max_tdp=120)
    db_session.commit()

    r = admin_client.get("/admin/components?category=cooler&status=with_price")
    assert r.status_code == 200
    assert "WithPrice 9A22" in r.text
    assert "NoPrice 9A22" not in r.text


def test_components_sort_by_model_asc(admin_client, db_session):
    """sort=model,asc — компоненты упорядочены по модели."""
    _seed_cooler(db_session, model="Zeta Cool 9A22", max_tdp=120)
    _seed_cooler(db_session, model="Alpha Cool 9A22", max_tdp=120)

    r = admin_client.get("/admin/components?category=cooler&sort=model,asc")
    assert r.status_code == 200
    idx_a = r.text.find("Alpha Cool 9A22")
    idx_z = r.text.find("Zeta Cool 9A22")
    assert idx_a > 0 and idx_z > 0
    assert idx_a < idx_z, "При sort=model,asc Alpha должна быть выше Zeta"


def test_components_sort_by_model_desc(admin_client, db_session):
    """sort=model,desc — обратный порядок."""
    _seed_cooler(db_session, model="Zulu Cool 9A22", max_tdp=120)
    _seed_cooler(db_session, model="Bravo Cool 9A22", max_tdp=120)

    r = admin_client.get("/admin/components?category=cooler&sort=model,desc")
    assert r.status_code == 200
    idx_b = r.text.find("Bravo Cool 9A22")
    idx_z = r.text.find("Zulu Cool 9A22")
    assert idx_b > 0 and idx_z > 0
    assert idx_z < idx_b, "При sort=model,desc Zulu должна быть выше Bravo"


def test_components_active_filter_chips(admin_client):
    """Активные фильтры рендерятся как chip'ы с кнопкой-крестиком."""
    r = admin_client.get("/admin/components?category=cooler&status=skeleton")
    assert r.status_code == 200
    # Есть chip-контейнер и chip'ы для category и status.
    assert 'id="kt-active-filters"' in r.text
    assert 'class="kt-chip"' in r.text
    # У chip'ов есть кнопка-крестик с data-clear.
    assert 'data-clear="category"' in r.text
    assert 'data-clear="status"' in r.text
    # И ссылка «Сбросить все».
    assert 'id="kt-clear-all"' in r.text


def test_components_no_active_chips_when_no_filters(admin_client):
    """Если фильтров нет — кнопка «Сбросить все» не рендерится."""
    r = admin_client.get("/admin/components")
    assert r.status_code == 200
    assert 'id="kt-clear-all"' not in r.text


def test_components_partial_returns_only_table(admin_client):
    """?partial=1 отдаёт partial без <head>/<aside>/<header>."""
    r = admin_client.get("/admin/components?partial=1")
    assert r.status_code == 200
    # В partial нет <html>, <body>, <aside class="kt-sidebar">
    # и не наследуется base.html.
    assert "<html" not in r.text.lower()
    assert "kt-sidebar" not in r.text
    # Зато есть таблица или пустое состояние.
    assert ('class="kt-table' in r.text) or ('Ничего не найдено' in r.text) \
        or ('id="kt-active-filters"' in r.text)


def test_components_sortable_column_buttons_present(admin_client):
    """В заголовках столбцов есть кнопки .kt-sort-btn."""
    r = admin_client.get("/admin/components")
    assert r.status_code == 200
    assert 'class="kt-sort-btn' in r.text
    # Есть кнопки сортировки минимум для 4 столбцов (category, manufacturer,
    # model, price, status).
    n = len(re.findall(r'class="kt-sort-btn', r.text))
    assert n >= 5, f"Ожидалось ≥5 sort-кнопок, найдено {n}"


# =====================================================================
# Блок D — /projects и /history (фильтры уже живые из 9А.2)
# =====================================================================

def test_history_filters_already_live(manager_client, db_session, manager_user):
    """На /history фильтры уже живые из 9А.2 — нет кнопок «Применить»
    в форме фильтров. Регресс-тест: должны остаться живыми."""
    # Заведём один query, чтобы блок фильтров отрендерился.
    db_session.execute(
        text(
            "INSERT INTO projects (user_id, name) VALUES (:u, 'P-history-9A22')"
        ),
        {"u": manager_user["id"]},
    )
    pid_row = db_session.execute(
        text("SELECT id FROM projects WHERE name='P-history-9A22'")
    ).first()
    db_session.execute(
        text(
            "INSERT INTO queries (project_id, user_id, raw_text, "
            "                     build_result_json, status, cost_usd, cost_rub) "
            "VALUES (:pid, :uid, 'тест', '{}'::jsonb, 'ok', 0, 0)"
        ),
        {"pid": pid_row.id, "uid": manager_user["id"]},
    )
    db_session.commit()

    r = manager_client.get("/history")
    assert r.status_code == 200
    # Селект статуса есть как был.
    assert 'id="kt-history-status"' in r.text
    # И живой поиск.
    assert 'id="kt-history-search"' in r.text
    # Без кнопок «Применить».
    assert "Применить" not in r.text


def test_projects_search_already_live(manager_client):
    """На /projects поиск живой (без кнопок «Применить»)."""
    r = manager_client.get("/projects")
    assert r.status_code == 200
    # У страницы без проектов нет input'а — но и без кнопки.
    # На странице с проектами или без — нет «Применить» рядом с
    # поиском. Здесь проверяем наличие inputа когда проекты есть
    # либо отсутствие любых apply-кнопок в тексте «Применить».
    # Самое простое — что сама форма фильтров без «Применить»
    # (есть только «Новый проект» как primary btn).
    assert "Применить" not in r.text
