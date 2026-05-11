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
    from portal.services.configurator import spec_service
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
    """На странице проекта со спецификацией виден id="kt-spec-recalc-btn".
    9А.2.3: текст обновился — теперь это «Пересобрать конфигурации»."""
    pid = _seed_project_with_spec(db_session, manager_user, n_items=1)
    r = manager_client.get(f"/configurator/project/{pid}")
    assert r.status_code == 200, r.text
    assert 'id="kt-spec-recalc-btn"' in r.text, (
        "Кнопка #kt-spec-recalc-btn должна рендериться при непустой спецификации"
    )
    assert "Пересобрать конфигурации" in r.text


def test_project_page_has_per_row_recalc_icons(
    manager_client, db_session, manager_user
):
    """В таблице спецификации число иконок refresh у строк = числу
    позиций (kt-spec-recalc-row у каждой строки)."""
    n = 3
    pid = _seed_project_with_spec(db_session, manager_user, n_items=n)
    r = manager_client.get(f"/configurator/project/{pid}")
    assert r.status_code == 200, r.text
    matches = re.findall(r'class="kt-spec-recalc-row\b', r.text)
    assert len(matches) == n, (
        f"Ожидалось {n} иконок per-row recalc, найдено {len(matches)}"
    )


def test_project_page_no_recalc_button_when_empty(
    manager_client, db_session, manager_user
):
    """В пустом проекте кнопка пересчёта не должна рендериться."""
    from portal.services.configurator import spec_service
    pid = spec_service.create_empty_project(
        db_session, user_id=manager_user["id"], name="P-empty"
    )
    r = manager_client.get(f"/configurator/project/{pid}")
    assert r.status_code == 200
    assert 'id="kt-spec-recalc-btn"' not in r.text


# =====================================================================
# Блок C — редизайн фильтров /databases/components: переехал в портал
# вместе со страницей (этап UI-2 Пути B, 2026-05-11). Тесты теперь
# в tests/test_portal/test_databases_components_filters.py.
# =====================================================================


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

    r = manager_client.get("/configurator/history")
    assert r.status_code == 200
    # Селект статуса есть как был.
    assert 'id="kt-history-status"' in r.text
    # И живой поиск.
    assert 'id="kt-history-search"' in r.text
    # Без кнопок «Применить».
    assert "Применить" not in r.text


def test_projects_search_already_live(manager_client):
    """На /projects поиск живой (без кнопок «Применить»)."""
    r = manager_client.get("/configurator/projects")
    assert r.status_code == 200
    # У страницы без проектов нет input'а — но и без кнопки.
    # На странице с проектами или без — нет «Применить» рядом с
    # поиском. Здесь проверяем наличие inputа когда проекты есть
    # либо отсутствие любых apply-кнопок в тексте «Применить».
    # Самое простое — что сама форма фильтров без «Применить»
    # (есть только «Новый проект» как primary btn).
    assert "Применить" not in r.text
