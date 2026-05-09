"""Канонизация написания бренда из прайсов разных дистрибьюторов.

Прайсы Merlion / OCS / Treolan / Ресурс-Медиа / Netlab / Green Place
пишут один и тот же бренд по-разному: `HP` / `HP Inc.` / `HP INC`,
`Pantum` / `PANTUM`, `Konica Minolta` / `Konica-Minolta`, `Asus` / `ASUS`,
`AMD` / `Advanced Micro Devices`. Без нормализации это даёт визуальный
шум в UI справочника (`/admin/components`) и потенциальные физические
дубли SKU (одна и та же модель попадает дважды под разной капитализацией
бренда).

Канонизатор — единая функция `canonical_brand(raw)` + явный словарь
алиасов. Объединяет печатные бренды (HP/Pantum/Canon/...) из QT-репо
и ПК-бренды (ASUS/AMD/Intel/...) из C-PC2 — единый источник правды
для обоих доменов после слияния (Этап 4 из 9, 2026-05-08).

Применяется в orchestrator при создании скелета компонента
(`_create_skeleton.manufacturer`); адаптеры прайсов отдают `PriceRow.brand`
без нормализации, чтобы не терять оригинальное написание в логах
и для отладки.

Бренды, которых нет в словаре, проходят через `.title()` и логируются
как кандидаты на добавление в словарь.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# Канон → множество алиасов (всё в нижнем регистре, после схлопывания пробелов).
# Канон уже в правильном написании производителя — именно его кладём в БД.
_ALIASES: dict[str, frozenset[str]] = {
    # --- Печатная техника (перенесено из QT, Этап 4 слияния) ----------------
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
    # Samsung / Toshiba — общие для печати, дисков и ОЗУ; см. также
    # ПК-секцию ниже (но дубликатных ключей нет: они объединены здесь).
    "Toshiba": frozenset({"toshiba"}),
    "Samsung": frozenset({"samsung", "samsung electronics"}),
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

    # --- ПК-компоненты (бренды, реально встречающиеся в прайсах ----------
    # Merlion / OCS / Treolan / Ресурс-Медиа / Netlab / Green Place).
    # Канон выбран по фактическому написанию из БД (`pc-brands-distinct.txt`):
    # большинство присутствует одновременно в верхнем и mixed-регистре,
    # канон — mixed-case производителя. Алиасы фиксируют все встретившиеся
    # вариации регистра, дефиса и пунктуации.

    # Спец-канон: brand='unknown' — fallback-плейсхолдер, который ставит
    # orchestrator при пустом бренде в прайсе. Не превращаем в 'Unknown'
    # через .title()-фоллбэк, чтобы не плодить шум в БД.
    "unknown": frozenset({"unknown"}),

    # CPU / SoC
    "Intel": frozenset({"intel", "intel corporation", "intel corp"}),
    "AMD": frozenset({
        "amd", "advanced micro devices", "amd ryzen", "amd radeon",
    }),
    "Supermicro": frozenset({"supermicro", "super micro", "super-micro"}),

    # Motherboards / общие платформенные бренды (часто пересекаются с GPU-AIB)
    "ASUS": frozenset({"asus", "asustek", "asus tek", "asustek computer inc"}),
    "MSI": frozenset({"msi", "micro-star", "micro star"}),
    "Gigabyte": frozenset({"gigabyte", "giga-byte", "gigabyte technology"}),
    "ASRock": frozenset({"asrock", "as rock", "as-rock"}),
    "Biostar": frozenset({
        "biostar", "biostar microtech",
        "biostar microtech netherlands b.v.",
    }),
    "AFOX": frozenset({"afox", "afox corporation"}),
    "MAXSUN": frozenset({"maxsun"}),
    # iRU уже определён выше в печатной части (один канон на оба домена).

    # GPU-AIB и графические бренды
    "NVIDIA": frozenset({"nvidia", "nvidia corp", "nvidia corporation"}),
    "ATI": frozenset({"ati"}),
    "Palit": frozenset({"palit"}),
    "PNY": frozenset({"pny"}),
    "ZOTAC": frozenset({"zotac"}),
    "INNO3D": frozenset({"inno3d", "inno 3d"}),
    "Sapphire": frozenset({"sapphire"}),
    "PowerColor": frozenset({"powercolor", "power color"}),
    "XFX": frozenset({"xfx"}),
    "Matrox": frozenset({"matrox"}),

    # RAM
    "Kingston": frozenset({"kingston"}),
    "Corsair": frozenset({"corsair"}),
    "Crucial": frozenset({"crucial"}),
    "G.Skill": frozenset({"g.skill", "gskill", "g-skill", "g skill"}),
    "Samsung": frozenset({"samsung", "samsung electronics"}),
    "Hynix": frozenset({"hynix", "sk hynix", "sk-hynix"}),
    "Patriot": frozenset({"patriot"}),
    "ADATA": frozenset({"adata", "a-data", "a data"}),
    "XPG": frozenset({"xpg"}),
    "Team Group": frozenset({"team group", "teamgroup", "team-group"}),
    "Goodram": frozenset({"goodram", "good ram", "good-ram"}),
    "Foxline": frozenset({"foxline", "fox line", "fox-line"}),
    "Netac": frozenset({"netac"}),
    "Apacer": frozenset({"apacer"}),
    "KingSpec": frozenset({"kingspec", "king spec", "king-spec"}),
    "Micron": frozenset({"micron", "micron technology"}),
    "DIGMA": frozenset({"digma"}),
    "ТМИ": frozenset({
        "тми",
        "ооо «телеком и микроэлектроник индастриз»",
        "ооо телеком и микроэлектроник индастриз",
    }),

    # Storage (Toshiba/Samsung — выше, общие для нескольких доменов)
    "Western Digital": frozenset({
        "western digital", "wd", "western-digital", "westerndigital",
    }),
    "Seagate": frozenset({"seagate"}),
    "SanDisk": frozenset({"sandisk", "san disk", "san-disk"}),
    "Silicon Power": frozenset({
        "silicon power", "siliconpower", "silicon-power",
    }),
    "Transcend": frozenset({"transcend"}),
    "AGI": frozenset({"agi"}),
    "Solidigm": frozenset({"solidigm"}),
    "Synology": frozenset({"synology"}),
    "Hikvision": frozenset({"hikvision", "hik vision", "hik-vision"}),
    "KIOXIA": frozenset({"kioxia", "kioxia europe gmbh.", "kioxia europe gmbh"}),
    "PC PET": frozenset({"pc pet", "pcpet", "pc-pet"}),
    "GS Nanotech": frozenset({"gs nanotech", "gs-nanotech", "gsnanotech"}),

    # Cases / Cooling / PSU — общие
    "Cooler Master": frozenset({
        "cooler master", "coolermaster", "cooler-master",
    }),
    "DeepCool": frozenset({"deepcool", "deep cool", "deep-cool"}),
    "Thermaltake": frozenset({"thermaltake", "thermal take", "thermal-take"}),
    "Thermalright": frozenset({"thermalright", "thermal right", "thermal-right"}),
    "Noctua": frozenset({"noctua"}),
    "Seasonic": frozenset({"seasonic", "sea sonic"}),
    "EVGA": frozenset({"evga"}),
    "Be Quiet!": frozenset({"be quiet!", "be quiet", "bequiet", "bequiet!"}),
    "Phanteks": frozenset({"phanteks"}),
    "NZXT": frozenset({"nzxt"}),
    "Lian Li": frozenset({"lian li", "lian-li", "lianli"}),
    "Fractal Design": frozenset({"fractal design", "fractal-design", "fractaldesign"}),
    "Zalman": frozenset({"zalman"}),
    "Aerocool": frozenset({"aerocool", "aero cool", "aero-cool"}),
    "Ocypus": frozenset({"ocypus"}),
    "JONSBO": frozenset({"jonsbo"}),
    "PCCooler": frozenset({"pccooler", "pc cooler", "pc-cooler"}),
    "ID-Cooling": frozenset({"id-cooling", "id cooling", "idcooling"}),
    "Arctic": frozenset({"arctic"}),
    "Scythe": frozenset({"scythe"}),
    "ExeGate": frozenset({"exegate"}),
    "Chieftec": frozenset({"chieftec"}),
    "FSP": frozenset({"fsp", "fsp group", "fspgroup"}),
    "1stPlayer": frozenset({"1stplayer", "1st player", "1st-player"}),
    "GameMax": frozenset({"gamemax", "game max", "game-max"}),
    "Montech": frozenset({"montech", "mon tech"}),
    "SAMA": frozenset({"sama"}),
    "AZZA": frozenset({"azza"}),
    "BLOODY": frozenset({"bloody"}),
    "POWERMAN": frozenset({"powerman", "power man"}),
    "Powercase": frozenset({"powercase", "power case"}),
    "InWin": frozenset({"inwin", "in win", "in-win"}),
    "Formula": frozenset({"formula"}),
    "Formula V": frozenset({"formula v"}),
    "Accord": frozenset({"accord"}),
    "HSPD": frozenset({"hspd"}),
    "ACD Systems": frozenset({"acd systems", "acdsystems", "acd-systems"}),
    "Raijintek": frozenset({"raijintek", "raijintek co ltd", "raijintek co. ltd"}),
    "Hiper": frozenset({"hiper"}),
    "AIC": frozenset({"aic"}),
    "RockPi": frozenset({"rockpi", "rock pi", "rock-pi"}),
    "KingPrice": frozenset({"kingprice", "king price", "king-price"}),
    "Chenbro": frozenset({"chenbro"}),
    "Geometric Future": frozenset({"geometric future", "geometric-future"}),
    "Linkworld": frozenset({"linkworld", "link world", "link-world"}),
    "Alseye": frozenset({
        "alseye", "alseye corporation limited", "alseye corp",
    }),
    "Ginzzu": frozenset({"ginzzu"}),
    "CBR": frozenset({"cbr"}),
    "Super Flower": frozenset({"super flower", "superflower", "super-flower"}),
    "SilverStone": frozenset({"silverstone", "silver stone", "silver-stone"}),
    "Crown": frozenset({"crown"}),
    "Foxconn": frozenset({"foxconn"}),
    "Cisco": frozenset({"cisco"}),
    "Ubiquiti": frozenset({"ubiquiti"}),
    "Lenovo": frozenset({"lenovo"}),
    "Gooxi": frozenset({
        "gooxi",
        "shenzhen guoxinhengyu technology (gooxi)",
        "shenzhen guoxinhengyu technology gooxi",
    }),
    "Raspberry Pi": frozenset({
        "raspberry pi", "raspberry pi foundation", "raspberrypi",
    }),

    # Не-бренд (юр-описание): «Производитель росреестровых твердотельных
    # дисков» — встречается в storages у двух SKU. Это поле manufacturer
    # заполнили длинным описанием, а не брендом. Канонизируем сами на себя,
    # чтобы canonical_brand не дёргал .title()-фоллбэк и не плодил
    # бессмысленные правки.
    "Производитель росреестровых твердотельных дисков": frozenset({
        "производитель росреестровых твердотельных дисков",
    }),
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
