# Извлечение обязательных характеристик материнской платы.
#
# Обязательные поля таблицы motherboards (001_init.sql):
#   socket, chipset, form_factor, memory_type, has_m2_slot.
#
# Форматы в прайсе двойственны:
# - Длинный: "MB MSI INTEL B760 s1700, 4xDDR4(128GB), ..., 2xM.2, ..., mATX"
# - Короткий: "PRIME B760M-F", "B840M D3HP", "H610I-PLUS D4-CSM".

import re

from portal.services.configurator.enrichment.base import ExtractedField


# Чипсет → сокет. Справочник используется для вывода socket и как
# фильтр «валидный чипсет», чтобы отсечь ложные срабатывания regex'а.
# Memory_type по чипсету НЕ выводим (LGA1700 поддерживает и DDR4, и DDR5
# — от платы зависит).
_CHIPSET_SOCKET = {
    # Intel LGA1200 (Comet/Rocket Lake)
    "H410":  "LGA1200", "B460":  "LGA1200", "H470":  "LGA1200",
    "Z490":  "LGA1200", "W480":  "LGA1200", "Q570":  "LGA1200",
    "H510":  "LGA1200", "B560":  "LGA1200", "H570":  "LGA1200",
    "Z590":  "LGA1200",
    # Intel LGA1700 (Alder/Raptor Lake, 12-14 gen)
    "H610":  "LGA1700", "B660":  "LGA1700", "H670":  "LGA1700",
    "Z690":  "LGA1700", "Q670":  "LGA1700",
    "H770":  "LGA1700", "B760":  "LGA1700", "Z790":  "LGA1700",
    "W680":  "LGA1700",
    # Intel LGA1851 (Core Ultra Series 2, 2024+)
    "H810":  "LGA1851", "B840":  "LGA1851", "B860":  "LGA1851",
    "Z890":  "LGA1851", "Q870":  "LGA1851",
    # Intel HEDT
    "X299":  "LGA2066",
    # AMD AM4
    "A320":  "AM4", "B350":  "AM4", "X370":  "AM4",
    "B450":  "AM4", "X470":  "AM4",
    "A520":  "AM4", "B550":  "AM4", "X570":  "AM4",
    # AMD AM5
    "A620":  "AM5",
    "B650":  "AM5", "B650E": "AM5",
    "X670":  "AM5", "X670E": "AM5",
    "B850":  "AM5", "B850E": "AM5",
    "X870":  "AM5", "X870E": "AM5",
    # AMD Threadripper
    "TRX40": "sTRX4", "TRX50": "sTR5",
    "WRX80": "sWRX8", "WRX90": "sWRX8",
}

# Чипсет: буква + 3-4 цифры + опциональный 'E' (X870E / B650E).
# Суффиксы 'M' (mATX), 'I' (ITX), 'AM' (ASUS TUF) и т.п. — НЕ включаем
# в захват; они идут отдельными регулярками формфактора.
# После чипсета не требуем \b, т.к. ASUS/MSI лепят буквы вплотную
# ("B550I", "A620AM", "B760M-F").
_CHIPSET_MAIN_RE = re.compile(r"\b([ABHXZWQ]\d{3,4}E?)")
# Threadripper серия — начинается с T.
_CHIPSET_TR_RE = re.compile(r"\b(TRX\d{2,4}|WRX\d{2,3})\b")

# Socket в длинном формате: "s1700", "sAM4", "Socket AM5", "LGA1700".
_SOCKET_EXPLICIT_RE = re.compile(
    r"\bs(LGA\d{3,4}|AM[45]|TR[X]?\d)\b"     # s1700, sAM4, sTRX4
    r"|\b(LGA\d{3,4})\b"                     # LGA1700
    r"|Socket\s+([A-Za-z0-9]+)",             # Socket AM5
    re.IGNORECASE,
)

# Формфактор
_FF_EATX   = re.compile(r"\bE-?ATX\b", re.IGNORECASE)
_FF_MITX   = re.compile(r"\bMini-?ITX\b", re.IGNORECASE)
_FF_ITX    = re.compile(r"\bITX\b", re.IGNORECASE)
_FF_MATX   = re.compile(r"\b(?:mATX|Micro-?ATX|uATX)\b", re.IGNORECASE)
_FF_ATX    = re.compile(r"\bATX\b", re.IGNORECASE)
# Суффикс модели: "B760M-F" → mATX, "B550I AORUS" → ITX
_FF_MATX_SUFFIX_RE = re.compile(r"\b[ABHXZWQ]\d{3,4}E?M\b")
_FF_ITX_SUFFIX_RE  = re.compile(r"\b[ABHXZWQ]\d{3,4}E?I\b")

