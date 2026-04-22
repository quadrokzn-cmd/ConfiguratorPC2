# Извлечение обязательных характеристик CPU из строки наименования прайса.
#
# Формат реальных позиций в прайсе OCS (наиболее частый):
#   "Процессор/ CPU <SOCKET> <Maker> <Model> (<характеристики через запятую>) OEM"
#   "Процессор/ APU LGA1200 Intel Core i5-11400 (Rocket Lake, 6C/12T, 2.6/4.4GHz, 12MB, 65/154W, UHD Graphics 730) OEM"
#
# В этом экстракторе покрываем только обязательные поля таблицы cpus
# (см. migrations/001_init.sql): socket, cores, threads, base_clock_ghz,
# turbo_clock_ghz, tdp_watts, has_integrated_graphics, memory_type, package_type.

import re

from app.services.enrichment.base import ExtractedField


# Соответствие «сокет → поддерживаемые типы памяти» для обязательного
# поля memory_type. У CPU это означает «что контроллер памяти поддерживает»,
# а не «что стоит в конкретной сборке» — поэтому для LGA1700 валидно DDR4+DDR5.
# Источник для таких значений — 'derived', а не 'regex'.
_SOCKET_MEMORY_TYPE = {
    "AM4":     "DDR4",
    "AM5":     "DDR5",
    "LGA1200": "DDR4",
    "LGA1700": "DDR4+DDR5",
    "LGA1851": "DDR5",
    "SP5":     "DDR5",
    "LGA4677": "DDR5",
    "LGA4710": "DDR5",
}

# Сокет: LGA1700 / AM5 / SP5 / sTR5 / LGA4710 ...
# Справа используем (?!\d) вместо \b: в прайсе встречаются склеенные случаи
# вида "LGA4677Intel Xeon..." — между сокетом и маркой нет пробела, и обычный
# \b там не срабатывает. Левый \b оставляем, чтобы не захватить часть слова.
_SOCKET_RE = re.compile(r"\b(LGA\d{3,4}|AM[45]|SP\d|sTR\d|TR\d|sWRX\d)(?!\d)")

# Гибридные ядра Alder/Raptor/Arrow Lake:
#   (8P+4E)C/(16P+4E)T  — базовое число и потоки оба гибридные
#   (8P+8E)C/24T        — суммарные потоки указаны одним числом
_CORES_HYBRID_FULL = re.compile(r"\((\d+)P\+(\d+)E\)C/\((\d+)P\+(\d+)E\)T")
_CORES_HYBRID_HALF = re.compile(r"\((\d+)P\+(\d+)E\)C/(\d+)T")

# Классическое число ядер/потоков: 8C/16T
_CORES_SIMPLE = re.compile(r"(\d+)C/(\d+)T")

# Частоты: "3.6/4.3GHz" — база/турбо
_GHZ_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*GHz",
    re.IGNORECASE,
)

# TDP: одиночное 125W либо пара 65/190W — берём максимум (пиковое)
_TDP_RE = re.compile(r"(\d+)(?:/(\d+))?\s*W\b")

# Тип поставки
_PACKAGE_RE = re.compile(r"\b(OEM|BOX)\b")

# Признаки встроенной графики в характеристиках в скобках
_IGPU_RE = re.compile(
    r"\b(UHD|Radeon|Iris|HD\s+Graphics|Vega|Graphics)\b",
    re.IGNORECASE,
)


def extract(model: str) -> dict[str, ExtractedField]:
    """Извлекает обязательные характеристики CPU из наименования.

    Возвращает словарь {field_name: ExtractedField}. Поля, которые
    не удалось извлечь, в результате отсутствуют.
    """
    if not model:
        return {}

    fields: dict[str, ExtractedField] = {}

    # Блок характеристик — содержимое между первой '(' и последней ')'.
    # Берём именно внешние скобки, потому что у гибридных CPU внутри встречаются
    # вложенные, например: "(Alder Lake, (8P+4E)C/(16P+4E)T, 3.6/5GHz, 25MB, 125/190W, UHD Graphics 770)".
    first = model.find("(")
    last  = model.rfind(")")
    has_parens = 0 <= first < last
    inner = model[first + 1:last] if has_parens else ""

    # --- socket (+ memory_type как производное) ---
    m = _SOCKET_RE.search(model)
    if m:
        socket = m.group(1).upper()
        fields["socket"] = ExtractedField(socket, "regex", 1.0)

        mem = _SOCKET_MEMORY_TYPE.get(socket)
        if mem is not None:
            fields["memory_type"] = ExtractedField(mem, "derived", 1.0)

    # --- cores / threads ---
    m = _CORES_HYBRID_FULL.search(model)
    if m:
        p1, e1, p2, e2 = map(int, m.groups())
        fields["cores"]   = ExtractedField(p1 + e1, "regex", 1.0)
        fields["threads"] = ExtractedField(p2 + e2, "regex", 1.0)
    else:
        m = _CORES_HYBRID_HALF.search(model)
        if m:
            p, e, t = map(int, m.groups())
            fields["cores"]   = ExtractedField(p + e, "regex", 1.0)
            fields["threads"] = ExtractedField(t,     "regex", 1.0)
        else:
            m = _CORES_SIMPLE.search(model)
            if m:
                fields["cores"]   = ExtractedField(int(m.group(1)), "regex", 1.0)
                fields["threads"] = ExtractedField(int(m.group(2)), "regex", 1.0)

    # --- base_clock / turbo_clock ---
    m = _GHZ_RE.search(model)
    if m:
        fields["base_clock_ghz"]  = ExtractedField(float(m.group(1)), "regex", 1.0)
        fields["turbo_clock_ghz"] = ExtractedField(float(m.group(2)), "regex", 1.0)

    # --- tdp_watts ---
    # В прайсе TDP указан либо одним числом (65W), либо парой base/peak
    # (например, 125/190W или 65/154W). По договорённости берём СТАНДАРТНОЕ
    # (базовое) тепловыделение — первое число пары. Пиковое (PL2/boost TDP)
    # не сохраняем: оно кратковременное и нерепрезентативно для расчётов.
    # Ищем по всей строке: у серверных CPU скобки могут не содержать
    # характеристик (например, "(clean pulled)"), а TDP при этом указан
    # вне скобок ("... 200W SP5 ...").
    m = _TDP_RE.search(model)
    if m:
        base_w = int(m.group(1))
        fields["tdp_watts"] = ExtractedField(base_w, "regex", 1.0)

    # --- package_type ---
    m = _PACKAGE_RE.search(model)
    if m:
        fields["package_type"] = ExtractedField(m.group(1).upper(), "regex", 1.0)

    # --- has_integrated_graphics ---
    # True, если в скобках характеристик есть упоминание iGPU (UHD/Radeon/…).
    # False, если скобки есть, но упоминания нет (типичный Intel «F», «KF»,
    # AMD Ryzen без суффикса «G», серверные Xeon/EPYC без iGPU).
    # Если скобок нет — поле не заполняем (нечего анализировать).
    if has_parens:
        has_igpu = bool(_IGPU_RE.search(inner))
        fields["has_integrated_graphics"] = ExtractedField(
            has_igpu, "regex", 1.0 if has_igpu else 0.8,
        )

    return fields
