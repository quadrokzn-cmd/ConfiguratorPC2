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


# ---------------------------------------------------------------------------
# Детектор адаптеров / зарядных устройств / PoE-инжекторов в категории psu
# (этап 11.6.2.5.0b).
# ---------------------------------------------------------------------------
# По итогам аудита 5.0a в bucket manufacturer='unknown' категории psu (232 шт)
# обнаружились ~70 не-PSU позиций: Gembird NPA-AC* / NPA-DC* (универсальные
# адаптеры и зарядки для ноутбуков), KS-is KS-* (универсальные адаптеры
# / зарядные PD USB-C), BURO BUM-* / BU-PA* (ноутбучные блоки питания
# и переходники Apple), ORIENT PU-C* / SAP-* / PA-* (DC-блоки), ББП Бастион
# РАПАН (батарейные блоки питания для охранных систем), Ubiquiti POE-*,
# FSP FSP040 (ноутбучный 40W), GOPOWER, WAVLINK и пр.
#
# Детектор работает на инверсии: сначала защитный слой ловит признаки
# «настоящего ATX/SFX-PSU» (форм-фактор, 80+, мощность ≥200W, серии CBR/
# Exegate UN/Ginzzu CB/PC, XPG KYBER, Zalman ZM, Aerocool Mirage/Cylon/KCAS,
# Powerman PM, 1STPLAYER NGDP, Thermaltake Smart и т. д.). Если защита
# сработала — возвращаем False, даже если в имени есть слово «адаптер».
# Логика: «80+ Bronze ATX 750W "Smart BX1"» с APFC не должно ложиться по
# слабому совпадению со словом «adapter» где-то в дополнительной строке.
# Только потом проверяются позитивные маркеры.

# Защитный слой 1 (форм-факторы и стандарт): ATX / ATX12V / ATX 3.0 / SFX /
# TFX / EPS / явный 80+ или 80 PLUS / слово «модульн». Lookahead на
# (?:\b|\d) нужен, чтобы поймать «ATX12V», «ATX3.0», «ATX 2.52» одинаково.
_PSU_REAL_FORM_FACTOR = re.compile(
    r"\bATX(?:\b|\d)|"
    r"\bSFX\b|\bTFX\b|\bEPS\b|"
    r"модульн|"
    r"\b80[\s\-]?(?:\+|PLUS)\b",
    flags=re.IGNORECASE,
)

# Защитный слой 2 (мощность ≥200W). Ловит «450W», «600 Вт», «850Вт».
# Не ловит код модели типа «CB450» (без буквы W/Вт) и «W700» (буква
# перед числом). Порог 200 безопасен: ноутбучные адаптеры в нашей БД
# идут до 150Вт включительно.
_PSU_REAL_WATTAGE = re.compile(
    r"(?<![A-Za-z])([2-9]\d{2,3})\s*(?:W\b|Вт\b|Watt\b)",
    flags=re.IGNORECASE,
)

# Защитный слой 3: серии гарантированно-настоящих PSU (whitelist).
# Если в имени совпала одна из этих серий — позиция считается PSU,
# даже если защитные слои 1-2 не сработали. Перечень основан на брендах,
# реально встретившихся в bucket unknown (id 731-747 CBR, id 921 Exegate
# UN450, id 1267-1277 Ginzzu CB/PC, id 1452-1463 XPG KYBER/CORE REACTOR,
# id 1066 1STPLAYER NGDP, id 1110/1480 Aerocool VX, id 1483 Aerocool VX
# через защиту 80+).
_PSU_REAL_SERIES = re.compile(
    r"\bCBR\s+ATX\b|"
    r"\bExe[Gg]ate\s+(?:UN|UNS|XP|AA|AAA|CP|PPE|PPX|NPX|NPXE|PPH|650PPH)|"
    r"\bGinzzu\s+(?:CB|PB|PC|MC|SA|SB)\d+|"
    r"\bXPG\s+(?:KYBER|CORE\s+REACTOR|PROBE|PYMCORE)|"
    r"\bZalman\s+ZM\d+|"
    r"\bAerocool\s+(?:Mirage|Cylon|KCAS|VX)|"
    r"\bPower[\s\-]?man\s+(?:PM|PMP)|"
    r"\b1\s*ST\s*PLAYER\s+NGDP|"
    r"\bThermaltake\s+(?:Smart|TR2|Toughpower)|"
    r"\bFormula\s+(?:VX|KCAS|V\s*Line)",
    flags=re.IGNORECASE,
)

