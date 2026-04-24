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
# Deepcool/Thermalright пишут "Soc-AM5/AM4/1200/1700/1851" или "Soc-1151/1200".
# Захватываем всё, что идёт после "Soc-" и состоит из букв/цифр/плюсов/слэшей —
# последующий split по "/" даст нам каждый сокет. _normalize_socket потом
# поставит префикс LGA числам и «115X»/«20XX».
_SOC_DASH_RE = re.compile(r"Soc-([A-Za-z0-9+/]+)", re.IGNORECASE)
# Merlion-SKU часто пишет цепочку Intel-сокетов с общим префиксом LGA:
# "LGA1851/1700/1200/115X/AM5/AM4". Первый элемент имеет префикс LGA,
# последующие 3-4-цифровые идут без префикса, а AM/FM продолжают цепочку
# через слэш. Захватываем «LGA<цифры>» и всю дальнейшую цепочку до первого
# не-цифрового не-слэшного символа (или пробела).
_LGA_CHAIN_RE = re.compile(
    r"LGA(\d{3,4}(?:/(?:\d{3,4}[A-Z]?|AM\d\+?|FM\d\+?|sTR\d|sTRX\d))*)",
    re.IGNORECASE,
)
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

# Маркеры СЖО (AIO liquid cooling) для derived max_tdp_watts по размеру радиатора.
_AIO_MARKER_RE = re.compile(
    r"Система\s+водяного\s+охлаждения"
    r"|\bAIO\b"
    r"|Liquid\s+cooling"
    r"|\bpump\b"
    r"|\bСВО\b",
    re.IGNORECASE,
)

# Размер радиатора AIO: стандартные 120 / 140 / 240 / 280 / 360 / 420 мм.
# Слева требуем отсутствие цифры (через negative lookbehind), чтобы «LM420»
# ловился (перед «4» стоит буква «M» — не цифра), а «12420» — нет (перед
# «4» другая цифра, т.е. это часть большего числа). Справа используем
# \b — чтобы отделить «120» от «1200» (в «LGA1200» справа от «120» идёт «0»,
# это продолжение числа, \b там не сработает — и правильно).
_AIO_RAD_SIZE_RE = re.compile(r"(?<!\d)(120|140|240|280|360|420)\b")

# Справочник derived TDP по размеру радиатора (консервативные значения,
# реальные СЖО обычно рассчитаны на немного большую мощность).
_AIO_RAD_TDP = {
    120: 150,
    140: 180,
    240: 200,
    280: 250,
    360: 300,
    420: 400,
}


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
        # Цепочка Intel-сокетов с унаследованным префиксом LGA:
        # "LGA1851/1700/1200/115X/AM5/AM4"
        for m in _LGA_CHAIN_RE.finditer(model):
            parts = m.group(1).split("/")
            # Первый элемент — голая цифра (после "LGA"), остальные могут
            # быть как цифрами (наследуют LGA), так и AM/FM/sTR/sTRX.
            if parts:
                raw_tokens.append("LGA" + parts[0])
                for p in parts[1:]:
                    p_up = p.upper()
                    # Числа/115X/20XX → считаются продолжением LGA-цепочки.
                    if p_up.isdigit() or p_up.startswith(("AM", "FM", "STR")) \
                            or p_up in ("115X", "20XX"):
                        raw_tokens.append(p)
                    else:
                        # 3-4 цифры с суффиксом — например "115X" — тоже LGA.
                        raw_tokens.append(p)
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

    # Derived fallback для СЖО: если явного "<TDP>W" нет, но строка содержит
    # маркер AIO (русское «Система водяного охлаждения», «AIO», «Liquid»,
    # «pump», «СВО») и указан стандартный размер радиатора (120/140/240/280/
    # 360/420 мм), берём консервативное значение по справочнику _AIO_RAD_TDP.
    if "max_tdp_watts" not in fields and _AIO_MARKER_RE.search(model):
        m = _AIO_RAD_SIZE_RE.search(model)
        if m:
            size = int(m.group(1))
            tdp = _AIO_RAD_TDP.get(size)
            if tdp is not None:
                fields["max_tdp_watts"] = ExtractedField(tdp, "derived", 0.8)

    return fields
