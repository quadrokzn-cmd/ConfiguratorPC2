# Регрессия 2026-05-14: после UI-4 (2026-05-11) роуты конфигуратора переехали
# под префикс /configurator/*, но static/js/project.js остался с устаревшими
# /project/{id}/select|/deselect|/update_quantity|/spec/* — все AJAX-вызовы
# чекбокса «В спецификацию», изменения количества и reoptimize падали 404,
# фронт ловил toast «Не удалось обновить спецификацию».
#
# Тест статически проверяет, что в JS нет неперфиксованных URL и что все
# /configurator/project/{id}/<endpoint> совпадают с реальными роутами FastAPI.
# Также ловит зеркальный баг — двойной /configurator/ в шаблонах.

from __future__ import annotations

import re
from pathlib import Path

from portal.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_JS = REPO_ROOT / "static" / "js" / "project.js"
PROJECT_DETAIL_HTML = (
    REPO_ROOT / "portal" / "templates" / "configurator" / "project_detail.html"
)


def _collect_routes() -> set[str]:
    paths: set[str] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path:
            paths.add(path)
    return paths


# Замена JS-переменных на FastAPI-шаблонные параметры.
_JS_VARS_TO_PARAMS = {
    "PROJECT_ID": "{project_id}",
    "itemId":     "{item_id}",
}


def _extract_post_urls(src: str) -> list[str]:
    """Собирает URL'ы из вызовов post('...') / fetch('...'), включая склейку строк.

    Поддерживает шаблоны вида post('/a/' + VAR + '/b', ...) и просто post('/x').
    Возвращает «реконструированные» URL'ы (VAR → {param}).
    """
    # Захватываем содержимое аргумента до запятой/скобки. Грубо, но достаточно
    # для нашего узкого случая.
    pattern = re.compile(
        r"(?:post|fetch)\(\s*((?:'[^']*'|\"[^\"]*\"|\s|\+|[A-Za-z_][A-Za-z0-9_]*)+?)\s*[,)]",
        re.DOTALL,
    )
    urls: list[str] = []
    for arg in pattern.findall(src):
        parts: list[str] = []
        # Делим по '+'
        for chunk in arg.split("+"):
            chunk = chunk.strip()
            if not chunk:
                continue
            if (chunk.startswith("'") and chunk.endswith("'")) or (
                chunk.startswith('"') and chunk.endswith('"')
            ):
                parts.append(chunk[1:-1])
            elif chunk in _JS_VARS_TO_PARAMS:
                parts.append(_JS_VARS_TO_PARAMS[chunk])
            else:
                # Незнакомая переменная — не пытаемся реконструировать.
                parts = []
                break
        if parts:
            joined = "".join(parts)
            if joined.startswith("/"):
                urls.append(joined)
    return urls


def test_project_js_uses_configurator_prefix():
    """JS не должен вызывать /project/... без /configurator/-префикса."""
    src = PROJECT_JS.read_text(encoding="utf-8")
    bad = [u for u in _extract_post_urls(src) if u.startswith("/project/")]
    assert not bad, (
        "В static/js/project.js остались неперфиксованные URL "
        f"(после UI-4 все роуты конфигуратора живут под /configurator/*): {bad}"
    )


def test_project_js_urls_match_fastapi_routes():
    """Каждый /configurator/...-URL из project.js должен совпадать с роутом FastAPI."""
    src = PROJECT_JS.read_text(encoding="utf-8")
    urls = [u for u in _extract_post_urls(src) if u.startswith("/configurator/")]
    routes = _collect_routes()

    unknown = [u for u in urls if u not in routes]
    assert not unknown, (
        "В project.js есть URL, которым нет соответствия в FastAPI-роутах "
        f"portal/main.py: {unknown}. Возможно, поменялся префикс или путь."
    )


def test_project_detail_no_double_configurator_prefix():
    """Шаблон не должен содержать /configurator/.../configurator/... (двойной префикс)."""
    src = PROJECT_DETAIL_HTML.read_text(encoding="utf-8")
    bad = re.findall(r"/configurator/[^\"'\s]*?/configurator/", src)
    assert not bad, (
        f"В project_detail.html обнаружен двойной /configurator/ "
        f"в URL: {bad}. Скорее всего, ошибка склейки строк в Jinja."
    )
