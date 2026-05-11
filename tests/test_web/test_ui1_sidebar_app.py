# UI-1 (Путь B, 2026-05-11): тесты общего sidebar в конфигураторе (app).
#
# Здесь проверяем, что на любой странице конфигуратора рендерится тот же
# партиал shared/_partials/sidebar.html, что и в портале, и что подсветка
# раздела/подпункта корректная.

from __future__ import annotations

import re


_EXPECTED_SECTIONS = ("home", "auctions", "configurator", "databases", "settings")


def _active_section(html: str) -> str | None:
    m = re.search(r'data-active-section="([^"]+)"', html)
    return m.group(1) if m else None


def test_configurator_index_uses_shared_sidebar(admin_client):
    """На / конфигуратора видны все 5 пунктов общего sidebar."""
    r = admin_client.get("/")
    assert r.status_code == 200, r.status_code
    html = r.text
    for sec in _EXPECTED_SECTIONS:
        assert f'data-testid="sidebar-section-{sec}"' in html, \
            f"Пункт {sec!r} отсутствует в sidebar конфигуратора"
    for label in (
        "Главная", "Аукционы", "Конфигуратор ПК", "Базы данных", "Настройки",
    ):
        assert label in html


def test_configurator_index_active_section_is_configurator(admin_client):
    r = admin_client.get("/")
    assert r.status_code == 200, r.status_code
    html = r.text
    assert _active_section(html) == "configurator"
    # Раскрыт блок подпунктов «Конфигуратор ПК».
    assert 'data-testid="sidebar-subitems-configurator"' in html
    # И подсвечен подпункт «Новый запрос».
    assert 'data-subsection="new_query"' in html
    # Лейблы подпунктов.
    for lbl in ("Новый запрос", "Проекты", "История запросов"):
        assert lbl in html


def test_configurator_projects_active_subsection_is_projects(admin_client):
    r = admin_client.get("/projects")
    assert r.status_code == 200, r.status_code
    html = r.text
    assert _active_section(html) == "configurator"
    assert 'data-subsection="projects"' in html
    assert 'data-testid="sidebar-subitems-configurator"' in html


def test_configurator_history_active_subsection_is_history(admin_client):
    r = admin_client.get("/history")
    assert r.status_code == 200, r.status_code
    html = r.text
    assert _active_section(html) == "configurator"
    assert 'data-subsection="history"' in html


def test_configurator_no_legacy_admin_section_label(admin_client):
    """Старый блок «Админ» (с .nav-section-label) удалён — админские
    страницы конфигуратора переезжают в Базы данных / Настройки на UI-2/UI-3."""
    html = admin_client.get("/").text
    # Заголовок «Админ» в верхней части был только в старом sidebar.
    assert 'class="nav-section-label"' not in html


def test_configurator_no_legacy_back_to_portal_link(admin_client):
    """В новом sidebar нет нижней ссылки kt-portal-back: «Главная» теперь
    в основном меню вверху."""
    html = admin_client.get("/").text
    assert 'class="kt-portal-back"' not in html
