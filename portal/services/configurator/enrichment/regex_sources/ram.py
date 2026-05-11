# Извлечение обязательных характеристик оперативной памяти.
#
# Обязательные поля таблицы rams: memory_type, form_factor, module_size_gb,
# modules_count, frequency_mhz.
#
# Форматы в прайсе OCS:
#   Kingston: "Kingston 32GB 3600MT/s DDR4 CL17 DIMM (Kit of 4) FURY Beast"
#   Apacer:   "Модуль памяти DIMM DDR5-6000 32GB (16GBx2) AH5U..."
#   Foxline:  "Foxline DIMM 32GB 2933 DDR4 CL 21 (2Gb*8)"
#   Samsung:  "Samsung DDR4 8GB SODIMM 3200, 1.2V"

import re

from portal.services.configurator.enrichment.base import ExtractedField

_MEM_TYPE_RE = re.compile(r"\b(DDR[2345]L?)\b", re.IGNORECASE)

# Формфакторы памяти. В схеме БД допустимы "DIMM" и "SO-DIMM".
# Варианты UDIMM/CUDIMM/RDIMM/LRDIMM — подтипы обычного DIMM (unbuffered,
# clocked, registered) — приводим к "DIMM".
_FORM_FACTOR_RE = re.compile(
    r"\b(SO[- ]?DIMM|U?DIMM|CUDIMM|L?RDIMM)\b", re.IGNORECASE,
)

# Частота: 1) с единицей измерения, 2) через префикс DDRx-N, 3) известное значение
_FREQ_WITH_UNIT_RE  = re.compile(r"(\d{3,5})\s*(?:MHz|MT/?s)\b", re.IGNORECASE)
_FREQ_DDR_PREFIX_RE = re.compile(r"DDR[345]L?-(\d{3,5})", re.IGNORECASE)
_FREQ_KNOWN_RE = re.compile(
    r"\b(667|800|1066|1333|1600|1866|2133|2400|2666|2800|2933|3000|3200|3466|"
    r"3600|3733|3800|4000|4266|4400|4800|5200|5333|5600|5800|6000|6200|6400|"
    r"6600|6800|7000|7200|7600|8000|8200|8400|8600)\b"
)

# Комплект модулей: "Kit of 4", "(Kit of 2)"
_KIT_RE = re.compile(r"Kit\s+of\s+(\d+)", re.IGNORECASE)

# Объём.
# Префиксный паттерн "NxM<Gb>" (Patriot-стиль: "DDR5 2x16Gb 6800MHz") —
# модулей N, размер каждого M. Регистр любой, т.к. Patriot пишет малой 'b'.
_NX_PREFIX_RE = re.compile(r"(\d+)\s*x\s*(\d+)\s*G[Bb]\b")

# Постфиксный паттерн "<размер>GBx<N>" или "<размер>GB*<N>" (Apacer-стиль:
# "(16GBx2)"). Здесь обязательно КАПС 'GB', иначе совпадёт Foxline-формат
# "(2Gb*8)", где маленькая 'b' означает гигабиты (ёмкость чипов, а не модулей).
_NXMGB_UPPER_RE = re.compile(r"(\d+)\s*GB\s*[x×*]\s*(\d+)\b")

# Одиночный общий объём — допускаем любой регистр ('GB' у Kingston,
# 'Gb' у Patriot); ложное срабатывание по Foxline "2Gb*8" уже отсеивается
# на уровне _NXMGB_UPPER_RE (ищем сначала NX-паттерны, потом total).
_TOTAL_GB_RE = re.compile(r"(\d+)\s*G[Bb]\b")


def extract(model: str) -> dict[str, ExtractedField]:
    """Извлекает обязательные поля ОЗУ из наименования."""
    if not model:
        return {}

    fields: dict[str, ExtractedField] = {}

    # --- memory_type ---
    m = _MEM_TYPE_RE.search(model)
    if m:
        fields["memory_type"] = ExtractedField(m.group(1).upper(), "regex", 1.0)

    # --- form_factor ---
    # Схема допускает значения "DIMM" / "SO-DIMM" (см. 001_init.sql).
    # Варианты UDIMM/CUDIMM/RDIMM/LRDIMM — все подтипы обычного DIMM.
    m = _FORM_FACTOR_RE.search(model)
    if m:
        raw = m.group(1).upper().replace(" ", "").replace("-", "")
        value = "SO-DIMM" if raw.startswith("SO") else "DIMM"
        fields["form_factor"] = ExtractedField(value, "regex", 1.0)

    # --- frequency_mhz ---
    freq = None
    for rx in (_FREQ_WITH_UNIT_RE, _FREQ_DDR_PREFIX_RE, _FREQ_KNOWN_RE):
        m = rx.search(model)
        if m:
            freq = int(m.group(1))
            break
    if freq is not None:
        fields["frequency_mhz"] = ExtractedField(freq, "regex", 1.0)

    # --- module_size_gb / modules_count ---
    # Порядок проб:
    #   1) префиксный "NxMGb/NxMGB" — модулей N, размер M (Patriot)
    #   2) постфиксный "MGBxN" — размер M, модулей N (Apacer и др.)
    #   3) "Kit of N" + общий объём — размер = total/N
    #   4) одиночный модуль с общим объёмом
    nx_pref = _NX_PREFIX_RE.search(model)
    nx_post = _NXMGB_UPPER_RE.search(model)
    kit     = _KIT_RE.search(model)
    total   = _TOTAL_GB_RE.search(model)

    if nx_pref:
        fields["modules_count"]  = ExtractedField(int(nx_pref.group(1)), "regex", 1.0)
        fields["module_size_gb"] = ExtractedField(int(nx_pref.group(2)), "regex", 1.0)
    elif nx_post:
        fields["module_size_gb"] = ExtractedField(int(nx_post.group(1)), "regex", 1.0)
        fields["modules_count"]  = ExtractedField(int(nx_post.group(2)), "regex", 1.0)
    elif kit and total:
        n = int(kit.group(1))
        total_gb = int(total.group(1))
        if n > 0 and total_gb % n == 0:
            fields["module_size_gb"] = ExtractedField(total_gb // n, "regex", 1.0)
            fields["modules_count"]  = ExtractedField(n, "regex", 1.0)
    elif total:
        fields["module_size_gb"] = ExtractedField(int(total.group(1)), "regex", 1.0)
        fields["modules_count"]  = ExtractedField(1, "regex", 0.9)

    return fields
