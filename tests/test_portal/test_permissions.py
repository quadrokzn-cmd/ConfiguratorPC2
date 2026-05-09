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
    список — это смежно с миграцией данных.

    Этап 7 (2026-05-08) добавил две тонких права аукционов
    (auctions_edit_status, auctions_edit_settings) — фиксируем."""
    assert "configurator" in MODULE_KEYS
    for key in (
        "kp_form",
        "auctions",
        "auctions_edit_status",
        "auctions_edit_settings",
        "mail_agent",
        "dashboard",
    ):
        assert key in MODULE_KEYS
    # И на каждый ключ есть человекочитаемая подпись.
    for key in MODULE_KEYS:
        assert key in MODULE_LABELS
        assert MODULE_LABELS[key].strip()


# ── Permission-ключи для модуля «Аукционы» (этап 7 слияния) ──────────────


def test_admin_has_all_auctions_perms():
    """Admin с дефолтным JSONB после миграции 033 имеет все три
    auctions-права. Но это сюда не доходит — admin обходит проверку
    JSONB по роли (has_permission всегда True). Тест фиксирует именно
    это: даже с пустыми permissions admin получает любой ключ."""
    perms = {
        "auctions": True,
        "auctions_edit_status": True,
        "auctions_edit_settings": True,
    }
    assert has_permission("admin", perms, "auctions") is True
    assert has_permission("admin", perms, "auctions_edit_status") is True
    assert has_permission("admin", perms, "auctions_edit_settings") is True
    # И даже если permissions пустые — admin всё равно True.
    assert has_permission("admin", {}, "auctions") is True
    assert has_permission("admin", {}, "auctions_edit_status") is True
    assert has_permission("admin", {}, "auctions_edit_settings") is True


def test_manager_default_no_edit_settings():
    """Manager с дефолтным JSONB по миграции 033: видит модуль и
    меняет статусы, но НЕ может править настройки."""
    perms = {
        "auctions": True,
        "auctions_edit_status": True,
        "auctions_edit_settings": False,
    }
    assert has_permission("manager", perms, "auctions") is True
    assert has_permission("manager", perms, "auctions_edit_status") is True
    assert has_permission("manager", perms, "auctions_edit_settings") is False


def test_manager_without_auctions_keys_has_no_access():
    """Если у manager в permissions нет ключа auctions* — has_permission
    возвращает False (а не падает с KeyError). Это важно: пользователь,
    созданный до миграции 033 и не прогнанный через неё, не должен
    получать доступ по умолчанию."""
    assert has_permission("manager", {"configurator": True}, "auctions") is False
    assert has_permission(
        "manager", {"configurator": True}, "auctions_edit_status"
    ) is False
    assert has_permission(
        "manager", {"configurator": True}, "auctions_edit_settings"
    ) is False


def test_admin_role_overrides_missing_auctions_perm():
    """Admin без явных auctions* прав в JSONB всё равно имеет доступ.
    Текущая модель C-PC2 даёт админу всё через role='admin', не сверяясь
    с permissions — тест фиксирует это поведение, чтобы случайное
    изменение has_permission не сломало админский доступ к аукционам."""
    assert has_permission("admin", None, "auctions") is True
    assert has_permission("admin", {}, "auctions_edit_status") is True
    # Даже явный False в JSONB админу не помеха.
    assert has_permission(
        "admin", {"auctions_edit_settings": False}, "auctions_edit_settings"
    ) is True


def test_auctions_keys_are_independent():
    """auctions_edit_status и auctions_edit_settings — независимые ключи:
    можно иметь право менять статусы, не имея прав на настройки, и наоборот.
    Базовый view (auctions) тоже отдельный — но в роутах модуля имеет
    смысл проверять (auctions AND <fine-grained>) явно."""
    only_view = {"auctions": True}
    assert has_permission("manager", only_view, "auctions") is True
    assert has_permission("manager", only_view, "auctions_edit_status") is False
    assert has_permission("manager", only_view, "auctions_edit_settings") is False

    only_status = {"auctions_edit_status": True}
    assert has_permission("manager", only_status, "auctions") is False
    assert has_permission("manager", only_status, "auctions_edit_status") is True
    assert has_permission("manager", only_status, "auctions_edit_settings") is False

    only_settings = {"auctions_edit_settings": True}
    assert has_permission("manager", only_settings, "auctions") is False
    assert has_permission("manager", only_settings, "auctions_edit_status") is False
    assert has_permission("manager", only_settings, "auctions_edit_settings") is True


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
