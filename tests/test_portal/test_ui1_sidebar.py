# UI-1 (Путь B, 2026-05-11): тесты общего sidebar портала.
#
# Проверяем:
#   1. На любой странице портала видны все 5 главных пунктов меню
#      (Главная / Аукционы / Конфигуратор ПК / Базы данных / Настройки)
#      именно в этом порядке.
#   2. active_section правильно определяется по URL — раскрываются
#      подпункты только активного раздела.
#   3. На главной отрисованы две плашки модулей: «Аукционы» (слева)
#      и «Конфигуратор ПК» (справа), плашка «Аукционы» ведёт на /auctions.
#   4. Старая ссылка «← Конфигуратор» в подвале sidebar удалена.

from __future__ import annotations

import re


# --- Хелперы ---------------------------------------------------------------

# Порядок 5 главных пунктов sidebar — фиксируем здесь и сверяем по позиции
# data-testid в HTML. Если кто-то переставит «Аукционы» обратно ниже
# Конфигуратора — тест упадёт и заставит обновить решение собственника.
_EXPECTED_SECTIONS = ("home", "auctions", "configurator", "databases", "settings")


def _section_positions(html: str) -> list[tuple[int, str]]:
    """Возвращает список (offset_in_html, section_key) в порядке появления."""
    pattern = re.compile(r'data-section="(home|auctions|configurator|databases|settings)"')
    return [(m.start(), m.group(1)) for m in pattern.finditer(html)]


def _active_section(html: str) -> str | None:
    """Достаёт значение data-active-section из <aside>-маркера."""
    m = re.search(r'data-active-section="([^"]+)"', html)
    return m.group(1) if m else None


def _has_subitem(html: str, sub_key: str) -> bool:
    return f'data-testid="sidebar-sub-{sub_key}"' in html


def _has_section_subitems_container(html: str, section_key: str) -> bool:
    return f'data-testid="sidebar-subitems-{section_key}"' in html


# --- 1) Порядок и присутствие пяти разделов на главной --------------------

def test_sidebar_main_sections_present_in_correct_order(admin_portal_client):
    r = admin_portal_client.get("/")
    assert r.status_code == 200
    html = r.text

    # Все 5 разделов присутствуют.
    for sec in _EXPECTED_SECTIONS:
        assert f'data-testid="sidebar-section-{sec}"' in html, \
            f"Пункт меню {sec!r} не найден в sidebar"

    # Порядок именно такой (Аукционы выше Конфигуратора — решение собственника).
    positions = _section_positions(html)
    ordered_keys = [k for _, k in positions]
    assert ordered_keys == list(_EXPECTED_SECTIONS), \
        f"Порядок пунктов sidebar не совпадает: {ordered_keys}"


def test_sidebar_main_sections_labels_present(admin_portal_client):
    """Видимые тексты пунктов меню — на русском, по решению собственника."""
    html = admin_portal_client.get("/").text
    for label in (
        "Главная", "Аукционы", "Конфигуратор ПК", "Базы данных", "Настройки",
    ):
        assert label in html, f"Лейбл «{label}» отсутствует в sidebar"


# --- 2) active_section по URL ---------------------------------------------

def test_home_active_section_is_home(admin_portal_client):
    r = admin_portal_client.get("/")
    assert r.status_code == 200
    assert _active_section(r.text) == "home"
    # У «Главной» нет подпунктов → ни один блок subitems не раскрыт.
    for sec in _EXPECTED_SECTIONS:
        assert not _has_section_subitems_container(r.text, sec) or sec == "home"


def test_auctions_active_section_is_auctions(admin_portal_client):
    r = admin_portal_client.get("/auctions")
    assert r.status_code == 200, r.status_code
    assert _active_section(r.text) == "auctions"
    # На UI-1 у Аукционов нет подпунктов — контейнер subitems не рендерится.
    assert not _has_section_subitems_container(r.text, "auctions")
    # И уж тем более не раскрыты подпункты databases/settings.
    assert not _has_section_subitems_container(r.text, "databases")
    assert not _has_section_subitems_container(r.text, "settings")


