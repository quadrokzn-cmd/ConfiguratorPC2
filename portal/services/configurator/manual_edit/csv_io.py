# Сериализация/десериализация значений для CSV-экспорта ручного редактирования.
#
# CSV формат фиксирован:
#   - разделитель колонок  — ';' (Excel на русской Windows);
#   - разделитель элементов массива внутри ячейки — '|';
#   - bool пишется как 'true'/'false';
#   - Decimal пишется с точкой;
#   - None в БД → пустая ячейка в CSV;
#   - специальное значение CLEAR_TOKEN в ячейке при импорте → «обнулить поле».

from __future__ import annotations

from decimal import Decimal
from typing import Any

from portal.services.configurator.manual_edit.schema import (
    ARRAY_CELL_SEP,
    CLEAR_TOKEN,
)


def serialize_cell(value: Any, *, is_array: bool) -> str:
    """Значение из БД → строка для записи в ячейку CSV."""
    if value is None:
        return ""
    if is_array:
        if not isinstance(value, (list, tuple)):
            # на случай строки, уже собранной в БД (не наш случай, но безопасно)
            return str(value)
        return ARRAY_CELL_SEP.join(str(x) for x in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        # убираем хвостовые нули и лишнюю точку, сохраняем точность
        s = format(value.normalize(), "f")
        if s.endswith("."):
            s = s[:-1]
        return s
    return str(value)


def parse_cell(
    raw: str,
    *,
    is_array: bool,
) -> tuple[str | None, bool]:
    """Ячейка CSV → подготовленное значение для валидатора.

    Возвращает кортеж (value, is_clear):
      - ('', True)  — ячейка содержит CLEAR_TOKEN → надо обнулить поле;
      - (None, False) — ячейка пустая → ничего не делать;
      - (str | list[str], False) — значение для валидации.
    """
    if raw is None:
        return None, False
    s = raw.strip()
    if s == "":
        return None, False
    if s == CLEAR_TOKEN:
        return None, True
    if is_array:
        parts = [x.strip() for x in s.split(ARRAY_CELL_SEP)]
        parts = [x for x in parts if x]
        return parts, False  # type: ignore[return-value]
    return s, False
