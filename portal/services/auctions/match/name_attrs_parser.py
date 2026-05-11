"""Извлечение атрибутов лота из `tender_items.name`.

Контекст: ингест-парсер `card_parser.py` в текущей версии складывает все характеристики
позиции одной строкой в `name` (а `required_attrs_jsonb` остаётся пустым). Делать
полноценный rewrite парсера карточки — за рамками Волны 2; здесь — текстовая
эмпирика на 188 печатных позициях из БД (прогон 2026-05-07).

Возвращает dict с ключами из `app/services/auctions/catalog/enrichment/schema.py`
(`PRINTER_MFU_ATTRS`). Если паттерн не найден — ключа в результате нет (значит,
требование лота на этот атрибут не зафиксировано → SKU фильтр не отсеивает).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# Маркеры значения «Да/Нет»
_YES = {"да", "yes", "true"}
_NO = {"нет", "no", "false"}

# Класс символов «формат» — кир. А (U+0410), кир. а (U+0430) и латинские A/a
_FORMAT_LETTER = "[АаAa]"


def _normalize(text: str) -> str:
    """Лёгкая нормализация: NFC + lower. Без замен кириллицы — иначе ломается
    «печ + кир.а + ти» (если бы заменили кир.а→лат.a) и regex с `печати` не работает.
    Замены кир→лат делаем точечно в нужных regex (`max_format`)."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    return text.lower()


def _parse_int_after(norm_text: str, pattern: str) -> int | None:
    m = re.search(pattern, norm_text)
    if not m:
        return None
    try:
        return int(m.group(1))
    except (ValueError, IndexError):
        return None


def parse_colorness(norm_text: str) -> str | None:
    # Берём окно после «Цветность [печати]» и в нём ищем ключевые слова.
    m = re.search(r"цветност[ьи](?:\s+печати)?\s+([^.,;]{1,40})", norm_text)
    if m:
        window = m.group(1)
        if "черно" in window or "ч/б" in window or "ч-б" in window:
            return "ч/б"
        if "цветн" in window or "полноцветн" in window:
            return "цветной"
    if "черно-белая" in norm_text or "чёрно-белая" in norm_text:
        return "ч/б"
    return None


def parse_max_format(norm_text: str) -> str | None:
    """Извлекает A3/A4. Логика: если в тексте встречается «А3» хотя бы один раз —
    устройство A3 (умеет и A4 тоже). Если только «А4» — A4-only.

    Триггеры на A3/A4: упоминания форматов в любом контексте печати/сканирования/
    копирования/поддержки. Регулярки эмпирические по 188 печатным позициям zakupki
    (см. диагностику Волны 2: «возможность сканирования в форматах A3», «в формате
    А3», «формат сканирования А3», «Максимальный формат печати А4»)."""
    triggers = (
        rf"(?:максимальн\w+\s+)?формат(?:\w+)?(?:\s+(?:печати|сканировани\w+|копировани\w+))?\s+({_FORMAT_LETTER}[34])\b",
        rf"(?:в|во)\s+формат\w+\s+({_FORMAT_LETTER}[34])\b",
        rf"(?:поддержк\w+|возможност[ьи]\s+\w+(?:\s+\w+){{0,3}})\s+({_FORMAT_LETTER}[34])\b",
        rf"(?:сканировани\w+|печат\w+|копировани\w+)[^.,;]{{0,30}}?\b({_FORMAT_LETTER}[34])\b",
    )
    seen: set[str] = set()
    for pattern in triggers:
        for m in re.finditer(pattern, norm_text):
            digit = m.group(1)[1]
            seen.add(f"A{digit}")
    if "A3" in seen:
        return "A3"
    if "A4" in seen:
        return "A4"
    return None


def parse_duplex(norm_text: str) -> str | None:
    m = re.search(
        r"(?:возможност[ьи]\s+)?(?:автоматическ\w+\s+)?двухсторонн\w+\s+печат\w+\s+(да|нет|yes|no)\b",
        norm_text,
    )
    if m:
        return "yes" if m.group(1) in _YES else "no"
    return None


