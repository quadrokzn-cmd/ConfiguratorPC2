# Derive-хелперы для Claude Code AI-обогащения (этап 2.5Б).
#
# needs_extra_power для GPU: по TDP и признаку наличия разъёма питания.
#
# Правило (зафиксировано пользователем):
# - TDP >= 75W или есть разъём питания (6/8/12pin, 12V-2x6, 12VHPWR) → True
# - Явный бесшинный (no power connector / slot power only) при TDP < 75W → False

from __future__ import annotations

import re

_POWER_CONNECTOR_TOKENS = re.compile(
    r"(?:"
    r"\b(?:6|8)\s*[\-\+]?\s*pin\b"   # 6pin, 8pin, 8-pin, 6+8pin
    r"|\b12\s*V?\s*HPWR\b"            # 12VHPWR / 12V HPWR
    r"|\b12\s*V\s*[\-]?\s*2\s*x\s*6\b"  # 12V-2x6
    r"|\bpcie\s*(?:6|8)\s*pin\b"
    r"|\b(?:1|2)\s*[xX\*]\s*(?:6|8)\s*[\-\+]?pin\b"
    r")",
    re.IGNORECASE,
)


def has_power_connector_hint(text: str | None) -> bool:
    """True, если в тексте (спецификация/название) виден признак
    внешнего разъёма питания видеокарты."""
    if not text:
        return False
    return _POWER_CONNECTOR_TOKENS.search(text) is not None


def derive_needs_extra_power(
    tdp_watts: int | None,
    power_connector_text: str | None = None,
) -> bool | None:
    """Возвращает True/False по правилу, либо None если недостаточно данных.

    Args:
        tdp_watts: TDP видеокарты в ваттах (если известен).
        power_connector_text: свободный текст о разъёмах питания из
            спецификации производителя (может содержать токены вида
            "8-pin", "12VHPWR", "no power connector" и т.п.).
    """
    if tdp_watts is not None:
        if tdp_watts >= 75:
            return True
        # TDP < 75W — обычно питание по слоту PCIe, кроме редких исключений.
        if not has_power_connector_hint(power_connector_text):
            return False
        return True
    # TDP неизвестен, но есть явный признак разъёма → True.
    if has_power_connector_hint(power_connector_text):
        return True
    return None


_VIDEO_OUTPUT_TOKEN = re.compile(
    r"""
    (?P<count>\d+)?           # опциональный счётчик «3x»
    \s*[xX×\*]?\s*
    (?P<port>HDMI|DisplayPort|DP|DVI(?:-[DID])?|VGA|D-Sub|USB-?C)
    (?:\s*(?P<ver>\d+(?:\.\d+)?))?
    """,
    re.IGNORECASE | re.VERBOSE,
)

_PORT_NORMALIZE = {
    "HDMI": "HDMI",
    "DISPLAYPORT": "DP",
    "DP": "DP",
    "DVI": "DVI",
    "DVI-D": "DVI-D",
    "DVI-I": "DVI-I",
    "VGA": "VGA",
    "D-SUB": "VGA",
    "USBC": "USB-C",
    "USB-C": "USB-C",
}


def normalize_video_outputs(raw: str | None) -> str | None:
    """Нормализует список видеовыходов к формату "NxPort[vVER]+...".

    Принимает свободный текст вида "1 HDMI 2.1 + 3 DP 1.4",
    "HDMI*1, DP*3", "HDMI 2.1a, 3x DP 1.4a", и т. п.
    Возвращает строку вида "1xHDMI2.1+3xDP1.4" (или None если не распознал).

    Для единообразия: порты в порядке первого упоминания, версии без
    суффиксов ('a', 'b' отсекаются).
    """
    if not raw or not isinstance(raw, str):
        return None
    # Разрезаем по '+', ',', '/', ';', но сохраняем исходный порядок.
    chunks = re.split(r"[+,;/\n]+", raw)
    result: list[tuple[str, str]] = []
    acc: dict[str, int] = {}
    port_ver: dict[str, str] = {}

    for chunk in chunks:
        # Ищем паттерн count × port ver в каждом чанке.
        s = chunk.strip()
        if not s:
            continue
        m = re.match(
            r"^\s*(?P<count>\d+)\s*[xX×\*]\s*(?P<port>HDMI|DisplayPort|DP|DVI(?:-[DID])?|VGA|D-Sub|USB-?C)\s*(?P<ver>\d+(?:\.\d+)?(?:[a-z]+)?)?",
            s, re.IGNORECASE,
        )
        if not m:
            # Альтернативный порядок: "HDMI*1", "HDMI x 3", "HDMI 2.1 *1"
            m = re.match(
                r"^\s*(?P<port>HDMI|DisplayPort|DP|DVI(?:-[DID])?|VGA|D-Sub|USB-?C)\s*(?P<ver>\d+(?:\.\d+)?(?:[a-z]+)?)?\s*[xX×\*]\s*(?P<count>\d+)",
                s, re.IGNORECASE,
            )
        if not m:
            # Без count: просто "HDMI 2.1" или "DisplayPort 1.4"
            m = re.match(
                r"^\s*(?P<count>\d+)?\s*(?P<port>HDMI|DisplayPort|DP|DVI(?:-[DID])?|VGA|D-Sub|USB-?C)\s*(?P<ver>\d+(?:\.\d+)?(?:[a-z]+)?)?",
                s, re.IGNORECASE,
            )
        if not m:
            continue
        port_raw = m.group("port").upper().replace(" ", "")
        port = _PORT_NORMALIZE.get(port_raw, port_raw)
        count = int(m.group("count")) if m.group("count") else 1
        ver_raw = m.group("ver") or ""
        # Отбрасываем суффиксы 'a', 'b' и прочие буквы у версий (2.1a → 2.1).
        ver = re.sub(r"[a-z]+$", "", ver_raw, flags=re.IGNORECASE)
        if port not in acc:
            acc[port] = 0
            port_ver[port] = ver
            result.append((port, ver))
        acc[port] += count
        if ver and not port_ver.get(port):
            port_ver[port] = ver

    if not acc:
        return None

    parts = []
    for port, _ver in result:
        v = port_ver.get(port) or ""
        parts.append(f"{acc[port]}x{port}{v}")
    return "+".join(parts)
