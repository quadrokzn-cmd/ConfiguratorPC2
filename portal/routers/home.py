# Главная страница портала (этап 9Б.1).
#
# Минимум: «Портал КВАДРО-ТЕХ» + список плиток модулей. В 9Б.1
# реально активна одна плитка — «Конфигуратор ПК». Остальные ключи
# из shared.permissions.MODULE_KEYS уже зарезервированы, но в UI
# не показываются — они появятся в 9Б.2 вместе с дашбордом.

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.config import settings
from portal.templating import templates
from shared.auth import AuthUser, get_csrf_token, require_login
from shared.permissions import MODULE_LABELS, has_permission


router = APIRouter()


# В 9Б.1 показываем только эту плитку. Список расширяется в 9Б.2.
_VISIBLE_MODULES_9B1: list[tuple[str, str, str]] = [
    # (key, label, target_url-template)
    ("configurator", MODULE_LABELS["configurator"], "{configurator_url}/"),
]


@router.get("/")
def home(
    request: Request,
    user: AuthUser = Depends(require_login),
):
    """Главная портала. Показывает плитки доступных пользователю модулей.
    Если ни одной — выводит «модулей нет, обратитесь к администратору».
    """
    tiles: list[dict] = []
    for key, label, url_tpl in _VISIBLE_MODULES_9B1:
        if has_permission(user.role, user.permissions or {}, key):
            tiles.append({
                "key":   key,
                "label": label,
                "url":   url_tpl.format(configurator_url=settings.configurator_url),
            })

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user":       user,
            "csrf_token": get_csrf_token(request),
            "tiles":      tiles,
        },
    )
