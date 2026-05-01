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


# ---------------------------------------------------------------------------
# Детекторы мусора в категории case (этап 11.6.2.4.0).
# ---------------------------------------------------------------------------
# По итогам аудита локальной БД kvadro_tech (1876 видимых cases) реальный
# объём мусора в категории cases оказался меньше, чем в cooler. Большинство
# подозрительных raw_name (drive cage / dust filter / side panel / riser /
# tempered glass) при ближайшем рассмотрении — это ОПИСАНИЕ ПОЛНОЦЕННОГО
# КОРПУСА: серверные JBOD-шасси AIC, корпуса Lian Li с предустановленным
# riser-кабелем, корпуса Deepcool/JONSBO с tempered glass-панелью и т. д.
#
# Поэтому детекторы здесь работают на инверсии: маркер мусора ловится только
# если в имени НЕТ явных признаков корпуса (midi/full/mid-tower, ATX case,
# словосочетания «корпус компьютерный» и пр.). Это профилактика upstream:
# когда в новом прайсе появится отдельная корзина 3.5" / спорный райзер /
# одиночный 120-мм вентилятор — он сразу будет помечен is_hidden=True
# и не попадёт в выдачу подбора корпусов.
#
# Все 5 детекторов используют общий «защитный» regex _CASE_HOUSING_HINTS.
# Если он матчится — детектор возвращает False, даже при положительном
# триггере. Логика «когда в названии есть и tower, и riser — это всё ещё
# полноценный корпус с riser в комплекте, не аксессуар».

# Маркеры «это полноценный корпус» — общий защитный слой для всех детекторов
# случая case ниже. Когда они сработали, мусорный детектор обязан вернуть
# False: даже если в имени есть «riser cable» или «dust filter», пока
# где-то рядом стоит «midi tower» / «корпус ПК» / «ATX case» — это корпус.
_CASE_HOUSING_HINTS = re.compile(
    r"(\b(?:midi|mid|full|mini|micro|cube|small|big)[\s\-]?tower\b|"
    r"\bmid[\s\-]?tower\b|"
    r"\b(?:atx|matx|m-atx|mini[\s\-]?itx|itx|e[\s\-]?atx|"
    r"ssi[\s\-]?ceb|ssi[\s\-]?eeb)[\s\-]+(?:case|корпус|tower)\b|"
    r"\b(?:pc|computer|pc[\s\-]?case|computer\s*case|gaming\s*case)\b|"
    r"\bкорпус(?:\b|компьютерн|\s+ПК|\s+пк|\s+midi|\s+mid|\s+mini|\s+full|"
    r"\s+server|\s+серверн|\s+desktop|\s+rack)|"
    r"\bjbod\b|\b(?:server|tower)\s+chassis\b|"
    r"\brack[\s\-]?mount\b|\brackmount\b|"
    r"\bдля\s+пк\b|\bдля\s+компьютера\b|"
    r"\bmid\s+gaming\b|\bmod\s+gaming\b|\bgaming\s+atx\s+case\b|"
    r"\btempered\s*glass\s*edition\b|"
    r"\bsbc\s*case\b|\bsbc[\s\-]?корпус\b)",
    flags=re.IGNORECASE,
)


def _has_case_housing_hint(text_full: str) -> bool:
    """Внутренний хелпер: совпал ли с _CASE_HOUSING_HINTS.

    Вынесен отдельно, чтобы случайно не пересечь логику с CPU-маркерами
    из _CPU_COOLER_HINTS — они для cooler, а здесь чисто корпусная логика.
    """
    return bool(_CASE_HOUSING_HINTS.search(text_full))