# Позитив 1 (общие маркеры): прямые слова про адаптер/зарядку/POE/dock-
# станцию/power-bank, а также фраза «блок питания для ноутбука/нетбука»
# (бытовые ноутбучные зарядки часто называют «блок питания», поэтому
# одной только подстроки «блок питания» недостаточно — нужна привязка
# к ноутбуку/нетбуку/Apple/Lenovo и т. п.).
_PSU_ADAPTER_KEYWORDS = re.compile(
    r"\bадаптер\b|"
    r"\bпереходник\b|"
    r"\bзарядн\w+|"
    r"\bcharger\b|charging|"
    r"\bpower[\s\-]*delivery\b|\busb[\s\-]?pd\b|"
    r"\bpoe\b|injector|"
    r"powerbank|\bpower\s*bank\b|"
    r"dock[\s\-]?station|"
    r"блок\s+питания\s+для\s+ноутбук|"
    r"блок\s+питания\s+для\s+нетбук|"
    r"блок\s+питания\s+для\s+Apple|"
    r"для\s+Яндекс|"
    r"для\s+мониторов|"
    r"\bББП\b",
    flags=re.IGNORECASE,
)

# Позитив 2 (бренд-серии): Gembird NPA-AC/DC, KS-is (вся серия —
# универсальные адаптеры/зарядки), BURO BUM-* (ноутбучные БП) и
# BU-PA* (переходники Apple), ORIENT PU-C/USB-/SAP-/PA-, GOPOWER,
# WAVLINK, FSP FSP040 (ноутбучный 40W), Ubiquiti POE, Бастион РАПАН.
# Эти серии в нашей БД полностью адаптерные — совпадение бренд-серии
# означает «не настоящий PSU».
_PSU_ADAPTER_BRAND_SERIES = re.compile(
    r"\bGembird\s+NPA[-\s]?(?:AC|DC)\d+\b|"
    r"\bKS-is\b|"
    r"\bBURO\s+BUM[-\s]?\d+|"
    r"\bBuro\s+BU-PA\d+|"
    r"\bORIENT\s+(?:PU-C|USB-\d|SAP-|PA-\d)|"
    r"\bGOPOWER\b|"
    r"\bWAVLINK\b|"
    r"\bFSP\s*FSP\s*0\d{2}\b|"
    r"\bUbiquiti\s+POE\b|"
    r"Бастион\s+РАПАН",
    flags=re.IGNORECASE,
)


def is_likely_psu_adapter(
    name: str | None,
    manufacturer: str | None = None,
) -> bool:
    """Эвристика: похожа ли позиция на адаптер питания / зарядное
    устройство / PoE-инжектор / dock-станцию / ноутбучный блок питания,
    ошибочно классифицированную как PSU (этап 11.6.2.5.0b).

    Защита (любой → НЕ адаптер):
    1. форм-фактор PSU в имени (ATX/SFX/TFX/EPS, 80+, модульн);
    2. явная мощность ≥200W (\\d{3,4}\\s*W/Вт/Watt);
    3. серия настоящего PSU из whitelist (CBR ATX, Exegate UN/PPH/PPX,
       Ginzzu CB/PC, XPG KYBER, Zalman ZM, Aerocool Mirage/Cylon/KCAS,
       Powerman PM, 1STPLAYER NGDP, Thermaltake Smart, Formula VX/KCAS).

    Позитив (любой → адаптер):
    * общие слова: адаптер / переходник / зарядное / charger / POE /
      USB-PD / powerbank / dock-station / «блок питания для ноутбука»;
    * бренд-серии: Gembird NPA-AC/DC, KS-is, BURO BUM-*/BU-PA-*,
      ORIENT PU-C/USB-/SAP-/PA-, GOPOWER, WAVLINK, FSP FSP040,
      Ubiquiti POE, Бастион РАПАН.

    Защитное поведение: пустой name → False (нечего скрывать без
    позитивной находки).
    """
    if not name:
        return False
    full = name if not manufacturer else f"{name} {manufacturer}"

    if _PSU_REAL_FORM_FACTOR.search(full):
        return False
    if _PSU_REAL_WATTAGE.search(full):
        return False
    if _PSU_REAL_SERIES.search(full):
        return False

    if _PSU_ADAPTER_BRAND_SERIES.search(full):
        return True
    if _PSU_ADAPTER_KEYWORDS.search(full):
        return True

    return False


