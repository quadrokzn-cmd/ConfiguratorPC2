# Шаблоны портала (этап 9Б.1).
#
# Намеренно минимально: один Jinja2Templates, один globals (portal_url
# + configurator_url) и filter static_url для cache-busting. Никакого
# курса ЦБ и фильтров to_rub/fmt_rub — порталу они не нужны (по
# крайней мере в 9Б.1).

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import settings


templates = Jinja2Templates(directory="portal/templates")


_STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"


def static_url(rel_path: str) -> str:
    """Cache-busting URL для статики. Аналог app/templating.py:static_url —
    ?v=<mtime файла>, чтобы пересборка CSS не упиралась в кеш браузера."""
    rel = rel_path.lstrip("/")
    if rel.startswith("static/"):
        rel_inside_static = rel[len("static/"):]
    else:
        rel_inside_static = rel
    full = _STATIC_ROOT / rel_inside_static
    base_url = "/static/" + rel_inside_static
    try:
        mtime = int(full.stat().st_mtime)
    except OSError:
        return base_url
    return f"{base_url}?v={mtime}"


templates.env.globals["static_url"] = static_url
templates.env.globals["portal_url"] = settings.portal_url
templates.env.globals["configurator_url"] = settings.configurator_url
