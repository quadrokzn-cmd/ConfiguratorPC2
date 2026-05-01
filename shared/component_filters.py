# Системные фильтры классификации компонентов (этап 9Г.1).
#
# Используются загрузчиками прайсов, чтобы при создании скелета компонента
# заранее пометить «спорные» позиции is_hidden=True. Раньше такие позиции
# чистились разовыми скриптами (scripts/hide_case_fans.py,
# scripts/hide_external_netac_ssd.py), но при следующей загрузке свежего
# прайса они опять появлялись как видимые.
#
# Если правило ложит компоненты, которые быть скрытыми НЕ должны —
# расширять исключения в этом модуле, а не плодить новые ad-hoc-скрипты.
# См. docs/enrichment_techdebt.md, секции 9 и 2.

from __future__ import annotations

import re


# Признаки «корпусного» вентилятора в названии: явные слова про корпус
# и chassis/system-варианты, плюс типовые модельные шаблоны (AF120/SP140/PWM 120).
_CASE_FAN_KEYWORDS = re.compile(
    r"(корпусн[ыоая]|case[\s\-]?fan|chassis[\s\-]?fan|system[\s\-]?fan|"
    r"вентилятор\s+для\s+корпуса)",
    flags=re.IGNORECASE,
)

# Модельные паттерны корпусных вентиляторов на 80/92/120/140/200 мм
# без радиатора. AF/SP/PWM/ARGB/RGB/MF — типовые префиксы серий.
_CASE_FAN_MODELS = re.compile(
    r"\b(?:AF|SP|PWM|ARGB|RGB|MF)[\-\s]*(?:80|92|120|140|200)\b",
    flags=re.IGNORECASE,
)

# Серии, которые у вендоров продаются как корпусные вентиляторы
# (а CPU-кулеры у них идут под другими сериями — см. ниже исключения).
# Перечислены те, что встречаются в наших прайсах от Netlab / Ресурс Медиа /
# Green Place и аналогов. Если найдём ещё — расширяем здесь, а не плодим
# отдельные скрипты.
#
# Защита: каждая серия должна включать характерный токен типоразмера
# (12/14 / 120 / 140), чтобы не задеть однотипные имена CPU-кулеров.
# Например, ARCTIC Freezer (CPU) тоже начинается на «ARCTIC», но идёт
# без P/F/BioniX-префикса. Aerocool «Air Frost» — CPU-кулер, а корпусной
# Aerocool Frost — это «Frost 12/14».
_CASE_FAN_SERIES = re.compile(
    # ARCTIC P12 / P14 / F12 / F14 / BioniX (P120 / F140) — корпусные.
    # BioniX — серия корпусных целиком, ловим её безусловно (даже без
    # типоразмера сразу после, типоразмер обычно идёт через слово —
    # «ARCTIC BioniX P120 A-RGB»).
    r"\barctic\s+(?:bionix|p\s*\d{1,3}|f\s*\d{1,3})\b"
    # Thermalright TL-* (TL-C12, TL-D12, TL-K12, TL-X12) — корпусные.
    r"|\bthermalright\s+tl[\-\s][a-z]?\d{1,3}\b"
    # Aerocool Frost/Force/Motion/Eclipse/Astro/Duo/Saturn 12/14.
    # «Air Frost» / «Air Force» — это уже CPU-кулеры, поэтому требуем
    # размер 12 или 14 после ключевого слова.
    r"|\baerocool\s+(?:frost|force|motion|eclipse|astro|duo|saturn)\s*1[24]\b"
    # be quiet! Pure Wings / Silent Wings / Light Wings — корпусные
    # (Pure Rock / Dark Rock / Pure Loop — CPU, не цепляем).
    r"|\b(?:pure|silent|light)\s+wings\b"
    # Cooler Master MasterFan / Sickleflow — корпусные.
    # Hyper / MasterAir / MasterLiquid — CPU-кулеры, не цепляем.
    r"|\bmasterfan\b|\bsickleflow\b"
    # Noctua NF-A12 / NF-A14 / NF-S12 / NF-P12 / NF-F12 (часто с суффиксом
    # вроде «x25» или «PWM») — корпусные. NH-D15 / NH-U12 / NH-L9 — CPU,
    # не цепляем.
    r"|\bnf[\-\s][asfp]\d{1,3}(?!\d)"
    # PCCooler корпусные серии: F5R120 / EF120 / F3 T120 (формат XxXxX мм).
    # AIO-серии PCCooler (DS/DT/DA/DC/DE 240/360) идут под отдельным брендом
    # и не пересекаются с этими сериями.
    r"|\b(?:pccooler)\s+(?:f\d[a-z]?|ef|f\d\s*t)\d{2,3}\b",
    flags=re.IGNORECASE,
)

