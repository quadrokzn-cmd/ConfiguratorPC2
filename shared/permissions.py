# Permissions: гибкие права доступа к модулям портала (этап 9Б.1).
#
# Модель: у каждого пользователя есть JSONB users.permissions со
# словарём вида {"configurator": true, "kp_form": false, ...}. admin
# видит все модули по определению — его permissions не проверяются
# и могут оставаться пустыми. Manager видит только те модули, по
# ключу которых стоит true.
#
# В этапе 9Б.1 активен только ключ "configurator" — это плитка
# главной портала, ведущая на config.quadro.tatar/. Остальные ключи
# зарезервированы под следующие подэтапы (9Б.2 — дашборд/виджеты,
# 9Б.3 — деплой). Они уже в MODULE_KEYS, чтобы будущие миграции
# на UI не требовали менять список.
#
# Список permission-ключей (этап 7 слияния, 2026-05-08):
#   configurator           — доступ к модулю «Конфигуратор ПК»
#   kp_form                — доступ к модулю «Формы КП»
#   auctions               — базовый доступ к модулю «Аукционы»
#                            (видеть страницу /auctions, читать список лотов)
#   auctions_edit_status   — менять статус лота
#                            (in_review / will_bid / submitted / won / skipped)
#   auctions_edit_settings — править настройки модуля аукционов
#                            (margin_threshold, ktru_watchlist, excluded_regions)
#   mail_agent             — доступ к модулю «Почтовый агент»
#   dashboard              — доступ к дашборду
#
# Структура — плоский JSONB словарь {key: bool}, без вложенности. Тонкие
# права аукционов решено реализовать как отдельные ключи верхнего
# уровня, а не как `auctions: {view, edit_status, edit_settings}` —
# чтобы не плодить два паттерна и переиспользовать существующий
# механизм has_permission/update_permissions/UI-чекбоксов.
#
# Ключи аукционов независимы: edit_status / edit_settings не подразумевают
# наличие auctions. Проверки в роутах должны явно проверять оба ключа,
# когда это нужно (например, "право на /auctions/{id}/status" =
# auctions AND auctions_edit_status).

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, status

from shared.auth import AuthUser, require_login


# Ключи модулей. Порядок важен — это порядок плиток на главной портала
# в 9Б.2. В 9Б.1 показываем только "configurator", остальные пока
# скрыты в UI.
MODULE_KEYS: list[str] = [
    "configurator",
    "kp_form",
    "auctions",
    "auctions_edit_status",
    "auctions_edit_settings",
    "mail_agent",
    "dashboard",
]

# Человекочитаемые подписи плиток. Используются в шаблонах портала.
MODULE_LABELS: dict[str, str] = {
    "configurator":           "Конфигуратор ПК",
    "kp_form":                "Формы КП",
    "auctions":               "Аукционы",
    "auctions_edit_status":   "Аукционы — менять статус лота",
    "auctions_edit_settings": "Аукционы — править настройки",
    "mail_agent":             "Почтовый агент",
    "dashboard":              "Дашборд",
}


def has_permission(
    user_role: str,
    user_permissions: dict[str, Any] | None,
    module_key: str,
) -> bool:
    """True, если пользователь имеет доступ к модулю.

    admin → всегда True (даже если permissions пустые);
    manager → bool(user_permissions[module_key]).
    """
    if user_role == "admin":
        return True
    if not user_permissions:
        return False
    return bool(user_permissions.get(module_key))


def require_permission(module_key: str):
    """FastAPI Depends-фабрика: пропускает только пользователей с
    правом на module_key. Иначе 403."""

    def _check(user: AuthUser = Depends(require_login)) -> AuthUser:
        if not has_permission(user.role, user.permissions or {}, module_key):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Нет доступа к модулю «{MODULE_LABELS.get(module_key, module_key)}».",
            )
        return user

    return _check