# Тип памяти
_MEMORY_EXPLICIT_RE = re.compile(r"\bDDR([345])\b")
_MEMORY_DDR_SUFFIX_RE = re.compile(r"(?<![A-Za-z0-9])D([345])\b")

# M.2
_M2_RE = re.compile(r"\bM\.2\b", re.IGNORECASE)


def extract(model: str) -> dict[str, ExtractedField]:
    """Извлекает обязательные поля материнской платы из наименования."""
    if not model:
        return {}

    fields: dict[str, ExtractedField] = {}

    # --- chipset + socket (socket для коротких — derived по чипсету) ---
    chipset = None
    for m in _CHIPSET_MAIN_RE.finditer(model):
        candidate = m.group(1).upper()
        if candidate in _CHIPSET_SOCKET:
            chipset = candidate
            break
    if chipset is None:
        m = _CHIPSET_TR_RE.search(model)
        if m:
            candidate = m.group(1).upper()
            if candidate in _CHIPSET_SOCKET:
                chipset = candidate

    if chipset:
        fields["chipset"] = ExtractedField(chipset, "regex", 1.0)

    # --- socket ---
    # Сначала пробуем явное упоминание в строке (длинный формат).
    socket_value = None
    m = _SOCKET_EXPLICIT_RE.search(model)
    if m:
        raw = (m.group(1) or m.group(2) or m.group(3) or "").upper()
        # "s1700" → "LGA1700" (префикс s опускается)
        if raw.isdigit():
            raw = "LGA" + raw
        socket_value = raw
        fields["socket"] = ExtractedField(socket_value, "regex", 1.0)
    elif chipset and chipset in _CHIPSET_SOCKET:
        # Fallback: определяем сокет по справочнику чипсетов (derived).
        socket_value = _CHIPSET_SOCKET[chipset]
        fields["socket"] = ExtractedField(socket_value, "derived", 1.0)

    # --- form_factor ---
    if _FF_EATX.search(model):
        fields["form_factor"] = ExtractedField("E-ATX", "regex", 1.0)
    elif _FF_MITX.search(model):
        fields["form_factor"] = ExtractedField("ITX", "regex", 1.0)
    elif _FF_MATX.search(model):
        fields["form_factor"] = ExtractedField("mATX", "regex", 1.0)
    elif _FF_ITX.search(model):
        fields["form_factor"] = ExtractedField("ITX", "regex", 1.0)
    elif _FF_MATX_SUFFIX_RE.search(model):
        # Суффикс 'M' у модели (B760M-F) надёжно означает mATX.
        fields["form_factor"] = ExtractedField("mATX", "regex", 0.95)
    elif _FF_ITX_SUFFIX_RE.search(model):
        fields["form_factor"] = ExtractedField("ITX", "regex", 0.95)
    elif _FF_ATX.search(model):
        fields["form_factor"] = ExtractedField("ATX", "regex", 1.0)
    elif chipset:
        # Если чипсет распознан, но формфактор не указан ни явно, ни
        # суффиксом M/I — это почти всегда полноразмерная ATX-плата.
        # Ставим derived ATX; этап 2.5Б при необходимости пересмотрит.
        fields["form_factor"] = ExtractedField("ATX", "derived", 0.8)

    # --- memory_type ---
    # Только явные сигналы — по чипсету/сокету НЕ выводим (LGA1700 амбивалентен).
    m = _MEMORY_EXPLICIT_RE.search(model)
    if m:
        fields["memory_type"] = ExtractedField(f"DDR{m.group(1)}", "regex", 1.0)
    else:
        m = _MEMORY_DDR_SUFFIX_RE.search(model)
        if m:
            fields["memory_type"] = ExtractedField(f"DDR{m.group(1)}", "regex", 0.9)

    # --- has_m2_slot ---
    # Пишем True только при явном упоминании "M.2".
    # Отсутствие упоминания — не гарантия отсутствия слота, поэтому False
    # не ставим; оставляем NULL под этап 2.5Б.
    if _M2_RE.search(model):
        fields["has_m2_slot"] = ExtractedField(True, "regex", 1.0)

    return fields
