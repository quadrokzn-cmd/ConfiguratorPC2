# Извлечение обязательных характеристик видеокарты.
#
# Обязательные поля таблицы gpus (001_init.sql):
#   vram_gb, vram_type, tdp_watts, needs_extra_power, video_outputs,
#   core_clock_mhz, memory_clock_mhz.
#
# Regex реально извлекает только vram_gb и (реже) vram_type.
# Остальные 5 полей системно отсутствуют в наименованиях прайса и
# целиком уходят в этап 2.5Б (AI-обогащение по чипу GPU).

import re

from app.services.enrichment.base import ExtractedField


# Объём видеопамяти.
# Встречаются форматы: "16GB", "16Gb", "8G", "-8GD" (Gigabyte), "O16G" (ASUS),
# "48 GB GDDR6". Берём первый «разумный» (<= 128 GB).
# Регистр у буквы «b»: допускаем и "GB", и "Gb" — у AFox/Biostar/Gigabyte
# в Merlion-прайсе встречается нижний регистр ("4Gb 128bit GDDR5").
_VRAM_GB_EXPLICIT_RE = re.compile(r"\b(\d+)\s*G[Bb]\b")
# Суффикс артикула Gigabyte/ASUS: "-8GD", "-2GD5" (GD + версия DDR), "-8GL"
_VRAM_GB_DASH_RE     = re.compile(r"-(\d+)\s*G[A-Z]\d?\b")
# ASUS префикс "O": "-O16G", "-O6G", "-O16GGAMING" (без пробела после G)
_VRAM_GB_O_PREFIX_RE = re.compile(r"-O(\d+)G(?=[A-Z]|\b)")
# Голое "16G GAMING"
_VRAM_GB_BARE_RE     = re.compile(r"\b(\d+)\s*G\b")

# Тип видеопамяти: берём первое вхождение GDDR[n][X]
_VRAM_TYPE_RE = re.compile(r"\bG?DDR\dX?\b")

# Сокращение типа VRAM: "D6", "D6X", "D7" — встречается в Merlion-формате
# у MAXSUN / INNO3D / ASUS / GIGABYTE (тип памяти в конце строки через запятую:
# "..., 8G, D7", "8G,D7", "16G D6").
# Слева требуем запятую/пробел (не буква и не цифра), справа — конец строки,
# запятая, пробел, точка с запятой: иначе поймаем "WDS100T1X0M" или суффикс
# модели "-8GD5".
_VRAM_TYPE_SHORT_RE = re.compile(
    r"(?:,|\s)\s*D(\d)(X?)(?=[\s,;]|$)",
)

# Разумные объёмы VRAM: отсеиваем случайные совпадения
_MAX_VRAM_GB = 128


def _pick_vram(model: str) -> int | None:
    """Пробует по очереди паттерны VRAM; возвращает первое правдоподобное число."""
    for rx in (_VRAM_GB_EXPLICIT_RE, _VRAM_GB_DASH_RE, _VRAM_GB_O_PREFIX_RE, _VRAM_GB_BARE_RE):
        for m in rx.finditer(model):
            v = int(m.group(1))
            if 1 <= v <= _MAX_VRAM_GB:
                return v
    return None


def extract(model: str) -> dict[str, ExtractedField]:
    """Извлекает обязательные поля видеокарты, которые видны в наименовании."""
    if not model:
        return {}

    fields: dict[str, ExtractedField] = {}

    # --- vram_gb ---
    v = _pick_vram(model)
    if v is not None:
        fields["vram_gb"] = ExtractedField(v, "regex", 1.0)

    # --- vram_type ---
    m = _VRAM_TYPE_RE.search(model)
    if m:
        # GDDR без X префикса оставляем как есть. Схема допускает
        # GDDR6 / GDDR6X / GDDR7 / DDR3 / DDR4 / DDR5.
        value = m.group(0).upper()
        fields["vram_type"] = ExtractedField(value, "regex", 1.0)
    else:
        # Merlion-сокращение "D6"/"D7"/"D6X" → GDDR6 / GDDR7 / GDDR6X.
        m = _VRAM_TYPE_SHORT_RE.search(model)
        if m:
            digit = m.group(1)
            suffix = (m.group(2) or "").upper()
            if digit in ("3", "4", "5", "6", "7"):
                fields["vram_type"] = ExtractedField(
                    f"GDDR{digit}{suffix}", "regex", 0.9,
                )

    # Остальные 5 обязательных полей (tdp_watts, needs_extra_power,
    # video_outputs, core_clock_mhz, memory_clock_mhz) в прайсовом
    # наименовании системно отсутствуют — оставляем NULL под 2.5Б.

    return fields