# Просто слово «вентилятор» / «fan» — слабый сигнал (отсекается, если есть
# CPU-маркеры).
_GENERIC_FAN = re.compile(r"вентилятор|\bfan\b", flags=re.IGNORECASE)

# Маркеры CPU-кулера: если они есть в имени, позицию НЕ помечаем как
# корпусную/мусор, даже если в имени есть слово «вентилятор», «термопаста»
# или «mount kit». Используется как защитный слой во всех is_likely_*
# детекторах ниже.
_CPU_COOLER_HINTS = re.compile(
    r"(процессор|cpu[\s\-]?cooler|башенн|tower|радиатор|heat[\s\-]?sink|"
    r"liquid|aio|жидкост|охлад\.\s*проц|water\s*cool|cpu\s*fan|процессорн)",
    flags=re.IGNORECASE,
)


def is_likely_case_fan(
    name: str | None,
    manufacturer: str | None = None,
    category_hint: str | None = None,
) -> bool:
    """Эвристика: похожа ли позиция на корпусный вентилятор.

    name           — наименование компонента из прайса.
    manufacturer   — бренд (используется как доп.строка для regex).
    category_hint  — наша категория, если уже известна (например, 'cooler').
                     Сейчас не влияет на результат, оставлен для расширения.

    Возвращает True, если name + manufacturer содержат явные признаки
    корпусного вентилятора и при этом нет маркеров CPU-кулера.
    Защитное поведение: при пустых/None входах возвращает False —
    скрытие должно требовать положительной находки, а не отсутствия данных.
    """
    if not name:
        return False

    full = name
    if manufacturer:
        full = f"{full} {manufacturer}"

    # Любой явный CPU-маркер блокирует пометку: даже у Noctua / Arctic
    # бывают вентиляторы 120/140 мм, которые поставляются как часть
    # CPU-кулера и не должны исчезать из конфигуратора.
    if _CPU_COOLER_HINTS.search(full):
        return False

    if _CASE_FAN_KEYWORDS.search(full):
        return True
    if _CASE_FAN_MODELS.search(full):
        return True
    if _CASE_FAN_SERIES.search(full):
        return True
    if _GENERIC_FAN.search(full):
        return True

    return False


# Маркеры термопасты / термопрокладки. Используется в is_likely_thermal_paste.
_THERMAL_PASTE_KEYWORDS = re.compile(
    r"(термопаст|термоинтерфейс|термопрокладк|тепло.*проклад|"
    r"thermal\s*paste|thermal\s*pad|thermal\s*compound|термогель)",
    flags=re.IGNORECASE,
)


def is_likely_thermal_paste(
    name: str | None,
    manufacturer: str | None = None,
) -> bool:
    """Эвристика: похожа ли позиция на термопасту / термопрокладку.

    Защитный слой совпадает с is_likely_case_fan: не помечаем, если в
    имени присутствует CPU-маркер (например, «термопаста для процессора»
    может быть фактически «комплект CPU + термопаста», то есть скелет
    CPU-кулера). Также не помечаем при пустом входе.
    """
    if not name:
        return False
    full = name if not manufacturer else f"{name} {manufacturer}"
    if _CPU_COOLER_HINTS.search(full):
        return False
    return bool(_THERMAL_PASTE_KEYWORDS.search(full))


