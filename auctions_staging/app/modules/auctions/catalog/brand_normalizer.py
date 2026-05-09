"""Канонизация написания бренда из прайсов разных дистрибьюторов.

Прайсы Merlion / OCS / Treolan / Ресурс-Медиа (и будущие — ASBIS, SanDisk,
Марвел, А1Тис) пишут один и тот же бренд по-разному: `HP` / `HP Inc.` / `HP INC`,
`Pantum` / `PANTUM`, `Konica Minolta` / `Konica-Minolta` и т.д. Без нормализации
это даёт визуальный шум в UI справочника `/nomenclature` и потенциальные
физические дубли SKU (одна и та же модель попадает дважды под разной
капитализацией бренда).

Канонизатор — единая функция `canonical_brand(raw)` + явный словарь алиасов.
Применяется в адаптерах прайсов (на уровне `PriceRow.brand`) и одноразовым
скриптом `scripts/normalize_brands.py` для миграции уже залитых данных.

Бренды, которых нет в словаре, проходят через `.title()` и логируются как
кандидаты на добавление в словарь.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# Канон → множество алиасов (всё в нижнем регистре, после схлопывания пробелов).
# Канон уже в правильном написании производителя — именно его кладём в БД.
_ALIASES: dict[str, frozenset[str]] = {
    "HP": frozenset({
        "hp", "hp inc", "hp inc.", "hpinc", "hpinc.",
        "hewlett packard", "hewlett-packard", "h.p.", "h p",
    }),
    "HPE": frozenset({
        "hpe", "hewlett packard enterprise", "hewlett-packard enterprise",
    }),
    "Pantum": frozenset({"pantum"}),
    "Canon": frozenset({"canon"}),
    "Kyocera": frozenset({
        "kyocera", "kyocera mita", "kyocera-mita", "kyoceramita",
    }),
    "Konica Minolta": frozenset({
        "konica minolta", "konica-minolta", "konica_minolta", "konicaminolta",
    }),
    "Xerox": frozenset({"xerox"}),
    "Brother": frozenset({"brother"}),
    "Ricoh": frozenset({"ricoh"}),
    "Epson": frozenset({"epson"}),
    "Sharp": frozenset({"sharp"}),
    "Lexmark": frozenset({"lexmark"}),
    "OKI": frozenset({"oki", "oki data", "oki-data"}),
    "Toshiba": frozenset({"toshiba"}),
    "Samsung": frozenset({"samsung"}),
    "Sindoh": frozenset({"sindoh"}),
    "Katusha IT": frozenset({
        "katusha it", "katusha-it", "katusha_it", "katushait",
        "katusha", "катюша it", "катюша-it", "катюша",
    }),
    "G&G": frozenset({
        "g&g", "g & g", "g g", "gg", "g and g", "g-g",
    }),
    "iRU": frozenset({"iru", "i-ru", "i ru"}),
    "Cactus": frozenset({"cactus", "кактус"}),
    "Bulat": frozenset({"bulat", "булат"}),
}


# Обратный индекс: алиас (нижний регистр) → канон. Строим один раз при импорте.
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for _canon, _aliases in _ALIASES.items():
    # сам канон в нижнем регистре тоже считается алиасом самого себя
    _ALIAS_TO_CANONICAL[_canon.lower()] = _canon
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias] = _canon


_WS_RE = re.compile(r"\s+")


def _collapse(raw: str) -> str:
    """Trim + схлопнуть множественные пробелы (включая \xa0, табы) в один."""
    s = raw.replace("\xa0", " ").strip()
    return _WS_RE.sub(" ", s)


def canonical_brand(raw: str | None) -> str:
    """Привести написание бренда к каноничному.

    Пустая/None строка → `""` (вызывающий сам решает, превращать ли в NULL).
    Известный алиас → каноничное написание из словаря.
    Неизвестный бренд → `.title()` + INFO-лог (кандидат на добавление в словарь).
    """
    if raw is None:
        return ""
    cleaned = _collapse(str(raw))
    if not cleaned:
        return ""

    key = cleaned.lower()
    canon = _ALIAS_TO_CANONICAL.get(key)
    if canon is not None:
        return canon

    fallback = cleaned.title()
    logger.info(
        "brand_normalizer: unknown brand %r, kept as title-case %r", raw, fallback,
    )
    return fallback


__all__ = ["canonical_brand"]