# ---------------------------------------------------------------------------
# Детектор «корпусов / кулеров / вентиляторов внутри psus» (этап 11.6.2.5.0c).
# ---------------------------------------------------------------------------
# По итогам аудита 5.0b в категории psus остались ~25 строк, у которых model
# начинается со слов «Корпус …», «Кулер …», «Вентилятор …», «MasterBox …»,
# «Устройство охлаждения …» — эти позиции попали в psus при первичной
# загрузке прайсов (в raw_name проскакивали слова «PCCOOLER» / «Cooler Master»
# / «PC Cooling Fan» / «PSU», и downstream-классификация ошиблась). Они не
# являются адаптерами, поэтому is_likely_psu_adapter их не ловит — нет ни
# слова «адаптер», ни бренд-серии адаптеров.
#
# Перед AI-обогащением эти строки нужно скрыть (is_hidden=TRUE), иначе AI
# тратит запросы на поиск «power_watts» у Cooler Master MasterBox NR200P
# (это корпус — у него такой колонки нет).

# Маркеры «нон-PSU» (корпус, кулер, вентилятор, водянка, радиатор, AIO,
# tower-форм-фактор корпуса, MasterBox/MasterAir-серии). Используется как
# позитивный сигнал в is_likely_non_psu_in_psus.
#
# Слово «корпус» намеренно ловим в любой падежной форме («корпус», «корпуса»,
# «к корпусам»), потому что PSU FSP/INWIN иногда подписаны как «… для корпуса
# Chenbro» — для них защитный слой по «Блок питания» / wattage всё равно
# срабатывает раньше.
_NON_PSU_KEYWORDS = re.compile(
    r"\bкорпус\w*|"
    r"\bкулер\w*|"
    r"\bвентилятор\w*|"
    r"\bводянк\w+|"
    r"\bрадиатор\w*|"
    r"\bchassis\b|"
    r"\b(?:mid|mini|full)[\s\-]?tower\b|"
    r"\bMasterBox\b|\bMasterAir\b|\bMasterLiquid\b|"
    r"\bAIO\b|"
    r"\bwater\s*cool\w*\b|"
    r"\bPC\s*Cooling\s*Fan\b|"
    r"\bbracket\b",
    flags=re.IGNORECASE,
)

# Маркер «эта позиция начинается со слова, обозначающего нон-PSU» — самый
# сильный сигнал. Если строка начинается с «Корпус» / «Кулер» / «Вентилятор»
# / «Устройство охлаждения» — никакая защита не должна спасти. Это ровно тот
# случай, когда apстрим-классификатор ошибся (psu вместо case/cooler), а в
# названии явно написана истинная категория.
_NON_PSU_LEADING = re.compile(
    r"^\s*(?:корпус\w*|"
    r"кулер\w*|"
    r"вентилятор\w*|"
    r"устройство\s+охлажд\w*|"
    r"chassis\b|"
    r"(?:mid|mini|full)[\s\-]?tower\b)",
    flags=re.IGNORECASE,
)

# Защитный слой: явный маркер «это блок питания» в любом месте имени.
# «Блок питания» как фраза — сильный позитив; «БП» одиночно ненадёжен
# (часто встречается «без БП» в имени корпуса), поэтому в защиту не
# включаем.
_REAL_PSU_KEYWORD = re.compile(
    r"\bБлок\s+питания\b|"
    r"\bPower\s+Supply\b|"
    r"\bPower\s+Unit\b",
    flags=re.IGNORECASE,
)


