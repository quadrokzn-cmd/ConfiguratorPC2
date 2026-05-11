# Шаблоны портала.
#
# Этап 9Б.1: минимальная регистрация (только globals portal_url/configurator_url
# и static_url).
# Этап 9Б.2: фильтры для дашборда (ru_date, ru_datetime_short, days_ago).
# Этап 9Б.2.1: курс ЦБ — общий партиал shared/templates/_partials/fx_widget.html
# импортирует Jinja-global current_exchange_rate(), регистрируем здесь же.
# Реализацию переиспользуем из app/templating.py — функция уже умеет
# открывать SessionLocal и парсить fetched_at в МСК.

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from app.config import settings


# 9Б.2.1: добавляем shared/templates как fallback. Партиалы
# (`_partials/fx_widget.html`) одни и те же для обоих сервисов;
# свои шаблоны портала находятся первыми.
templates = Jinja2Templates(directory=["portal/templates", "shared/templates"])


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

# 9Б.4: has_permission(user.role, user.permissions, module_key) — для условного
# рендера ссылок на модули в сайдбаре (например, «← Конфигуратор» прячется,
# если у пользователя нет permissions["configurator"]).
from shared.permissions import has_permission as _has_permission

templates.env.globals["has_permission"] = _has_permission

# 9Б.2: фильтры для дашборда — русское форматирование дат и «N дней назад».
from portal.services import dashboard as _dashboard

templates.env.filters["ru_date"] = _dashboard.format_ru_date
templates.env.filters["ru_datetime_short"] = _dashboard.format_ru_datetime_short
templates.env.filters["days_ago"] = _dashboard.format_days_ago

# 9Б.2.1: курс ЦБ — переиспользуем готовую функцию app/templating.py.
# Она открывает свою сессию SessionLocal и форматирует fetched_at в МСК.
# Импорт лежит здесь, а не на уровне модуля, чтобы не падать в тестах,
# где app.templating может тянуть тяжёлые зависимости (scheduler и пр.).
from app.templating import current_exchange_rate as _current_exchange_rate
# UI-4 (Путь B, 2026-05-11): фильтры to_rub/fmt_rub теперь нужны и
# в portal/ — шаблоны конфигуратора (project_detail.html, result.html
# и т.д.) переехали в portal/templates/configurator/ и обращаются к
# этим фильтрам. Переиспользуем реализацию из app/templating.py
# (app/templating.py остаётся, пока существует /admin (dashboard) в
# конфигураторе — окончательное место для to_rub/fmt_rub станет
# portal/templating.py после UI-5).
from app.templating import to_rub as _to_rub, fmt_rub as _fmt_rub

templates.env.globals["current_exchange_rate"] = _current_exchange_rate
templates.env.filters["to_rub"] = _to_rub
templates.env.filters["fmt_rub"] = _fmt_rub


# 9a-fixes: форматирование сумм по-русски — «5 348 890,31».
# Неразрывный пробел между разрядами + запятая как десятичный разделитель.
def _ru_money(value, *, decimals: int = 2) -> str:
    if value is None or value == "":
        return "—"
    try:
        from decimal import Decimal as _D
        d = _D(str(value))
    except Exception:
        return str(value)
    # форматирование с фиксированным числом знаков после точки
    sign = "-" if d < 0 else ""
    abs_str = f"{abs(d):.{decimals}f}"
    if "." in abs_str:
        int_part, frac_part = abs_str.split(".", 1)
    else:
        int_part, frac_part = abs_str, ""
    # группируем тройки разрядов справа, разделитель —   (NBSP)
    rev = int_part[::-1]
    chunks = [rev[i:i + 3] for i in range(0, len(rev), 3)]
    int_grouped = " ".join(chunks)[::-1]
    if frac_part:
        return f"{sign}{int_grouped},{frac_part}"
    return f"{sign}{int_grouped}"


templates.env.filters["ru_money"] = _ru_money


# 9a-fixes: «бренд + модель + русское описание» для блока primary/alternative.
# Пример входа: brand='G&G', sku='g&g:P2022W-NC1',
#                name='Принтер G&G P2022W, Printer, Mono laser, A4, 22 ppm'.
# Выход (dict с тремя кусками для шаблона):
#   {'brand': 'G&G',
#    'model': 'P2022W-NC1',
#    'desc':  'Принтер лазерный, A4, 22 ppm'}
# В шаблоне рендерим: «{{brand}} {{model}} — {{desc}}».
import re as _re


_ENG_NOISE_PATTERNS = (
    _re.compile(r"\b(printer|mono\s*laser|color\s*laser|laser|inkjet|mfp|all\-?in\-?one)\b", _re.I),
    _re.compile(r"\b(\d+\s*ppm)\b", _re.I),  # «22 ppm» — оставим только русские «стр/мин»
)


