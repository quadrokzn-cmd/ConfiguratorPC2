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


def test_sidebar_shows_configurator_link_for_manager_with_perm(
    portal_client, manager_user
):
    """У менеджера с permissions["configurator"]=true в подвале
    сайдбара видна ссылка «← Конфигуратор»."""
    _login(portal_client, manager_user)
    r = portal_client.get("/")
    assert r.status_code == 200
    # Подвал сайдбара содержит .kt-portal-back ссылку с текстом «Конфигуратор».
    assert 'class="kt-portal-back"' in r.text
    assert "Конфигуратор</span>" in r.text


def test_sidebar_hides_configurator_link_for_manager_without_perm(
    portal_client, manager_user_no_perms
):
    """У менеджера БЕЗ permissions["configurator"] ссылки в сайдбаре нет."""
    _login(portal_client, manager_user_no_perms)
    r = portal_client.get("/")
    assert r.status_code == 200
    # Класс ссылки нигде не должен встречаться: её просто не отрендерили.
    assert 'class="kt-portal-back"' not in r.text
    # И самого слова «Конфигуратор» в подвале сайдбара тоже не должно
    # быть. На главной плитка «Конфигуратор ПК» тоже скрыта (см. home.py),
    # поэтому простой substring-чек надёжен.
    assert "Конфигуратор" not in r.text


def test_sidebar_shows_configurator_link_for_admin_without_perms(
    portal_client, admin_user
):
    """Admin всегда видит ссылку, даже с пустыми permissions
    (has_permission(admin, ...) → True)."""
    _login(portal_client, admin_user)
    r = portal_client.get("/")
    assert r.status_code == 200
    assert 'class="kt-portal-back"' in r.text


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