def is_likely_non_psu_in_psus(
    name: str | None,
    manufacturer: str | None = None,
) -> bool:
    """Эвристика: попала ли в категорию psus позиция, которая на самом
    деле является корпусом / кулером / вентилятором / радиатором (этап
    11.6.2.5.0c). Дополняет is_likely_psu_adapter — оба детектора
    используются вместе в reclassify_psu_misclassified.py (логически OR).

    Дизайн:

    1. ЖЁСТКИЙ маркер: имя начинается с «Корпус» / «Кулер» / «Вентилятор»
       / «Устройство охлажд» / «Chassis» / «Mid-tower» — сразу True,
       никакая защита по watts/series/Блок-питания не действует. Если в
       начале имени стоит истинная категория — это всегда нон-PSU. ATX
       или 600W в названии — это атрибут совместимости корпуса с PSU/
       платой, а не признак самого PSU.

    2. Позитивный маркер где-то в имени (но не в начале): «MasterBox»,
       «AIO», «PC Cooling Fan», «к корпусам», «вентилятора». Тогда
       проверяем защитные слои:
       - Если в имени где-то есть «Блок питания» / «Power Supply» —
         это PSU с упоминанием своих характеристик (например, FSP
         «Блок питания FSP… для корпуса Chenbro»). Возвращаем False.
       - Если есть PSU-серия из whitelist (_PSU_REAL_SERIES) — PSU.
       - Если есть явная мощность ≥200W (_PSU_REAL_WATTAGE) — PSU.
       Иначе — True.

    Защита по форм-фактору (ATX/SFX) НЕ применяется, потому что у
    корпусов это атрибут поддерживаемого форм-фактора платы, и она
    дала бы массу ложно-отрицательных (Корпус … ATX → выпустили).
    Защита по watts применяется, т.к. у корпусов мощность редко
    указывается в названии модели; CPU-кулеры с TDP 200-300W —
    единичные случаи и для них срабатывает _NON_PSU_LEADING (имя
    начинается с «Устройство охлажд» / «Кулер»).

    Пустые входы → False.
    """
    if not name:
        return False
    full = name if not manufacturer else f"{name} {manufacturer}"

    if _NON_PSU_LEADING.match(full):
        return True

    if not _NON_PSU_KEYWORDS.search(full):
        return False

    if _REAL_PSU_KEYWORD.search(full):
        return False
    if _PSU_REAL_SERIES.search(full):
        return False
    if _PSU_REAL_WATTAGE.search(full):
        return False

    return True


# ---------------------------------------------------------------------------
# Детектор «не накопителей» в категории storage (этап 11.6.2.6.0b).
# ---------------------------------------------------------------------------
# По итогам аудита 6.0a в категории storages обнаружились всего 2 явно
# мусорные позиции: id=782 Kingston SNA-BR2/35 и id=1133 Digma DGBRT2535 —
# обе это рамки-переходники 2.5"→3.5" для крепления SSD/HDD в отсек 3.5".
# Сами 1187 видимых storages на 99% — реальные накопители (Samsung 980,
# WD Blue, Kingston A2000, Crucial MX, ExeGate Next/NextPro+, Toshiba MQ,
# Netac N600S и т. д.).
#
# Детектор работает узким regex'ом по фактическому мусору + профилактически
# ловит card-reader / USB-hub (на будущее, для новых загрузок прайсов).
#
# Защитные слои выстроены так, чтобы позитивный триггер «крепления для
# SSD/HDD» не блокировался самим словом «SSD»/«HDD» (которое неизбежно
# присутствует в этой фразе). Поэтому защита по именованию использует
# только форм-факторные / технологические токены, которые НЕ встречаются
# в триггер-фразах: NVMe / M.2 / 2280 / mSATA / U.2.
#
# Дополнительно — защита по характеристикам строки: если у компонента уже
# есть осмысленные значения capacity_gb (≥32 ГБ) или storage_type, это
# гарантия, что normalizer/AI-обогащение его уже идентифицировали как
# реальный накопитель — детектор возвращает False, не глядя на имя.

