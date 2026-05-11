# UI-тесты permission enforcement в портале (этап 9Б.4).
#
# Бриф 9Б.4: ссылка «← Конфигуратор» в подвале сайдбара портала
# отрисовывается только пользователям с permissions["configurator"]
# (admin — всегда). На главной портала при ?denied=configurator
# показываем баннер «У вас нет доступа к модулю «...»» — туда
# приходит редирект из middleware конфигуратора.

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_portal.conftest import extract_csrf, _login_via_portal


def _login(client: TestClient, user: dict) -> None:
    _login_via_portal(client, user["login"], user["password"])


def test_sidebar_shows_configurator_section_for_manager_with_perm(
    portal_client, manager_user
):
    """UI-1 (Путь B): нижняя ссылка «← Конфигуратор» убрана; пункт
    «Конфигуратор ПК» переехал в основное меню sidebar."""
    _login(portal_client, manager_user)
    r = portal_client.get("/")
    assert r.status_code == 200
    assert 'data-testid="sidebar-section-configurator"' in r.text
    assert "Конфигуратор ПК" in r.text


def test_sidebar_shows_configurator_section_for_manager_without_perm(
    portal_client, manager_user_no_perms
):
    """UI-1 (Путь B, 2026-05-11): RBAC-фильтрация меню отложена на этап
    после UI-5 — меню одинаково для admin/manager и для менеджеров с/без
    permission. Пункт «Конфигуратор ПК» виден даже без права; при клике
    middleware конфигуратора редиректит на /?denied=configurator
    (см. test_denied_banner_renders_when_query_param_present)."""
    _login(portal_client, manager_user_no_perms)
    r = portal_client.get("/")
    assert r.status_code == 200
    # Старая ссылка удалена.
    assert 'class="kt-portal-back"' not in r.text
    # Пункт меню видим всем (одинаковое меню).
    assert 'data-testid="sidebar-section-configurator"' in r.text
    # Плашка модуля «Конфигуратор ПК» на главной по-прежнему скрыта
    # для менеджера без права — это всё ещё работает (см. home.py).
    assert 'data-testid="tile-configurator"' not in r.text


def test_sidebar_shows_configurator_section_for_admin(
    portal_client, admin_user
):
    """Admin всегда видит пункт «Конфигуратор ПК» в основном меню."""
    _login(portal_client, admin_user)
    r = portal_client.get("/")
    assert r.status_code == 200
    assert 'data-testid="sidebar-section-configurator"' in r.text


def test_denied_banner_renders_when_query_param_present(
    portal_client, manager_user_no_perms
):
    """?denied=configurator → на главной появляется flash-баннер."""
    _login(portal_client, manager_user_no_perms)
    r = portal_client.get("/?denied=configurator")
    assert r.status_code == 200
    assert 'data-testid="denied-banner"' in r.text
    assert "У вас нет доступа к модулю" in r.text
    # MODULE_LABELS["configurator"] = "Конфигуратор ПК"
    assert "Конфигуратор ПК" in r.text


def test_denied_banner_absent_without_query_param(
    portal_client, manager_user_no_perms
):
    """Без ?denied=… баннер не отрисовывается — лишний шум на главной
    при обычной навигации не нужен."""
    _login(portal_client, manager_user_no_perms)
    r = portal_client.get("/")
    assert r.status_code == 200
    assert 'data-testid="denied-banner"' not in r.text


def test_denied_banner_ignores_unknown_module_key(
    portal_client, manager_user_no_perms
):
    """Если в ?denied= пришёл неизвестный ключ (например, кто-то подделал
    URL), баннер не показываем — нет MODULE_LABELS-записи, label=None."""
    _login(portal_client, manager_user_no_perms)
    r = portal_client.get("/?denied=hacker")
    assert r.status_code == 200
    assert 'data-testid="denied-banner"' not in r.text


def test_login_page_renders_redesigned_layout(portal_client):
    """Редизайн логина: каркас с .kt-login-page/.kt-login-card,
    каплейн «ПОРТАЛ» и таглайн «Внутренний сервис КВАДРО-ТЕХ»."""
    r = portal_client.get("/login")
    assert r.status_code == 200
    # Брендовый каркас.
    assert "kt-login-page" in r.text
    assert "kt-login-card" in r.text
    assert "kt-login-caption" in r.text
    # Логотип UADRO — тот же SVG, что в сайдбаре.
    assert "/static/img/brand/quadro-logo.svg" in r.text
    # Подпись «ПОРТАЛ» собирается из <span>-букв (как в сайдбаре).
    assert ">П</span>" in r.text
    assert ">Л</span>" in r.text
    # Таглайн.
    assert "Внутренний сервис КВАДРО-ТЕХ" in r.text
    # CSRF-поле и поля логина/пароля по-прежнему на месте.
    assert 'name="csrf_token"' in r.text
    assert 'name="login"' in r.text
    assert 'name="password"' in r.text
