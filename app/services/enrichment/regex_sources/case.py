# Извлечение обязательных характеристик корпуса ПК.
#
# Обязательные поля таблицы cases (001_init.sql):
#   supported_form_factors TEXT[]
#   has_psu_included       BOOLEAN
#   included_psu_watts     INT      (значим только если has_psu_included=True)

import re

from app.services.enrichment.base import ExtractedField


# Формфакторы. Порядок важен: специфичные (E-ATX, mATX, mITX) ищутся
# раньше общего ATX, чтобы их подстроки не интерпретировались ошибочно.
_FF_EATX  = re.compile(r"\bE-?ATX\b", re.IGNORECASE)
_FF_MATX  = re.compile(r"\b(?:mATX|Micro-?ATX|uATX)\b", re.IGNORECASE)
_FF_MITX  = re.compile(r"\b(?:mITX|Mini-?ITX)\b", re.IGNORECASE)
_FF_ITX   = re.compile(r"\bITX\b", re.IGNORECASE)
# ATX — без буквы/дефиса перед, чтобы не захватить 'E-ATX' или 'mATX'
_FF_ATX   = re.compile(r"(?<![A-Za-z\-])ATX\b", re.IGNORECASE)

# Наличие БП: приоритет явных маркеров в префиксе (price_loader их сохраняет)
# и описании.
_NO_PSU_RE = re.compile(
    r"без\s+блока\s+питания|без\s+БП|w/\s*o\s+PSU|w/o\s+FAN.*w/o\s+PSU",
    re.IGNORECASE,
)
_WITH_PSU_RE = re.compile(
    r"с\s+блоком\s+питания"
    r"|с\s+внешним\s+блоком\s+питания"
    r"|с\s+БП"
    r"|w/\s*PSU"
    r"|w/\d+\s*W|\bw/\s*\d+W",
    re.IGNORECASE,
)

# Мощность БП: "450Вт", "450 Ватт", "450W". Ищем только когда has_psu=True.
_PSU_WATTS_RE = re.compile(
    r"(\d{3,4})\s*(?:Вт|Ватт|W)\b",
    re.IGNORECASE,
)


def extract(model: str) -> dict[str, ExtractedField]:
    """Извлекает обязательные поля корпуса из наименования."""
    if not model:
        return {}

    fields: dict[str, ExtractedField] = {}

    # --- supported_form_factors ---
    forms = []
    seen = set()
    def _add(v):
        if v not in seen:
            seen.add(v)
            forms.append(v)

    if _FF_EATX.search(model):
        _add("E-ATX")
    if _FF_ATX.search(model):
        _add("ATX")
    if _FF_MATX.search(model):
        _add("mATX")
    # mini-ITX и просто ITX трактуем как одно значение "ITX"
    if _FF_MITX.search(model) or _FF_ITX.search(model):
        _add("ITX")

    if forms:
        fields["supported_form_factors"] = ExtractedField(forms, "regex", 1.0)

    # --- has_psu_included ---
    no_psu    = bool(_NO_PSU_RE.search(model))
    with_psu  = bool(_WITH_PSU_RE.search(model))

    watts = None
    m = _PSU_WATTS_RE.search(model)
    if m:
        v = int(m.group(1))
        if 200 <= v <= 2000:
            watts = v

    if no_psu and not with_psu:
        fields["has_psu_included"] = ExtractedField(False, "regex", 1.0)
    elif with_psu and not no_psu:
        fields["has_psu_included"] = ExtractedField(True, "regex", 1.0)
        if watts is not None:
            fields["included_psu_watts"] = ExtractedField(watts, "regex", 1.0)
    elif watts is not None and not no_psu:
        # Derived: в имени корпуса указана разумная мощность БП, отрицания
        # нет — значит, БП входит в комплект. Корпуса без БП не пишут
        # ватты в названии (POWERMAN/Foxline/Chieftec с цифрой W/Вт).
        fields["has_psu_included"] = ExtractedField(True, "derived", 0.85)
        fields["included_psu_watts"] = ExtractedField(watts, "derived", 0.85)
    # Иначе (оба маркера или нет никаких признаков) — NULL.

    return fields