def parse_print_speed_ppm(norm_text: str) -> int | None:
    """«Скорость [черно-белой/цветной] печати [в формате A3/A4] [не менее|≥] N стр/мин|ppm».

    Стратегия: явные «маркер→число» паттерны имеют приоритет над эвристикой
    «первое число после слова "печати"». Чтобы не зацепить цифру из «А3»/«A4»,
    запрещаем, чтобы число шло сразу после буквы «А»/«A» (кир/лат, любой регистр).
    Эмпирика по диагностике Волны 2: «Скорость цветной печати в формате А3,
    стр/мин ≥ 20» — должно вернуть 20, а не 3 из «А3».

    Применяется только в окне после слова «скорость», чтобы не ловить цифры из
    других характеристик (например, ресурс картриджа)."""
    no_a_letter = r"(?<![АAaа])"
    speed_window = re.search(r"скорост[ьи][^;]{0,200}", norm_text)
    if not speed_window:
        return None
    window = speed_window.group(0)
    explicit_patterns = (
        rf"(?:не\s+менее|≥|>=|от)\s*{no_a_letter}(\d+)",
        rf"{no_a_letter}(\d+)\s*(?:стр\.?\s*/\s*мин|стр/мин|ppm)",
    )
    for pattern in explicit_patterns:
        for m in re.finditer(pattern, window):
            try:
                return int(m.group(1))
            except (ValueError, IndexError):
                continue
    fallback = (
        rf"скорост[ьи]\s+(?:[\w\-ё]+\s+)?печати"
        rf"[^0-9]{{0,40}}?{no_a_letter}(\d+)"
    )
    return _parse_int_after(norm_text, fallback)


def parse_print_technology(norm_text: str) -> str | None:
    m = re.search(r"технологи[яи]\s+печати\s+([\wё]+)", norm_text)
    if not m:
        return None
    v = m.group(1)
    if v.startswith("лазер"):
        return "лазерная"
    if v.startswith("струй"):
        return "струйная"
    if v.startswith("светодиод"):
        return "светодиодная"
    if v.startswith("электрограф"):
        return "электрографическая"
    return None


def parse_usb(norm_text: str) -> str | None:
    if re.search(r"способ\s+подключени[яе][^.]{0,80}\busb\b", norm_text):
        return "yes"
    if re.search(r"\busb\b", norm_text) and "подключени" in norm_text:
        return "yes"
    return None


def parse_network_interface(norm_text: str) -> str | None:
    """Возвращает один интерфейс (LAN/WiFi). Bluetooth/Ethernet кроме LAN не учитываем."""
    if re.search(r"wi[-\s]?fi", norm_text):
        return "WiFi"
    if re.search(r"\b(?:lan|ethernet|rj-?45)\b", norm_text):
        return "LAN"
    return None


def parse_resolution_dpi(norm_text: str) -> int | None:
    pattern = r"разрешени[ея][^,]{0,80}?dpi[^0-9]{0,30}(\d+)"
    return _parse_int_after(norm_text, pattern)


def parse_starter_cartridge_pages(norm_text: str) -> int | None:
    pattern = r"(?:ресурс|стартов\w+).{0,60}?картридж\w*[^0-9]{0,40}(\d{2,7})"
    return _parse_int_after(norm_text, pattern)


def extract_attrs_from_name(name: str | None) -> dict[str, Any]:
    """Главный entrypoint."""
    if not name:
        return {}
    norm = _normalize(name)
    out: dict[str, Any] = {}

    val = parse_colorness(norm)
    if val is not None:
        out["colorness"] = val
    val = parse_max_format(norm)
    if val is not None:
        out["max_format"] = val
    val = parse_duplex(norm)
    if val is not None:
        out["duplex"] = val
    val = parse_print_speed_ppm(norm)
    if val is not None:
        out["print_speed_ppm"] = val
    val = parse_print_technology(norm)
    if val is not None:
        out["print_technology"] = val
    val = parse_usb(norm)
    if val is not None:
        out["usb"] = val
    val = parse_network_interface(norm)
    if val is not None:
        out["network_interface"] = val
    val = parse_resolution_dpi(norm)
    if val is not None:
        out["resolution_dpi"] = val
    val = parse_starter_cartridge_pages(norm)
    if val is not None:
        out["starter_cartridge_pages"] = val
    return out


def merge_required_attrs(jsonb_attrs: dict[str, Any] | None, parsed_attrs: dict[str, Any]) -> dict[str, Any]:
    """Если ингест когда-нибудь начнёт класть структурированные атрибуты в
    `required_attrs_jsonb` — они приоритетнее, name-парсер только дополняет."""
    merged = dict(parsed_attrs)
    if jsonb_attrs:
        for k, v in jsonb_attrs.items():
            if v is not None:
                merged[k] = v
    return merged
