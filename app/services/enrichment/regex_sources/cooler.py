# Извлечение обязательных характеристик кулеров и СЖО.
#
# Обязательные поля таблицы coolers (001_init.sql):
#   supported_sockets TEXT[]  — массив сокетов, с которыми совместим кулер
#   max_tdp_watts     INT     — максимальная рассеиваемая мощность
#
# Формат в прайсе OCS (кулер процессора):
#   "CPU Cooler PCCooler RT500 TC ARGB BK (245W, 4-pin PWM, 152mm, Al/Cu,
#    5x6mm, ARGB, 1x120mm, 73.32CFM, 34.9dBA, 2200RPM,
#    S: 1851/1700/1200/115X, AM5/AM4, black)"
#
# Не все позиции таблицы — это кулеры процессора: в неё также попадают
# корпусные вентиляторы (ARCTIC F12) и серверные фан-киты (HPE). У них
# supported_sockets/max_tdp_watts отсутствуют — оставляем NULL.

import re

from app.services.enrichment.base import ExtractedField


# Блок сокетов: всё между "S:" и следующей ')'.
# Используем не-жадный захват, чтобы не выйти за пределы скобок характеристик.
_SOCKETS_BLOCK_RE = re.compile(r"S:\s*([^)]*)", re.IGNORECASE)

# Токены сокетов внутри блока. Порядок альтернатив важен (длинное раньше).
_SOCKET_TOKEN_RE = re.compile(
    r"LGA\s*\d{3,4}"            # LGA1851, LGA 1200
    r"|115[Xx]|20[Xx]{2}"       # 115X / 20XX — обобщённые Intel
    r"|\b\d{3,4}\b"             # голые числа 3-4 цифры (в блоке S: это всегда сокет)
    r"|AM\d\+?"                 # AM3 / AM3+ / AM4 / AM5
    r"|FM\d\+?"                 # FM1 / FM2 / FM2+
    r"|sTR\d|sTRX\d|TRX\d+|TR\d"  # Threadripper
)

# Fallback-поиск сокетов, если блок "S:" в строке отсутствует
# (серверные кулеры пишут "Socket Intel LGA1700", "AMD SP5",
# Deepcool пишет "Soc-1151/1200", Foxline — просто "LGA1700").
# Собираем только токены с явным маркером (LGA, SP, AM, FM, sTR, Soc-):
# голые числа без префикса не берём, чтобы не перепутать с размерами
# (120mm, 92mm) или оборотами.
_SOC_DASH_RE = re.compile(r"Soc-(\d{3,4}(?:/\d{3,4})*)", re.IGNORECASE)
_SOCKET_EXPLICIT_RE = re.compile(
    r"LGA\s*\d{3,4}"
    r"|\bSP\d\b"
    r"|\bAM\d\+?\b"
    r"|\bFM\d\+?\b"
    r"|\bsTR\d\b|\bsTRX\d\b|\bTRX\d+\b"
    r"|\bs?115[Xx]\b|\bs?20[Xx]{2}\b"
)

# Максимальное рассеяние тепла: первое число с W в скобках характеристик.
# Паттерн "<цифры>W" после '(' или запятой — характерен для начала блока.
_TDP_IN_PARENS_RE = re.compile(r"\(\s*(\d+)\s*W\b")
# Запасной вариант: первое "<цифры>W" вообще в строке.
_TDP_ANYWHERE_RE = re.compile(r"\b(\d+)\s*W\b")


def _normalize_socket(token: str) -> str:
    """Приводит токен к каноничному виду: для Intel-сокетов добавляет префикс LGA."""
    t = token.strip().upper().replace(" ", "")
    if t.startswith("LGA"):
        return t
    if t.isdigit() or t in ("115X", "20XX"):
        return "LGA" + t
    return t


def extract(model: str) -> dict[str, ExtractedField]:
    """Извлекает обязательные поля кулера из наименования."""
    if not model:
        return {}

    fields: dict[str, ExtractedField] = {}

    # --- supported_sockets ---
    # Шаг 1: основной блок "S: ..." внутри скобок характеристик
    m_block = _SOCKETS_BLOCK_RE.search(model)
    if m_block:
        raw_tokens = _SOCKET_TOKEN_RE.findall(m_block.group(1))
        normalized = []
        seen = set()
        for tok in raw_tokens:
            canonical = _normalize_socket(tok)
            if canonical and canonical not in seen:
                seen.add(canonical)
                normalized.append(canonical)
        if normalized:
            fields["supported_sockets"] = ExtractedField(normalized, "regex", 1.0)

    # Шаг 2 (fallback): явные упоминания сокетов по всему тексту
    if "supported_sockets" not in fields:
        raw_tokens: list[str] = []
        # "Soc-1151/1200" → развернуть список
        for m in _SOC_DASH_RE.finditer(model):
            raw_tokens.extend(m.group(1).split("/"))
        # Одиночные явные упоминания
        raw_tokens.extend(_SOCKET_EXPLICIT_RE.findall(model))

        normalized = []
        seen = set()
        for tok in raw_tokens:
            # У вариантов "s115X"/"s20XX" срезаем ведущее "s"
            t = tok.strip().upper().replace(" ", "")
            if t.startswith("S") and not t.startswith(("SP", "STR", "STRX")):
                t = t[1:]
            canonical = _normalize_socket(t)
            if canonical and canonical not in seen:
                seen.add(canonical)
                normalized.append(canonical)
        if normalized:
            fields["supported_sockets"] = ExtractedField(normalized, "regex", 0.9)

    # --- max_tdp_watts ---
    # Сначала строго «в скобках», чтобы не путаться с чем-то посторонним.
    m = _TDP_IN_PARENS_RE.search(model)
    if not m:
        m = _TDP_ANYWHERE_RE.search(model)
    if m:
        value = int(m.group(1))
        # Отсечка правдоподобности: CPU-кулеры и СЖО обычно 65–400 Вт.
        if 30 <= value <= 600:
            fields["max_tdp_watts"] = ExtractedField(value, "regex", 1.0)

    return fields
