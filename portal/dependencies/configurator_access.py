# UI-4 (Путь B, 2026-05-11): scoped-проверка доступа к модулю
# «Конфигуратор ПК». Раньше это была глобальная middleware в
# app/main.py (_enforce_configurator_permission), которая проверяла
# permissions["configurator"] на КАЖДОМ запросе к config.quadro.tatar.
# После переезда конфигуратора в /configurator/* делать проверку
# глобальной нельзя — портал обслуживает много URL, и не каждый из
# них требует это право.
#
# Подключение: dependencies=[Depends(require_configurator_access)]
# на роутерах portal/routers/configurator/{main,projects,export}.py.
#
# Семантика — та же, что у бывшей middleware:
#   - не залогиненный → LoginRequiredRedirect (стандартный 302 на /login);
#   - без права + Accept: application/json (без text/html) → 403 JSON;
#   - без права + браузер → ConfiguratorAccessDenied (302 на главную
#     с баннером ?denied=configurator).
#
# admin всегда проходит — has_permission(admin, ..., 'configurator')
# возвращает True (см. shared/permissions.py).

from __future__ import annotations

from fastapi import Depends, HTTPException, Request, status

from shared.auth import AuthUser, require_login
from shared.permissions import has_permission


class ConfiguratorAccessDenied(Exception):
    """Внутреннее исключение: залогиненный пользователь без права
    permissions['configurator']. Перехватывается в portal/main.py
    через exception_handler и превращается в 302 на главную портала
    с query-параметром ?denied=configurator (на главной показывается
    баннер «Нет доступа к модулю»)."""


def require_configurator_access(
    request: Request,
    user: AuthUser = Depends(require_login),
) -> AuthUser:
    """Возвращает залогиненного пользователя с правом на конфигуратор.

    Поднимает:
      - LoginRequiredRedirect (через require_login) — если не залогинен;
      - HTTPException 403 — если JSON-запрос без права;
      - ConfiguratorAccessDenied — если HTML-запрос без права.
    """
    if has_permission(user.role, user.permissions or {}, "configurator"):
        return user

    accept = request.headers.get("accept", "")
    if "application/json" in accept and "text/html" not in accept:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Нет доступа к модулю «Конфигуратор ПК».",
        )
    raise ConfiguratorAccessDenied()