# Маркеры кабеля / удлинителя / адаптера / переходника / панели подключения.
# В is_likely_cable_or_adapter защищены: процессорный маркер блокирует
# пометку (на случай «кулер с USB-подсветкой» и т. п.).
_CABLE_ADAPTER_KEYWORDS = re.compile(
    r"(\busb[\b\s\-/]|\bкабел[ьея]|\bcable\b|удлинител|extension|extender|"
    r"переходник|разветвител|splitter|"
    r"патч[\s\-]корд|patch[\s\-]?cord|"
    r"front\s*panel|панель\s+(?:для|с|подключен|управлен))",
    flags=re.IGNORECASE,
)


def is_likely_cable_or_adapter(
    name: str | None,
    manufacturer: str | None = None,
) -> bool:
    """Эвристика: похожа ли позиция на кабель / удлинитель / адаптер /
    переходник / панель подключения, ошибочно классифицированные как cooler.
    """
    if not name:
        return False
    full = name if not manufacturer else f"{name} {manufacturer}"
    if _CPU_COOLER_HINTS.search(full):
        return False
    # Радиатор / вентилятор как самостоятельные слова — тоже сигнал, что
    # перед нами часть кулера (а не аксессуар), даже если есть слово USB.
    if re.search(r"\bвентилятор|\bfan\b|\bрадиатор|heat[\s\-]?sink",
                 full, flags=re.IGNORECASE):
        return False
    return bool(_CABLE_ADAPTER_KEYWORDS.search(full))


# Маркеры монтажного комплекта / бэк-плейта / кронштейна. Защитный слой
# тот же — CPU-маркер блокирует. Артикулы Exegate BKT-* и явно «secure frame»
# попадают в эту категорию, но «mounting kit для AM5 secure frame» от
# DeepCool/Noctua пройдут защиту, потому что в их raw_name присутствует
# «cpu»/«процессор»/«cooler».
_MOUNTING_KIT_KEYWORDS = re.compile(
    r"(mount(?:ing)?\s*kit|"
    r"\bкреплени[ея](?!\s+(?:вентилятора|радиатора))|"
    r"\bbracket\b|"
    r"back[\s\-]?plate|backplate|бэкплейт|"
    r"secure\s*frame|"
    r"\bbkt[\s\-]?\d|"  # Exegate BKT-0126, BKT-0126L
    r"рамк[ауи]\s+(?:для\s+процессор|cpu)?)",
    flags=re.IGNORECASE,
)


def is_likely_mounting_kit(
    name: str | None,
    manufacturer: str | None = None,
) -> bool:
    """Эвристика: похожа ли позиция на монтажный комплект / бэк-плейт /
    кронштейн без самого кулера.
    """
    if not name:
        return False
    full = name if not manufacturer else f"{name} {manufacturer}"
    if _CPU_COOLER_HINTS.search(full):
        return False
    return bool(_MOUNTING_KIT_KEYWORDS.search(full))


def is_likely_external_storage(
    name: str | None,
    manufacturer: str | None = None,
) -> bool:
    """Заглушка под будущий фильтр внешних накопителей (USB-C SSD и т.п.).

    В этапе 9Г.1 закрыта только разовая чистка 4 Netac USB-C SSD скриптом
    scripts/hide_external_netac_ssd.py — этого хватает: схема storages
    для внешних накопителей и так не применима. Если внешних SSD будет
    появляться больше при следующих загрузках прайсов, реализовать здесь
    проверку (USB-C / external / portable) и подключить в orchestrator
    тем же способом, что и is_likely_case_fan для коулеров.
    """
    return False