def _strip_brand_prefix(text_value: str, brand: str) -> str:
    """Удаляет brand из начала строки name (с двоеточием/тире/пробелом)."""
    if not text_value or not brand:
        return text_value or ""
    pattern = _re.compile(
        r"^\s*" + _re.escape(brand) + r"\s*[:\-—\s]*",
        _re.IGNORECASE,
    )
    return pattern.sub("", text_value).strip()


def _clean_name_desc(name: str, brand: str) -> str:
    """Чистит русское описание: убирает бренд в начале и явные англ. дубли."""
    if not name:
        return ""
    cleaned = _strip_brand_prefix(name, brand)
    # Удаляем английские шумные термины (Printer, Mono laser, 22 ppm)
    # — оставляем русское описание + цифры/A4/A3.
    for pat in _ENG_NOISE_PATTERNS:
        cleaned = pat.sub("", cleaned)
    # Чистим хвостовые ", ,  ," и удвоенные пробелы.
    cleaned = _re.sub(r"\s*,\s*,+", ",", cleaned)
    cleaned = _re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(" ,—-:·")
    return cleaned


def _extract_model_from_sku(sku: str, brand: str, mpn: str | None = None) -> str:
    """Достаёт модель: предпочтительно mpn; иначе часть SKU после префикса
    бренда вида 'g&g:P2022W-NC1' → 'P2022W-NC1'."""
    if mpn:
        return str(mpn).strip()
    if not sku:
        return ""
    s = str(sku)
    # Если SKU содержит ':', берём часть после двоеточия.
    if ":" in s:
        return s.split(":", 1)[1].strip()
    # Иначе пытаемся снять префикс-бренд.
    if brand:
        return _strip_brand_prefix(s, brand) or s
    return s


def _clean_sku_display(brand: str, sku: str, name: str, mpn: str | None = None) -> dict:
    """Возвращает {brand, model, desc} для рендера в шаблоне.

    Использование (Jinja-фильтр или функция):
        {% set d = clean_sku_display(p.brand, p.sku, p.name, p.mpn) %}
        {{ d.brand }} {{ d.model }} — {{ d.desc }}
    """
    b = (brand or "").strip()
    model = _extract_model_from_sku(sku or "", b, mpn)
    desc = _clean_name_desc(name or "", b)
    return {
        "brand": b or "—",
        "model": model or "—",
        "desc":  desc or "",
    }


templates.env.globals["clean_sku_display"] = _clean_sku_display


# 9a-fixes-2 (#2): inline-описание атрибутов SKU для /nomenclature.
# Превращает attrs_jsonb в строку «8 стр/мин · ч/б · A4 · 200 dpi · USB»,
# пропускает n/a/None, для известных ключей добавляет единицы измерения.
# Используется в шаблоне `nomenclature/index.html` под именем SKU.
def _format_attrs_inline(attrs: dict | None, na_value: str = "n/a") -> str:
    if not attrs:
        return ""
    parts: list[str] = []
    for key, value in attrs.items():
        if value is None or value == na_value:
            continue
        if isinstance(value, list):
            cleaned = [str(v) for v in value if v and v != na_value]
            if not cleaned:
                continue
            parts.append("+".join(cleaned))
            continue
        sval = str(value).strip()
        if not sval or sval == na_value:
            continue
        if key == "print_speed_ppm":
            parts.append(f"{sval} стр/мин")
        elif key == "resolution_dpi":
            parts.append(f"{sval} dpi")
        elif key == "starter_cartridge_pages":
            parts.append(f"стартовый {sval} стр.")
        elif key == "usb":
            if sval.lower() in ("yes", "true", "да"):
                parts.append("USB")
        elif key == "duplex":
            if sval.lower() in ("yes", "true", "да"):
                parts.append("дуплекс")
        else:
            parts.append(sval)
    return " · ".join(parts)


templates.env.globals["format_attrs_inline"] = _format_attrs_inline


# 9a-fixes-2 (#6): русские лейблы для ключей customer_contacts_jsonb.
# email — оставляем как есть (привычно на латинице). Для неизвестного ключа
# возвращаем сам ключ — мягкий fallback.
_CONTACT_LABELS = {
    "fio":      "ФИО",
    "phone":    "Телефон",
    "position": "Должность",
    "email":    "email",
}


def _contact_label(key: str) -> str:
    if not key:
        return ""
    return _CONTACT_LABELS.get(str(key).strip().lower(), str(key))


templates.env.globals["contact_label"] = _contact_label


# 9a-fixes-3 (#1): русские лейблы для category в /nomenclature.
# printer → Принтер, mfu → МФУ. Неизвестное значение возвращаем как есть
# (не теряем категории, появившиеся позже).
_CATEGORY_LABELS = {
    "printer": "Принтер",
    "mfu":     "МФУ",
}


def _category_label(category: str | None) -> str:
    if not category:
        return "—"
    return _CATEGORY_LABELS.get(str(category).strip().lower(), str(category))


templates.env.globals["category_label"] = _category_label
templates.env.filters["category_label"] = _category_label