# 1. Самостоятельный корпусной/120-мм вентилятор в категории case.
# Триггер: явные слова про вентилятор/кулер БЕЗ слов «корпус/case/tower».
# Реальный кейс из БД (id=1065): «Устройство охлаждения(кулер) Aerocool
# Core Plus, 120мм, Ret» — попал в cases, должен быть скрыт.
_LOOSE_CASE_FAN_KEYWORDS = re.compile(
    r"(устройство\s+охлажд|"
    r"^\s*кулер\b|^\s*вентилятор\b|"
    r"\bкорпусн\w*\s+(?:fan|вентилятор)\b|"
    r"\b(?:case|chassis|system)[\s\-]?(?:fan|вентилятор)\b|"
    r"\bvent[\s\-]?для\s+корпус)",
    flags=re.IGNORECASE,
)


def is_likely_loose_case_fan(
    name: str | None,
    manufacturer: str | None = None,
) -> bool:
    """Эвристика: похожа ли позиция в категории case на отдельный
    корпусной вентилятор / кулер, ошибочно классифицированный как корпус.

    Защита: если в имени есть маркеры корпуса (midi tower, ATX case,
    «корпус компьютерный» и т. п.) — НЕ помечаем. Корпус с
    предустановленным вентилятором — всё равно корпус.
    """
    if not name:
        return False
    full = name if not manufacturer else f"{name} {manufacturer}"
    if _has_case_housing_hint(full):
        return False
    if _LOOSE_CASE_FAN_KEYWORDS.search(full):
        return True
    # Серии типичных корпусных вентиляторов из cooler-детектора уже знают
    # эти серии. Используем их же — кейс «Aerocool Frost 12 / Pure Wings»
    # в категории cases ловится по той же серии.
    if _CASE_FAN_SERIES.search(full):
        return True
    if _CASE_FAN_MODELS.search(full):
        return True
    return False


# 2. Отдельная корзина / mobile rack / drive cage без корпуса.
# Профилактический детектор: реальных кейсов в БД cases НЕТ (все 5
# совпадений по «cage» оказались серверными JBOD-шасси). Но если
# поставщик пришлёт «корзина 5.25→4×3.5"», она обязана быть скрыта.
_DRIVE_CAGE_KEYWORDS = re.compile(
    r"(\bкорзин[аеуы]\s+(?:для\s+)?(?:hdd|ssd|жестк|3\.?5|2\.?5)|"
    r"\b(?:hdd|ssd|drive|disk)\s*cage\b|"
    r"\bmobile\s*rack\b|\bmobile-rack\b|"
    r"\bsalazk[аеи]\b|\bсалазк[аеи]\b|"
    r"\b5\.?25.+(?:to|→|->|\s+for\s+)\s*3\.?5|"
    r"\bhot[\s\-]?swap\s+(?:cage|backplane|tray|adapter)\b|"
    r"\bhdd\s+enclosure\b|\bssd\s+enclosure\b)",
    flags=re.IGNORECASE,
)


def is_likely_drive_cage(
    name: str | None,
    manufacturer: str | None = None,
) -> bool:
    """Эвристика: похожа ли позиция на отдельную корзину/mobile rack/
    drive cage, попавшую в категорию case ошибочно.

    Защита (важно): серверные JBOD-шасси и rack-mount корпуса у поставщиков
    в названии тоже содержат «hot-swap bay» и «cage» — но рядом всегда
    стоит «JBOD» / «chassis» / «rack-mount» / «1U/2U/4U». Детектор
    срабатывает только если ОТСУТСТВУЕТ маркер корпуса.
    """
    if not name:
        return False
    full = name if not manufacturer else f"{name} {manufacturer}"
    if _has_case_housing_hint(full):
        return False
    return bool(_DRIVE_CAGE_KEYWORDS.search(full))


