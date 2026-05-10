"""Regex-парсер атрибутов SKU printers/mfu из имени прайс-позиции.

Контекст: основной источник `attrs_jsonb` для `printers_mfu` — это Claude
Code-обогащение (см. `exporter.py`/`importer.py` + `enrichment/auctions/`).
Claude Code обходит официальные сайты производителей, но для нишевых
брендов (G&G, Bulat, иногда Sindoh) сайт часто не отдаёт характеристики
→ в attrs_jsonb остаются `n/a` для critical-атрибутов. По решению
собственника №19 такие SKU не отбрасываются матчингом, что даёт false-
positive (G&G P2022W со скоростью 22 ppm проходит как primary в лот с
требованием ≥30 ppm).

Этот модуль — **fallback-источник**. Он смотрит в `printers_mfu.name`
(имя SKU из прайса дистрибьютора, например «G&G P2022W, Printer, Mono
laser, A4, 22 ppm (max 20000 p/mon), 1200x1200 dpi, USB, WiFi») и
извлекает атрибуты, которые там написаны явно. Не возвращает `n/a` —
ключ либо есть с конкретным значением, либо отсутствует в результате.

В пайплайне обогащения:
- Claude Code (`importer.py`) — primary: всегда выигрывает, перезаписывает
  всё.
- `parse_printer_attrs(name)` (этот модуль) — secondary: применяется
  только к ключам, у которых сейчас стоит `n/a`. Никогда не перезаписывает
  не-n/a значения от Claude Code или ручной правки.

Сравни с `app/services/auctions/match/name_attrs_parser.py`: тот парсер
работает с **именем позиции тендера** (где требования вида «не менее 30
стр/мин», «возможность сканирования А3»). Здесь — имя SKU, где
характеристики написаны как конкретные значения («22 ppm», «А4», «Mono
laser»).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


# ---- Скорость печати (ppm / стр/мин / страниц в минуту) -------------------

# Число + единица. Допускаем разделитель: пробел, неразрывный пробел, без
# пробела. Поддерживаем форматы ppm / pages per minute / стр./мин / страниц
# в минуту.
_RE_SPEED = re.compile(
    r"(?<![А-Яа-яA-Za-z0-9])(\d{1,3})\s*"
    r"(?:ppm|pages?\s*/\s*min|pages?\s+per\s+minute"
    r"|стр\.?\s*/\s*мин|страниц(?:\s*/\s*мин|\s+в\s+минуту))"
)


def _parse_speed_ppm(norm: str) -> int | None:
    """Возвращает первое валидное (1..199) число перед маркером скорости.

    Защита от заведомо неверных значений: «Принтер на 100 страниц» → не
    подойдёт, потому что «100 страниц» без «/мин» или «в минуту» не
    срабатывает; а вот «100 ppm» сработает (это валидная скорость
    промышленного принтера). Обрезаем диапазон 1..199 — выше реальных
    моделей нет, ниже — мусор.
    """
    for m in _RE_SPEED.finditer(norm):
        try:
            n = int(m.group(1))
        except ValueError:
            continue
        if 1 <= n <= 199:
            return n
    return None


# ---- Цветность ------------------------------------------------------------

# Вынесли в отдельные паттерны, чтобы при одновременном упоминании
# colorness и mono выбрать правильное (color приоритетнее, потому что
# «Mono laser» в строке цветного МФУ — это явная редкость).
_RE_MONO = re.compile(
    r"\b(?:mono(?:chrome)?(?:\s+laser)?|monochromatic|bw|b/w|b\.w\.)\b"
    r"|ч/б|ч-б|\bчб\b|чёрно-бел\w+|черно-бел\w+|монохромн\w+",
    re.IGNORECASE,
)
_RE_COLOR = re.compile(
    r"\b(?:colou?r|color\s+laser)\b|цветн\w+|полноцветн\w+",
    re.IGNORECASE,
)


def _parse_colorness(norm: str) -> str | None:
    has_color = bool(_RE_COLOR.search(norm))
    has_mono = bool(_RE_MONO.search(norm))
    if has_color and not has_mono:
        return "цветной"
    if has_mono and not has_color:
        return "ч/б"
    if has_color and has_mono:
        # Очень редкий случай: «mono laser, colour scanner» — у МФУ может
        # быть монохромная печать и цветной сканер. Цветность относится к
        # **печати**, mono выигрывает.
        return "ч/б"
    return None


# ---- Максимальный формат --------------------------------------------------

# A3 / A4 в окружении non-alnum. Кир. «А» (U+0410) и латинская «A»
# (U+0041). После цифры — не должна идти ещё цифра/буква (защита от A4plus,
# A35 и пр.).
_RE_FORMAT = re.compile(
    r"(?<![А-Яа-яA-Za-z0-9])([Аа]|[Aa])\s*([34])(?![А-Яа-яA-Za-z0-9])"
)


def _parse_max_format(norm: str) -> str | None:
    seen: set[str] = set()
    for m in _RE_FORMAT.finditer(norm):
        seen.add(f"A{m.group(2)}")
    if "A3" in seen:
        return "A3"
    if "A4" in seen:
        return "A4"
    return None


# ---- Дуплекс --------------------------------------------------------------

_RE_DUPLEX = re.compile(
    r"\bduplex\b|дуплекс|двусторонн\w+\s+печат\w*",
    re.IGNORECASE,
)
# Иногда сокращают `dn` / `dw` / `dnw` в конце MPN — это маркер duplex+net+wifi.
# Берём только если стоит как отдельный токен в конце слова и до него идут
# цифры (типичное окончание моделей: M3040dnwa, P2516dn).
_RE_DUPLEX_MPN_SUFFIX = re.compile(r"\b\w*\d\w*?(d)(?:n?w?a?)\b", re.IGNORECASE)


def _parse_duplex(norm: str) -> str | None:
    if _RE_DUPLEX.search(norm):
        return "yes"
    return None


# ---- Разрешение печати (dpi) ----------------------------------------------

# «1200 dpi», «1200x1200 dpi», «2400 x 600 dpi», «1200х1200dpi» (кир. х).
# Берём максимум по горизонтали (первое число), потому что у принтеров
# разрешение обычно 1200x1200, 2400x600 → «логическое» горизонтальное больше.
_RE_DPI = re.compile(
    r"(?<![А-Яа-яA-Za-z0-9])(\d{3,5})(?:\s*[xXхХ×]\s*\d{3,5})?\s*dpi\b",
    re.IGNORECASE,
)


def _parse_resolution_dpi(norm: str) -> int | None:
    """Берём максимум по горизонтальному разрешению (первое число до x)."""
    candidates: list[int] = []
    for m in _RE_DPI.finditer(norm):
        try:
            candidates.append(int(m.group(1)))
        except ValueError:
            continue
    if not candidates:
        return None
    return max(candidates)


# ---- Технология печати ----------------------------------------------------

_RE_LASER = re.compile(r"\blaser\w*|лазерн\w+", re.IGNORECASE)
_RE_LED = re.compile(r"\bled\b|светодиодн\w+", re.IGNORECASE)
_RE_INKJET = re.compile(r"\binkjet\w*|струйн\w+", re.IGNORECASE)
_RE_ELECTRO = re.compile(r"электрограф\w+", re.IGNORECASE)
_RE_THERMAL = re.compile(r"\bthermal\b|термопечат\w+|термопринтер", re.IGNORECASE)


def _parse_print_technology(norm: str) -> str | None:
    """Возвращает каноничное значение для PRINTER_MFU_ATTRS:
    'лазерная' | 'струйная' | 'светодиодная'. Электрография → 'лазерная'
    (по решению собственника №20: эквивалентны)."""
    if _RE_LASER.search(norm):
        return "лазерная"
    if _RE_LED.search(norm):
        return "светодиодная"
    if _RE_ELECTRO.search(norm):
        return "лазерная"
    if _RE_INKJET.search(norm):
        return "струйная"
    if _RE_THERMAL.search(norm):
        # 'термопечать' нет в схеме, но всё, что не lazer/inkjet/led —
        # пропускаем без значения (matcher не блокирует, attrs_jsonb=n/a).
        return None
    return None


# ---- USB ------------------------------------------------------------------

_RE_USB = re.compile(r"\busb\b", re.IGNORECASE)


def _parse_usb(norm: str) -> str | None:
    if _RE_USB.search(norm):
        return "yes"
    return None


# ---- Сетевой интерфейс (LAN / Wi-Fi) --------------------------------------

# В schema: list of [LAN, WiFi]. Возвращаем list, если что-то нашли.
_RE_LAN = re.compile(
    r"\b(?:lan|ethernet|rj-?45|gigabit\s+ethernet|10/100|10/100/1000)\b"
    r"|сетев\w+\s+интерфейс|проводн\w+\s+(?:сет|подключени)",
    re.IGNORECASE,
)
_RE_WIFI = re.compile(
    r"\bwi[\s\-]?fi\b|\bwifi\b|wlan|802\.11|беспроводн\w+",
    re.IGNORECASE,
)


def _parse_network_interface(norm: str) -> list[str] | None:
    """Возвращает list (как в PRINTER_MFU_ATTRS schema), либо None если
    ничего не нашли."""
    found: list[str] = []
    if _RE_LAN.search(norm):
        found.append("LAN")
    if _RE_WIFI.search(norm):
        found.append("WiFi")
    return found if found else None


# ---- Ресурс стартового картриджа в страницах ------------------------------

# Паттерны: «1500 страниц», «1.5K pages», «10K стартовый», «старт. картридж
# (700 стр.)», «starter cartridge 700 pages».
# Контекст: должно быть рядом упоминание «стартов*» / «starter» / «старт.»
# (иначе зацепим обычный ресурс полного картриджа, который для матчинга
# не нужен).
_RE_STARTER_PAGES_FULL = re.compile(
    r"(?:старт(?:овы\w*|\.)?\s+картридж\w*|starter\s+cartridge\w*)"
    r"[^0-9k]{0,40}?(\d{2,5})(?:\s*[\.,]\s*(\d))?\s*([kKкК])?\s*"
    r"(?:стр|pages?|p\b|страниц)",
    re.IGNORECASE,
)
# Обратный порядок: «(700 стр.)» сразу после «старт. картридж»
_RE_STARTER_PAGES_PARENS = re.compile(
    r"(?:старт(?:овы\w*|\.)?\s+картридж\w*|starter\s+cartridge\w*)"
    r"[^0-9]{0,30}?\(\s*(\d{2,5})(?:\s*[\.,]\s*(\d))?\s*([kKкК])?\s*"
    r"(?:стр|pages?|p\b|страниц)",
    re.IGNORECASE,
)


def _multiply_k(num: int, decimal_part: str | None, k_marker: str | None) -> int:
    """«1.5K» → 1500. «10K» → 10000. «700» (без K) → 700."""
    if k_marker:
        if decimal_part:
            try:
                d = int(decimal_part)
                return num * 1000 + d * 100
            except ValueError:
                return num * 1000
        return num * 1000
    return num


def _parse_starter_pages(norm: str) -> int | None:
    for pattern in (_RE_STARTER_PAGES_FULL, _RE_STARTER_PAGES_PARENS):
        m = pattern.search(norm)
        if m:
            try:
                num = int(m.group(1))
                pages = _multiply_k(num, m.group(2), m.group(3))
                if 50 <= pages <= 50_000:
                    return pages
            except (ValueError, IndexError):
                continue
    return None


# ---- Главный entrypoint ---------------------------------------------------

def _normalize(text: str) -> str:
    """NFC + сохраняем регистр (regex'ы IGNORECASE / используют классы
    кир/лат явно)."""
    if not text:
        return ""
    return unicodedata.normalize("NFC", text)


def parse_printer_attrs(name: str | None) -> dict[str, Any]:
    """Извлекает атрибуты SKU printer/mfu из имени прайс-позиции.

    Возвращает dict с подмножеством ключей PRINTER_MFU_ATTRS. Не возвращает
    `n/a` — ключ либо есть с конкретным значением, либо отсутствует.

    Поддерживаемые ключи:
    - print_speed_ppm (int): «22 ppm», «30 стр/мин», «40 страниц в минуту»
    - colorness (str): «ч/б» из «Mono», «monochrome», «Mono laser», «BW»;
      «цветной» из «Color».
    - max_format (str): «A4» / «A3» из «A4»/«А4»/«A3»/«А3» (кир/лат).
      A3 побеждает A4 при упоминании обоих.
    - duplex (str): «yes» из «duplex», «дуплекс», «двусторонняя печать».
    - resolution_dpi (int): из «1200 dpi», «2400x600 dpi» (макс по
      горизонтали).
    - print_technology (str): «лазерная» из «laser», «лазерн*»,
      «электрограф*»; «струйная» из «inkjet», «струйн*»; «светодиодная»
      из «LED», «светодиодн*».
    - usb (str): «yes» из «USB».
    - network_interface (list[str]): ["LAN"] / ["WiFi"] / ["LAN", "WiFi"]
      из «LAN»/«Ethernet»/«RJ-45» и «Wi-Fi»/«WiFi»/«WLAN».
    - starter_cartridge_pages (int): из «1500 страниц», «1.5K pages»,
      «старт. картридж (700 стр.)».

    Чувствителен к кириллице и латинице, регистронезависимый. Возвращает
    только конкретные значения; если паттерн не сработал — ключа нет в
    результате.
    """
    if not name:
        return {}
    norm = _normalize(name)
    out: dict[str, Any] = {}

    v = _parse_speed_ppm(norm)
    if v is not None:
        out["print_speed_ppm"] = v
    v = _parse_colorness(norm)
    if v is not None:
        out["colorness"] = v
    v = _parse_max_format(norm)
    if v is not None:
        out["max_format"] = v
    v = _parse_duplex(norm)
    if v is not None:
        out["duplex"] = v
    v = _parse_resolution_dpi(norm)
    if v is not None:
        out["resolution_dpi"] = v
    v = _parse_print_technology(norm)
    if v is not None:
        out["print_technology"] = v
    v = _parse_usb(norm)
    if v is not None:
        out["usb"] = v
    v = _parse_network_interface(norm)
    if v is not None:
        out["network_interface"] = v
    v = _parse_starter_pages(norm)
    if v is not None:
        out["starter_cartridge_pages"] = v
    return out