def test_nomenclature_active_section_is_databases(admin_portal_client):
    r = admin_portal_client.get("/nomenclature")
    assert r.status_code == 200, r.status_code
    html = r.text
    assert _active_section(html) == "databases"
    # Раскрыт блок подпунктов «Базы данных».
    assert _has_section_subitems_container(html, "databases")
    # И в нём — все ожидаемые подпункты.
    for sub in ("nomenclature", "prices", "autoload", "suppliers",
                "components", "mapping"):
        assert _has_subitem(html, sub), f"Подпункт «Базы данных» {sub!r} отсутствует"
    # «Справочник оргтехники» — переименованный лейбл (URL /nomenclature не трогаем).
    assert "Справочник оргтехники" in html
    # Других секций subitems быть не должно.
    assert not _has_section_subitems_container(html, "settings")
    assert not _has_section_subitems_container(html, "configurator")


def test_audit_active_section_is_settings(admin_portal_client):
    r = admin_portal_client.get("/admin/audit")
    assert r.status_code == 200, r.status_code
    html = r.text
    assert _active_section(html) == "settings"
    assert _has_section_subitems_container(html, "settings")
    for sub in ("users", "backups", "audit"):
        assert _has_subitem(html, sub), f"Подпункт «Настройки» {sub!r} отсутствует"
    # И на этой странице помечен активный подпункт — «Журнал действий».
    assert 'data-subsection="audit"' in html
    assert 'aria-current="page"' in html


def test_users_page_active_subsection_is_users(admin_portal_client):
    r = admin_portal_client.get("/admin/users")
    assert r.status_code == 200, r.status_code
    html = r.text
    assert _active_section(html) == "settings"
    assert _has_subitem(html, "users")


# --- 3) Плашки модулей на главной ------------------------------------------

def test_home_has_two_module_tiles_in_correct_order(admin_portal_client):
    """Аукционы слева, Конфигуратор ПК справа — решение собственника 2026-05-11."""
    html = admin_portal_client.get("/").text
    # Обе плитки присутствуют.
    assert 'data-testid="tile-auctions"' in html, "Плашка «Аукционы» не найдена"
    assert 'data-testid="tile-configurator"' in html, "Плашка «Конфигуратор ПК» не найдена"
    # Порядок: Аукционы раньше Конфигуратора в HTML-потоке.
    pos_auctions = html.find('data-testid="tile-auctions"')
    pos_configurator = html.find('data-testid="tile-configurator"')
    assert pos_auctions < pos_configurator, \
        "Плашка «Аукционы» должна стоять слева/выше плашки «Конфигуратор ПК»"


def test_home_auctions_tile_links_to_auctions(admin_portal_client):
    html = admin_portal_client.get("/").text
    # Ищем якорь с data-testid=tile-auctions и проверяем href.
    m = re.search(
        r'<a\s+href="([^"]+)"[^>]*data-testid="tile-auctions"', html
    )
    assert m, "Не найден <a data-testid=tile-auctions>"
    assert m.group(1) == "/auctions", f"Плашка ведёт не на /auctions: {m.group(1)}"


def test_home_auctions_tile_contains_label_and_subtitle(admin_portal_client):
    html = admin_portal_client.get("/").text
    # Заголовок и подзаголовок плашки.
    assert "Аукционы" in html
    assert "Поиск и обработка тендерных лотов" in html


# --- 4) Старая ссылка «← Конфигуратор» убрана -----------------------------

def test_sidebar_no_legacy_back_to_configurator_link(admin_portal_client):
    """В новом sidebar нет нижней ссылки kt-portal-back: переходы между
    сервисами теперь — через пункт «Конфигуратор ПК» в основном меню."""
    html = admin_portal_client.get("/").text
    assert 'class="kt-portal-back"' not in html


# --- 5) Подвал sidebar по-прежнему содержит курс и блок пользователя -----

def test_sidebar_footer_keeps_user_block(admin_portal_client, admin_user):
    html = admin_portal_client.get("/").text
    # Карточка пользователя с logout-формой.
    assert "Администратор" in html  # name из фикстуры admin_user
    # Logout — POST-форма с CSRF.
    assert re.search(
        r'<form[^>]+method="post"[^>]+action="[^"]*/logout"', html
    ), "Не найдена форма logout в подвале sidebar"
