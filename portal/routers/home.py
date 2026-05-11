# Главная страница портала (этап 9Б.2).
#
# 9Б.1: текстовый плейсхолдер с плитками модулей.
# 9Б.2: дашборд из 5 виджетов (общие метрики компании) + большая
# плитка-модуль «Конфигуратор ПК». Виджеты доступны всем авторизованным
# (admin + manager) — это «всё видят все внутри компании». Плитка
# модуля видна только тем, у кого есть permission "configurator"
# (или admin).

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from shared.config import settings
from portal.services.dashboard import get_dashboard_data
from portal.templating import templates
from shared.auth import AuthUser, get_csrf_token, require_login
from shared.db import get_db
from shared.permissions import MODULE_LABELS, has_permission


router = APIRouter()


@router.get("/")
def home(
    request: Request,
    denied: str = "",
    user: AuthUser = Depends(require_login),
    db: Session = Depends(get_db),
):
    """Главная портала — дашборд + плитка Конфигуратора (если разрешён).

    Сервис dashboard.get_dashboard_data() возвращает безопасный dict
    даже на пустой БД — здесь дополнительной обработки не требуется.

    9Б.4: ?denied=<module_key> — приходит редиректом из middleware
    конфигуратора (или другого модуля), когда у менеджера нет permission
    на запрашиваемый модуль. Превращаем в человекочитаемый текст
    «У вас нет доступа к модулю «X»» и показываем баннером сверху
    дашборда.
    """
    dashboard = get_dashboard_data(db)

    show_configurator = has_permission(
        user.role, user.permissions or {}, "configurator"
    )
    show_auctions = has_permission(
        user.role, user.permissions or {}, "auctions"
    )

    # Имя для приветствия: первое слово из user.name либо login.
    full_name = (user.name or "").strip()
    first_name = full_name.split()[0] if full_name else user.login

    denied_label = MODULE_LABELS.get(denied) if denied else None

    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "user":              user,
            "csrf_token":        get_csrf_token(request),
            "first_name":        first_name,
            "dashboard":         dashboard,
            "show_configurator": show_configurator,
            "show_auctions":     show_auctions,
            "configurator_url":  settings.configurator_url,
            "denied_label":      denied_label,
        },
    )