# Маркеры мусора. Узко:
#   • явное «крепление для (твердотельного диска | HDD | SSD)»;
#   • переходник/адаптер/рамка/кронштейн «2.5» БЕЗ «GB» рядом, чтобы не
#     задеть «2.5 GB кэш» и т. п.;
#   • явная конверсия размеров 2.5" → 3.5";
#   • card-reader / кардридер / USB-hub / USB-концентратор.
_NON_STORAGE_KEYWORDS = re.compile(
    r"крепления?\s+для\s+(?:твердотельного\s+диска|HDD|SSD)|"
    r"(?:переходник|адаптер|рамк[ауи]|кронштейн)\s+(?:с\s+)?2[\.,]5"
    r"(?!\s*(?:GB|ГБ|Гб|G[\s\-]?B))|"
    r"2[\.,]5[\s\-]?(?:\"|''|дюйм)?\s*(?:->|→|на|to)\s*3[\.,]5|"
    r"\bcard[\s\-]?reader\b|"
    r"\bкардридер\b|"
    r"\bUSB[\s\-]?hub\b|"
    r"\bUSB[\s\-]?концентратор\b",
    flags=re.IGNORECASE,
)

# Защитный слой по имени: форм-факторные / технологические маркеры
# реального накопителя. Намеренно НЕ включаем «SSD»/«HDD» одиночными
# словами — они появляются в триггер-фразах «крепления для SSD/HDD» и
# заблокировали бы основной кейс id=782/1133. NVMe/M.2/2280/mSATA/U.2
# в триггер-фразах не встречаются, поэтому работают как чистая защита
# от ложных срабатываний на полноценных SSD вида «Kingston A2000 NVMe
# M.2 2280 1TB».
_REAL_STORAGE_NAME_MARKERS = re.compile(
    r"\bNVMe?\b|"
    r"\bM[\.\s]?2\b|"
    r"\b2280\b|"
    r"\bmSATA\b|"
    r"\bU[\.\s]?2\b",
    flags=re.IGNORECASE,
)


def is_likely_non_storage(
    name: str | None,
    manufacturer: str | None = None,
    *,
    capacity_gb: float | int | None = None,
    storage_type: str | None = None,
) -> bool:
    """Эвристика: попала ли в категорию storages позиция, которая на самом
    деле является аксессуаром (рамкой 2.5"→3.5", card-reader, USB-hub и
    т. п.). Этап 11.6.2.6.0b.

    Защита (любой → НЕ мусор):

    1. ``capacity_gb`` ≥ 32 — реальные накопители всегда ≥32 ГБ,
       аксессуары ёмкости не имеют.
    2. ``storage_type`` не пустой — у настоящего SSD/HDD это поле уже
       заполнено нормализатором / AI-обогащением.
    3. в имени где-то стоит «NVMe» / «M.2» / «2280» / «mSATA» / «U.2» —
       форм-фактор/технология идентифицируют позицию как накопитель.
       (Слова «SSD»/«HDD» не используем как защиту: они есть в самих
       триггер-фразах вида «крепления для SSD/HDD», и иначе детектор
       никогда бы не сработал на id=782/1133.)

    Позитив (любой → мусор):

    * «крепления для твердотельного диска / HDD / SSD»;
    * «переходник / адаптер / рамка / кронштейн 2.5» БЕЗ контекста «GB/ГБ»;
    * явная конверсия размера 2.5"→3.5" в имени;
    * card-reader / кардридер / USB-hub / USB-концентратор.

    Защитное поведение: пустое ``name`` → False (нечего скрывать без
    позитивной находки).
    """
    if not name:
        return False

    if capacity_gb is not None:
        try:
            if float(capacity_gb) >= 32:
                return False
        except (TypeError, ValueError):
            pass

    if storage_type is not None and str(storage_type).strip():
        return False

    full = name if not manufacturer else f"{name} {manufacturer}"

    if _REAL_STORAGE_NAME_MARKERS.search(full):
        return False

    return bool(_NON_STORAGE_KEYWORDS.search(full))
