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
# Важно: "GDDR6" / "GDDR6X" НЕ совпадают — у нас шаблон "\d+G[BDD]?\b",
# а "6X" после G даёт букву X, не граница слова.
_VRAM_GB_EXPLICIT_RE = re.compile(r"\b(\d+)\s*GB\b")
# Суффикс артикула Gigabyte/ASUS: "-8GD", "-2GD5" (GD + версия DDR), "-8GL"
_VRAM_GB_DASH_RE     = re.compile(r"-(\d+)\s*G[A-Z]\d?\b")
# ASUS префикс "O": "-O16G", "-O6G", "-O16GGAMING" (без пробела после G)
_VRAM_GB_O_PREFIX_RE = re.compile(r"-O(\d+)G(?=[A-Z]|\b)")
# Голое "16G GAMING"
_VRAM_GB_BARE_RE     = re.compile(r"\b(\d+)\s*G\b")

# Тип видеопамяти: берём первое вхождение GDDR[n][X]
_VRAM_TYPE_RE = re.compile(r"\bG?DDR\dX?\b")

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
        # Нормализация: GDDR без X префикса оставляем как есть.
        # Схема допускает GDDR6 / GDDR6X / GDDR7 и т.п. (см. комментарий).
        value = m.group(0).upper()
        if value.startswith("DDR"):
            # Обычный DDR — у старых GT/R7 GPU. Не GDDR, но допустимо.
            value = value
        fields["vram_type"] = ExtractedField(value, "regex", 1.0)

    # Остальные 5 обязательных полей (tdp_watts, needs_extra_power,
    # video_outputs, core_clock_mhz, memory_clock_mhz) в прайсовом
    # наименовании системно отсутствуют — оставляем NULL под 2.5Б.

    return fields
