# Юнит-тесты shared.permissions.has_permission (этап 9Б.1).

from __future__ import annotations

from shared.permissions import (
    MODULE_KEYS,
    MODULE_LABELS,
    has_permission,
)


def test_admin_always_has_access():
    assert has_permission("admin", {}, "configurator") is True
    assert has_permission("admin", {}, "kp_form") is True
    assert has_permission("admin", {}, "anything") is True
    # Даже если в permissions явный false — admin всё равно True.
    assert has_permission("admin", {"configurator": False}, "configurator") is True


def test_manager_with_permission():
    assert has_permission("manager", {"configurator": True}, "configurator") is True


def test_manager_with_explicit_false():
    assert has_permission("manager", {"configurator": False}, "configurator") is False


def test_manager_missing_key_is_denied():
    assert has_permission("manager", {}, "configurator") is False
    assert has_permission("manager", {"kp_form": True}, "configurator") is False


def test_manager_with_none_permissions():
    """Если из БД пришёл None (теоретически — до миграции 017 поля
    могло не быть). Должны не падать, считать что прав нет."""
    assert has_permission("manager", None, "configurator") is False


def test_module_keys_contains_expected():
    """Ключи перечислены в брифе 9Б.1: configurator + четыре модуля
    на будущее. Пусть тест ловит, если кто-то случайно поменяет
    список — это смежно с миграцией данных."""
    assert "configurator" in MODULE_KEYS
    for key in ("kp_form", "auctions", "mail_agent", "dashboard"):
        assert key in MODULE_KEYS
    # И на каждый ключ есть человекочитаемая подпись.
    for key in MODULE_KEYS:
        assert key in MODULE_LABELS
        assert MODULE_LABELS[key].strip()


def test_require_permission_dependency():
    """require_permission возвращает callable Depends — проверим, что
    он 403-кидает у пользователя без прав и пропускает админа.
    Полный e2e-тест см. в портал-тестах админ-страницы."""
    from fastapi import HTTPException
    from shared.auth import AuthUser
    from shared.permissions import require_permission

    dep = require_permission("configurator")

    admin = AuthUser(id=1, login="a", role="admin", name="A", permissions={})
    assert dep(admin) is admin

    manager_with = AuthUser(
        id=2, login="m", role="manager", name="M",
        permissions={"configurator": True},
    )
    assert dep(manager_with) is manager_with

    manager_without = AuthUser(
        id=3, login="m2", role="manager", name="M2",
        permissions={},
    )
    try:
        dep(manager_without)
    except HTTPException as exc:
        assert exc.status_code == 403
    else:
        assert False, "Ожидался HTTPException 403"
