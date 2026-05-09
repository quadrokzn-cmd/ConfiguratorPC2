"""Нормализация сырой таблицы характеристик zakupki в ключи нашей schema.

Контекст: `card_parser._extract_position_attrs` достаёт из expander-блока
позиции (`<tr class="truInfo_NNN">`-сёстры) пары «Наименование характеристики
→ Значение характеристики» как у zakupki — длинные русские строки с
неструктурированными значениями («≥ 30», «Черно-Белая», «А4», «USB»).

Задача нормализатора — привести их к ключам `PRINTER_MFU_ATTRS`
(`app/services/auctions/catalog/enrichment/schema.py`), чтобы матчер
`attribute_rules.check_attribute` работал на структурных данных, а не
полагался на текстовый разбор `name`.

Ключевые особенности:

- Один zakupki-ключ может расщепляться на несколько schema-ключей. Пример:
  «Способ подключения» с rowspan-значениями ["USB", "LAN"] → `usb="yes"` +
  `network_interface="LAN"`.
- Несколько zakupki-ключей могут схлопываться в один schema-ключ. Пример:
  «Максимальное разрешение по вертикали, dpi» и «… по горизонтали, dpi»
  → один `resolution_dpi` (берём минимум — это нижняя граница, как ge).
- Атрибуты вне schema (объём ОЗУ, класс энергоэффективности, ёмкость лотков)
  **игнорируются** молча; вызывающий код может получить список незнакомых
  ключей через возврат для разовой INFO-логи на прогон.
- Значения сравниваются по правилам из `attribute_rules.py`. Для critical-
  атрибутов с `>=`/`<=` ловим **нижнюю границу** требования лота: «≥ 30»
  → 30, «не менее 30 стр/мин» → 30. Если у лота граница «≤ 8» (время
  отпечатка — у нас не critical), нормализатор всё равно берёт число — но
  такие ключи в schema всё равно не попадают.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.services.auctions.catalog.enrichment.schema import PRINTER_MFU_ATTRS


# Все схемные ключи (на момент написания: 9). Любой ключ вне этого набора
# в нормализованный dict не попадает.
_SCHEMA_KEYS: frozenset[str] = frozenset(PRINTER_MFU_ATTRS.keys())


def _norm(text: str) -> str:
    """NFC + lower. Для ключей и значений достаточно: regex'ы ниже работают
    в lower-case с кириллицей."""
    if not text:
        return ""
    return unicodedata.normalize("NFC", text).lower().strip()


# Извлечение нижней границы из значения: «≥ 30», «не менее 30», «от 30»,
# «30», «30-60». Используется для всех числовых ge-атрибутов.
_LOWER_BOUND_RE = re.compile(
    r"(?:не\s+менее|≥|>=|от|свыше|больше)\s*(\d+)"
    r"|(\d+)(?:\s*[-–—]\s*\d+)?",
    re.IGNORECASE,
)


def _parse_lower_bound_int(value: str) -> int | None:
    """«≥ 30» → 30, «не менее 30 стр/мин» → 30, «30» → 30, «30-60» → 30,
    «А4» → None (буква перед цифрой не считается). None если ничего нет."""
    if not value:
        return None
    text = value.strip()
    # Перебираем все matches и берём первый, у которого цифра НЕ идёт сразу
    # после «А»/«A» (защита от «А4» → 4).
    for m in _LOWER_BOUND_RE.finditer(text):
        digit_str = m.group(1) or m.group(2)
        if not digit_str:
            continue
        start = m.start(1) if m.group(1) else m.start(2)
        if start > 0 and text[start - 1] in "АAaа":
            continue
        try:
            return int(digit_str)
        except ValueError:
            continue
    return None


def _values_as_list(raw_value: Any) -> list[str]:
    """`_extract_position_attrs` может вернуть либо строку (один rowspan=1),
    либо список (rowspan>1, например USB+LAN). Приводим к списку строк."""
    if raw_value is None:
        return []
    if isinstance(raw_value, list):
        return [str(v) for v in raw_value if v]
    return [str(raw_value)] if str(raw_value).strip() else []


# ---------- Распознавание ключей zakupki ----------

# Скорость печати (стр/мин). Подходят оба варианта:
# «Скорость черно-белой печати в формате А4 …, стр/мин»
# «Скорость цветной печати в формате А3, стр/мин ≥ 20»
_RE_PRINT_SPEED = re.compile(r"скорост[ьи]\b.*\bпечат")

# Цветность («Цветность», «Цветность печати»)
_RE_COLORNESS = re.compile(r"^цветност[ьи]\b")

# Максимальный формат печати/сканирования
# («Максимальный формат печати», «Максимальный формат сканирования»)
_RE_MAX_FORMAT = re.compile(r"максимальн\w+\s+формат\s+(?:печати|сканировани\w+|копировани\w+)")

# Двусторонняя печать («Наличие автоматической двусторонней печати»,
# «Двусторонняя печать»)
_RE_DUPLEX = re.compile(r"двусторонн\w+\s+печат")

# Способ подключения (USB / LAN / Wi-Fi)
_RE_CONNECTION = re.compile(r"способ\s+подключени[яе]")

# Технология печати
_RE_PRINT_TECH = re.compile(r"технологи\w+\s+печати")

# Разрешение печати в dpi («Максимальное разрешение … печати … dpi»)
_RE_RESOLUTION = re.compile(r"разрешени\w+\s+.*\bпечат\w*.*\bdpi\b")

# Ресурс стартового картриджа в страницах. Внимание: «Количество стартовых
# картриджей» и «ресурс картриджа» — разные вещи; ловим только ресурс.
_RE_STARTER_RES = re.compile(r"ресурс\s+(?:оригинальн\w+\s+)?(?:стартов\w+\s+)?(?:.*\s+)?картридж\w*")
_RE_STARTER_PAGES_HINT = re.compile(r"стартов\w+\s+картридж\w*.*страниц")

# Ключи, которые мы знаем, но не складываем в schema (просто не warn'аем).
# Для всего остального вызывающий получит «незнакомый ключ».
_RE_KNOWN_NON_SCHEMA = (
    re.compile(r"наличи\w+\s+в\s+комплект\w+"),
    re.compile(r"количеств\w+\s+(?:оригинальн\w+\s+)?(?:черных\s+|цветных\s+)?тонер[\s-]"),
    re.compile(r"наличи\w+.*стартов\w+.*картридж"),
    re.compile(r"суммарн\w+\s+емкост"),
    re.compile(r"объ[её]м\s+(?:установленн\w+\s+)?оперативн\w+\s+памят"),
    re.compile(r"класс\s+энергет\w+"),
    re.compile(r"врем\w+\s+выхода"),
    re.compile(r"время\s+первого"),
    re.compile(r"мощност"),
    re.compile(r"шум"),
    re.compile(r"масс[аы]"),
    re.compile(r"габарит"),
    re.compile(r"гарант"),
    re.compile(r"срок\s+эксплуатац"),
    re.compile(r"страна\s+происхожден"),
)


# ---------- Парсеры значений ----------

def _parse_colorness(values: list[str]) -> str | None:
    for v in values:
        n = _norm(v)
        if not n:
            continue
        # «Черно-Белая», «черно-белая», «ч/б», «ч-б», «монохромная»
        if "чёрно-бел" in n or "черно-бел" in n or "ч/б" in n or "ч-б" in n or "монохром" in n:
            return "ч/б"
        # «Цветная», «полноцветная»
        if "цветн" in n or "полноцветн" in n:
            return "цветной"
    return None


def _parse_max_format(values: list[str]) -> str | None:
    """А4 → A4, А3 → A3. A3 побеждает A4 при одновременном упоминании."""
    seen: set[str] = set()
    for v in values:
        n = _norm(v)
        # Принимаем кир. «а» и лат. «a», единственный формат в значении
        for m in re.finditer(r"\b[аa]\s*([34])\b", n):
            seen.add(f"A{m.group(1)}")
    if "A3" in seen:
        return "A3"
    if "A4" in seen:
        return "A4"
    return None


def _parse_yes_no(values: list[str]) -> str | None:
    for v in values:
        n = _norm(v)
        if n in ("да", "yes", "true", "+"):
            return "yes"
        if n in ("нет", "no", "false", "-"):
            return "no"
    return None


def _parse_print_technology(values: list[str]) -> str | None:
    """Возможные значения zakupki: «Лазерная», «Светодиодная»,
    «Электрографическая», «Струйная». Возвращаем как есть в нашем
    каноничном виде — `attribute_rules._EQUIVALENCE_GROUPS` сворачивает
    лазерная/светодиодная/электрографическая в одну семью при сравнении."""
    for v in values:
        n = _norm(v)
        if not n:
            continue
        if n.startswith("лазер"):
            return "лазерная"
        if n.startswith("светодиод"):
            return "светодиодная"
        if n.startswith("электрограф"):
            return "электрографическая"
        if n.startswith("струй"):
            return "струйная"
    return None


def _parse_print_speed(values: list[str]) -> int | None:
    for v in values:
        n = _parse_lower_bound_int(v)
        if n is not None:
            return n
    return None


def _parse_resolution(values: list[str]) -> int | None:
    """Может прийти от «по вертикали» и «по горизонтали» отдельно
    (см. `normalize_attrs` — мы агрегируем минимум всех найденных)."""
    nums: list[int] = []
    for v in values:
        n = _parse_lower_bound_int(v)
        if n is not None:
            nums.append(n)
    if not nums:
        return None
    return min(nums)


def _parse_starter_pages(values: list[str]) -> int | None:
    for v in values:
        n = _parse_lower_bound_int(v)
        if n is not None:
            return n
    return None


def _parse_connection(values: list[str]) -> tuple[str | None, str | None]:
    """Возвращает (usb, network_interface). USB → 'yes' если в значениях
    есть «USB»; network_interface — 'LAN' если есть проводной (LAN/
    Ethernet/RJ45), иначе 'WiFi' если есть Wi-Fi. Если оба — приоритет
    LAN (соответствует логике `name_attrs_parser.parse_network_interface`).

    Если поле «Способ подключения» вообще не пришло — оба None (лот не
    требует конкретного интерфейса; матчер пропустит SKU без проверки).
    """
    has_usb = False
    has_lan = False
    has_wifi = False
    saw_anything = False
    for v in values:
        n = _norm(v)
        if not n:
            continue
        saw_anything = True
        if "usb" in n:
            has_usb = True
        if re.search(r"\b(?:lan|ethernet|rj-?45)\b", n) or "проводн" in n:
            has_lan = True
        if re.search(r"wi[-\s]?fi", n) or "беспроводн" in n:
            has_wifi = True
    if not saw_anything:
        return (None, None)
    usb = "yes" if has_usb else "no"
    if has_lan:
        net = "LAN"
    elif has_wifi:
        net = "WiFi"
    else:
        net = None
    return (usb, net)


# ---------- Главная функция ----------

def normalize_attrs(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Принимает сырой dict «zakupki-ключ → значение или [значения]» из
    `card_parser._extract_position_attrs`, возвращает кортеж:

    - **normalized** — dict с ключами строго из `PRINTER_MFU_ATTRS`.
      Если соответствие не найдено — ключа в результате нет (это для
      матчера означает «лот не требует этот атрибут»).
    - **unknown_keys** — список zakupki-ключей, которые мы не сумели
      классифицировать (ни как schema-key, ни как «известный не-schema»).
      Вызывающий код один раз за прогон логирует список — это сигнал к
      возможному расширению schema или паттернов.
    """
    normalized: dict[str, Any] = {}
    unknown_keys: list[str] = []

    # Многие ключи у zakupki приходят парами («по вертикали»+«по горизонтали»
    # для разрешения; иногда два формата печати). Соберём по schema-ключу
    # все значения, потом распарсим.
    buckets: dict[str, list[str]] = {k: [] for k in _SCHEMA_KEYS}
    # «Способ подключения» — отдельная корзина, расщепится в usb +
    # network_interface на этапе парсинга значений.
    connection_values: list[str] = []
    saw_connection_key = False

    for key, value in raw.items():
        if not key:
            continue
        nk = _norm(key)
        values = _values_as_list(value)
        if _RE_PRINT_SPEED.search(nk):
            buckets["print_speed_ppm"].extend(values)
            continue
        if _RE_COLORNESS.search(nk):
            buckets["colorness"].extend(values)
            continue
        if _RE_MAX_FORMAT.search(nk):
            buckets["max_format"].extend(values)
            continue
        if _RE_DUPLEX.search(nk):
            buckets["duplex"].extend(values)
            continue
        if _RE_PRINT_TECH.search(nk):
            buckets["print_technology"].extend(values)
            continue
        if _RE_RESOLUTION.search(nk):
            buckets["resolution_dpi"].extend(values)
            continue
        # Ресурс стартового картриджа в страницах. Чтобы не зацепить
        # «Количество тонер-картриджей», требуем явное упоминание
        # «ресурс» либо «стартовый … страниц».
        if _RE_STARTER_RES.search(nk) or _RE_STARTER_PAGES_HINT.search(nk):
            buckets["starter_cartridge_pages"].extend(values)
            continue
        if _RE_CONNECTION.search(nk):
            saw_connection_key = True
            connection_values.extend(values)
            continue
        if any(p.search(nk) for p in _RE_KNOWN_NON_SCHEMA):
            continue
        unknown_keys.append(key)

    if buckets["colorness"]:
        v = _parse_colorness(buckets["colorness"])
        if v is not None:
            normalized["colorness"] = v
    if buckets["max_format"]:
        v = _parse_max_format(buckets["max_format"])
        if v is not None:
            normalized["max_format"] = v
    if buckets["duplex"]:
        v = _parse_yes_no(buckets["duplex"])
        if v is not None:
            normalized["duplex"] = v
    if buckets["print_technology"]:
        v = _parse_print_technology(buckets["print_technology"])
        if v is not None:
            normalized["print_technology"] = v
    if buckets["print_speed_ppm"]:
        v = _parse_print_speed(buckets["print_speed_ppm"])
        if v is not None:
            normalized["print_speed_ppm"] = v
    if buckets["resolution_dpi"]:
        v = _parse_resolution(buckets["resolution_dpi"])
        if v is not None:
            normalized["resolution_dpi"] = v
    if buckets["starter_cartridge_pages"]:
        v = _parse_starter_pages(buckets["starter_cartridge_pages"])
        if v is not None:
            normalized["starter_cartridge_pages"] = v
    if saw_connection_key:
        usb, net = _parse_connection(connection_values)
        if usb is not None:
            normalized["usb"] = usb
        if net is not None:
            normalized["network_interface"] = net

    return normalized, unknown_keys
