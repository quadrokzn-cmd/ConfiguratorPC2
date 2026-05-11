"""Схема обогащения атрибутов принтеров и МФУ через Claude Code.

Целевой набор полей зафиксирован собственником в плане
`plans/2026-04-23-platforma-i-aukciony.md` (Волна 1А-β). Любое поле, которое
не удалось найти на официальном сайте производителя, должно прийти как
строка `"n/a"` — в матчинге это трактуется как «подходит, но менеджер
должен уточнить вручную».
"""

from __future__ import annotations

NA = "n/a"

# Допустимые значения для enum-полей. Регистр и точное написание важны:
# валидатор проверяет ровно эти строки.
COLORNESS_VALUES: frozenset[str] = frozenset({"ч/б", "цветной"})
MAX_FORMAT_VALUES: frozenset[str] = frozenset({"A4", "A3"})
DUPLEX_VALUES: frozenset[str] = frozenset({"yes", "no"})
USB_VALUES: frozenset[str] = frozenset({"yes", "no"})
PRINT_TECH_VALUES: frozenset[str] = frozenset({"лазерная", "струйная", "светодиодная"})
NETWORK_INTERFACE_VALUES: frozenset[str] = frozenset({"LAN", "WiFi"})

# Схема целевых атрибутов: имя поля -> человекочитаемое описание типа.
# Описание используется в промт-шаблоне и в UI-tooltip.
PRINTER_MFU_ATTRS: dict[str, str] = {
    "print_speed_ppm":         "int | n/a",
    "colorness":               "ч/б | цветной | n/a",
    "max_format":              "A4 | A3 | n/a",
    "duplex":                  "yes | no | n/a",
    "resolution_dpi":          "int | n/a",
    "network_interface":       "list of [LAN, WiFi] | n/a",
    "usb":                     "yes | no | n/a",
    "starter_cartridge_pages": "int | n/a",
    "print_technology":        "лазерная | струйная | светодиодная | n/a",
}

# Источник, под которым atts_jsonb пишутся в БД.
SOURCE_CLAUDE_CODE = "claude_code"
SOURCE_MANUAL = "manual"

# Размер батча по умолчанию.
DEFAULT_BATCH_SIZE = 30


def _validate_int(field: str, value) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return f"{field}: ожидался int, пришло {type(value).__name__}"
    if value < 0:
        return f"{field}: int должен быть >= 0, пришло {value}"
    return None


def _validate_enum(field: str, value, allowed: frozenset[str]) -> str | None:
    if not isinstance(value, str):
        return f"{field}: ожидался enum-string, пришло {type(value).__name__}"
    if value not in allowed:
        return f"{field}: значение '{value}' не в {sorted(allowed)}"
    return None


def _validate_list_enum(field: str, value, allowed: frozenset[str]) -> str | None:
    if not isinstance(value, list):
        return f"{field}: ожидался список, пришло {type(value).__name__}"
    for item in value:
        if not isinstance(item, str):
            return f"{field}: элементы списка должны быть строками, пришло {type(item).__name__}"
        if item not in allowed:
            return f"{field}: элемент '{item}' не в {sorted(allowed)}"
    return None


def validate_attrs(payload: dict) -> list[str]:
    """Возвращает список строк-ошибок (пусто => payload валиден).

    Любое поле может быть строкой "n/a" — это не ошибка, а маркер «не нашли
    на сайте производителя». Все 9 ключей обязаны присутствовать.
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        return [f"payload должен быть dict, пришло {type(payload).__name__}"]

    missing = [k for k in PRINTER_MFU_ATTRS if k not in payload]
    if missing:
        errors.append(f"отсутствуют поля: {missing}")

    extra = [k for k in payload if k not in PRINTER_MFU_ATTRS]
    if extra:
        errors.append(f"лишние поля: {extra}")

    for field, value in payload.items():
        if field not in PRINTER_MFU_ATTRS:
            continue
        if value == NA:
            continue
        if field in ("print_speed_ppm", "resolution_dpi", "starter_cartridge_pages"):
            err = _validate_int(field, value)
        elif field == "colorness":
            err = _validate_enum(field, value, COLORNESS_VALUES)
        elif field == "max_format":
            err = _validate_enum(field, value, MAX_FORMAT_VALUES)
        elif field == "duplex":
            err = _validate_enum(field, value, DUPLEX_VALUES)
        elif field == "usb":
            err = _validate_enum(field, value, USB_VALUES)
        elif field == "print_technology":
            err = _validate_enum(field, value, PRINT_TECH_VALUES)
        elif field == "network_interface":
            err = _validate_list_enum(field, value, NETWORK_INTERFACE_VALUES)
        else:
            err = None
        if err:
            errors.append(err)

    return errors
