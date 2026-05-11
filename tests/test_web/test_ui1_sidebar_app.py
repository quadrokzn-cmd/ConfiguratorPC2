# UI-1 (Путь B): тесты общего sidebar в app/ (config.quadro.tatar).
#
# UI-4 (Путь B, 2026-05-11): после переноса конфигуратора в portal в app/
# остались только admin-страницы (/admin, /admin/budget, /admin/queries).
# Проверяем, что на этих страницах рендерится общий sidebar, active_section
# подсвечен 'configurator', а подпункты конфигуратора стали cross-service
# (URL на portal_url/configurator/* с маркером ↗).

from __future__ import annotations

import re


_EXPECTED_SECTIONS = ("home", "auctions", "configurator", "databases", "settings")


def _active_section(html: str) -> str | None:
    m = re.search(r'data-active-section="([^"]+)"', html)
    return m.group(1) if m else None


def test_admin_dashboard_uses_shared_sidebar(admin_client_app):
    """На /admin dashboard видны все 5 пунктов общего sidebar."""
    r = admin_client_app.get("/admin")
    assert r.status_code == 200, r.status_code
    html = r.text
    for sec in _EXPECTED_SECTIONS:
        assert f'data-testid="sidebar-section-{sec}"' in html, \
            f"Пункт {sec!r} отсутствует в sidebar admin-страницы"
    for label in (
        "Главная", "Аукционы", "Конфигуратор ПК", "Базы данных", "Настройки",
    ):
        assert label in html


def test_admin_dashboard_active_section_is_configurator(admin_client_app):
    r = admin_client_app.get("/admin")
    assert r.status_code == 200, r.status_code
    html = r.text
    assert _active_section(html) == "configurator"
    # Раскрыт блок подпунктов «Конфигуратор ПК».
    assert 'data-testid="sidebar-subitems-configurator"' in html
    # Подпункты "Новый запрос", "Проекты", "История запросов".
    for lbl in ("Новый запрос", "Проекты", "История запросов"):
        assert lbl in html


def test_configurator_subitems_are_cross_service_after_ui4(admin_client_app):
    """UI-4 (Путь B): подпункты «Конфигуратор ПК» теперь cross-service —
    ведут на portal_url/configurator/*. На стороне app/ они имеют
    маркер ↗ (как любая cross-service ссылка в sidebar)."""
    html = admin_client_app.get("/admin").text
    # Абсолютные ссылки на portal с префиксом /configurator/.
    assert "/configurator/projects" in html
    assert "/configurator/history" in html
    # Старые app-URL'ы (без префикса /configurator/) в подпунктах
    # больше не используются.
    assert 'href="/projects"' not in html
    assert 'href="/history"' not in html
    # Подпункт «Новый запрос» помечен sub_key new_query.
    assert 'data-subsection="new_query"' in html


def test_admin_dashboard_no_legacy_admin_section_label(admin_client_app):
    """Старый блок «Админ» (с .nav-section-label) удалён — админские
    подпункты не показываются в sidebar отдельным разделом."""
    html = admin_client_app.get("/admin").text
    assert 'class="nav-section-label"' not in html


def test_admin_dashboard_no_legacy_back_to_portal_link(admin_client_app):
    """В новом sidebar нет нижней ссылки kt-portal-back: «Главная» —
    в основном меню вверху."""
    html = admin_client_app.get("/admin").text
    assert 'class="kt-portal-back"' not in html


def test_admin_dashboard_no_old_admin_section_links_in_html(admin_client_app):
    """UI-2: «Поставщики», «Комплектующие для ПК», «Очередь маппинга»
    переехали в portal/databases. На странице /admin (active_section=
    'configurator') подпункты «Базы данных» свёрнуты, никаких ссылок
    на старые /admin/{suppliers,components,mapping} в HTML быть не должно."""
    html = admin_client_app.get("/admin").text
    assert "/admin/suppliers" not in html
    assert "/admin/components" not in html
    assert "/admin/mapping" not in html
