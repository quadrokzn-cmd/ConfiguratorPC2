# Извлечение обязательных характеристик блока питания из наименования.
#
# Обязательное поле в таблице psus одно — power_watts.
# В прайсе мощность указана по-разному: "500W", "750W 80+ Gold", "ATX 500W",
# "1000W 80+ Platinum", "400 Ватт", "2200W ATX".

import re

from portal.services.configurator.enrichment.base import ExtractedField

# Мощность: число, затем "W" (со словом-границей) или русское "Ватт".
# Ищем все вхождения и берём первое, которое выглядит как мощность блока
# питания для ПК (>= 100 Вт). Нижняя граница отсекает PoE-инжекторы
# ("POE-15-12W"), ошибочно попавшие в таблицу psus.
_POWER_RE = re.compile(r"\b(\d{2,4})\s*(?:W\b|Ватт)", re.IGNORECASE)

# Минимальная правдоподобная мощность PSU для ПК. Реальный минимум — около 300 Вт,
# но оставляем запас для редких компактных SFX/TFX-блоков.
_MIN_WATTS = 100


def extract(model: str) -> dict[str, ExtractedField]:
    """Извлекает power_watts из наименования блока питания."""
    if not model:
        return {}

    fields: dict[str, ExtractedField] = {}

    for m in _POWER_RE.finditer(model):
        value = int(m.group(1))
        if value >= _MIN_WATTS:
            fields["power_watts"] = ExtractedField(value, "regex", 1.0)
            break

    return fields