# 3. Отдельный PCIe riser cable / extender card.
# Профилактика: реальный кейс id=1709 (Lian Li SUP01X) — это полноценный
# корпус с riser в комплекте, _CASE_HOUSING_HINTS его защитит. Но
# отдельный райзер обязан хайдиться.
_PCIE_RISER_KEYWORDS = re.compile(
    r"(\b(?:pcie|pci-e|pci\s*express)\s*(?:riser|extender|extension)\b|"
    r"\briser\s+cable\b|\briser\s+card\b|\bvertical\s+gpu\s+mount\b|"
    r"\bвертикальн\w+\s+креплени\w+\s+(?:gpu|видеокарт)|"
    r"\bрайзер[\-\s]?(?:кабел|карта))",
    flags=re.IGNORECASE,
)


def is_likely_pcie_riser(
    name: str | None,
    manufacturer: str | None = None,
) -> bool:
    """Эвристика: похожа ли позиция на отдельный PCIe-райзер.

    Защита: корпус, в котором райзер идёт в комплекте, не помечается.
    """
    if not name:
        return False
    full = name if not manufacturer else f"{name} {manufacturer}"
    if _has_case_housing_hint(full):
        return False
    return bool(_PCIE_RISER_KEYWORDS.search(full))


# 4. Отдельная сменная боковая панель / стекло / пылевой фильтр.
# Большинство таких слов в текущей БД — описание корпуса с tempered
# glass-панелью. Срабатываем только если в имени явно сказано
# «replacement / spare / отдельная панель».
_CASE_PANEL_OR_FILTER_KEYWORDS = re.compile(
    r"(\b(?:replacement|spare|extra|optional)\s+(?:side\s+)?panel\b|"
    r"\b(?:replacement|spare)\s+tempered\s+glass\b|"
    r"\bсменн\w+\s+(?:боков\w+\s+)?панел[ьеи]\b|"
    r"\bотдельн\w+\s+боков\w+\s+панел[ьеи]\b|"
    r"\bзапасн\w+\s+панел[ьеи]\b|"
    r"\b(?:standalone|spare)\s+dust\s+filter\b|"
    r"\bотдельн\w+\s+пылев\w+\s+фильтр|"
    r"\b(?:standalone|spare)\s+slot\s+cover\b|"
    r"\bsmall\s+pci[\s\-]?slot\s+covers?\b)",
    flags=re.IGNORECASE,
)


def is_likely_case_panel_or_filter(
    name: str | None,
    manufacturer: str | None = None,
) -> bool:
    """Эвристика: похожа ли позиция на отдельную сменную боковую панель,
    стекло или пылевой фильтр (без корпуса).
    """
    if not name:
        return False
    full = name if not manufacturer else f"{name} {manufacturer}"
    if _has_case_housing_hint(full):
        return False
    return bool(_CASE_PANEL_OR_FILTER_KEYWORDS.search(full))


# 5. Отдельный антипровисной кронштейн для GPU.
# Профилактика: реальных кейсов в БД нет, но Cooler Master / Lian Li
# их выпускают как самостоятельные товары.
_GPU_SUPPORT_KEYWORDS = re.compile(
    r"(\bgpu\s+(?:support|holder|brace|sag\s+brace)\b|"
    r"\bgpu\s+support\s+bracket\b|"
    r"\b(?:graphics|video)\s+card\s+(?:holder|support|brace)\b|"
    r"\bvideo\s*card\s*holder\b|"
    r"\bsag[\s\-]?bracket\b|"
    r"\b(?:антипровис\w*|противопровис\w*)\s+(?:кроншт|стойк|опор|подпорк)|"
    r"\b(?:кроншт|подпорк|стойк)\w*\s+(?:для\s+)?(?:видеокарт|gpu))",
    flags=re.IGNORECASE,
)


def is_likely_gpu_support_bracket(
    name: str | None,
    manufacturer: str | None = None,
) -> bool:
    """Эвристика: похожа ли позиция на отдельный антипровисной
    кронштейн для видеокарты.
    """
    if not name:
        return False
    full = name if not manufacturer else f"{name} {manufacturer}"
    if _has_case_housing_hint(full):
        return False
    return bool(_GPU_SUPPORT_KEYWORDS.search(full))
