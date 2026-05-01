# Схема обогащения через Claude Code: целевые поля, типы, ограничения,
# белый список доменов источников, размеры батчей.
#
# Этап 2.5Б закрывает «длинный хвост» характеристик, которые регулярки
# не смогли достать из наименования прайса. Состав целевых полей по
# категориям зафиксирован пользователем; перечень полей со схемой
# валидации — здесь.

from __future__ import annotations

# -----------------------------------------------------------------------------
# Целевые поля по категориям. Только эти поля экспортируются и принимаются
# от Claude Code. Если в категории есть другие NULL-поля — они в этап 2.5Б
# не входят (например, included_psu_watts обрабатывается двухпроходно: см.
# CASE_PSU_WATTS_FIELD ниже).
# -----------------------------------------------------------------------------
TARGET_FIELDS: dict[str, list[str]] = {
    "gpu": [
        "tdp_watts",
        "needs_extra_power",
        "video_outputs",
        "core_clock_mhz",
        "memory_clock_mhz",
        "vram_gb",
        "vram_type",
    ],
    # motherboard: на этапе 11.6.2.1 добавлены socket/chipset (хвост 2-3
    # позиции, которые regex не сумел разобрать из нестандартных прайсов).
    "motherboard": [
        "memory_type",
        "has_m2_slot",
        "socket",
        "chipset",
    ],
    # cooler: на этапе 11.6.2.1 добавлен supported_sockets (~1.1k позиций).
    "cooler": [
        "max_tdp_watts",
        "supported_sockets",
    ],
    "case": [
        "has_psu_included",
        "supported_form_factors",
    ],
    "cpu": [
        "base_clock_ghz",
        "turbo_clock_ghz",
        "package_type",
    ],
    "psu": [
        "power_watts",
    ],
    # storage — добавлено на Этапе 2.5Б (2026-04-24, оркестратор).
    # 10 позиций с NULL в одном из полей, не закрывались regex.
    "storage": [
        "storage_type",
        "form_factor",
        "interface",
        "capacity_gb",
    ],
}

# Поле включённого БП у корпуса — обрабатывается ВО ВТОРОМ прогоне Case,
# только для тех корпусов, у которых уже известно has_psu_included = TRUE.
CASE_PSU_WATTS_FIELD = "included_psu_watts"

# Список категорий в порядке, удобном для CLI --all.
ALL_CATEGORIES: list[str] = [
    "cpu", "psu", "storage", "cooler", "case", "motherboard", "gpu",
]

# Размер батча по умолчанию для каждой категории (можно переопределить
# параметром --batch-size).
DEFAULT_BATCH_SIZES: dict[str, int] = {
    "gpu":         30,
    "motherboard": 30,
    "cooler":      30,
    "case":        30,
    "cpu":         20,
    "psu":         20,
    "storage":     20,
}

# Соответствие категории и таблицы компонентов (берём ровно как в base.py,
# чтобы не плодить параллельный источник истины).
from app.services.enrichment.base import CATEGORY_TO_TABLE  # noqa: E402

# -----------------------------------------------------------------------------
# Белый список доменов источников.
#
# В URL допускается только домен или его поддомен из этого набора. Любой
# другой домен (маркетплейс, агрегатор, форум) приводит к отклонению значения
# на этапе валидации.
# -----------------------------------------------------------------------------
OFFICIAL_DOMAINS: frozenset[str] = frozenset({
    # GPU: чипмейкеры
    "nvidia.com",
    "amd.com",
    "intel.com",
    # GPU: AIB-партнёры
    "asus.com",
    "msi.com",
    "gigabyte.com",
    "aorus.com",
    "asrock.com",
    "palit.com",
    "zotac.com",
    "pny.com",
    "biostar.com.tw",
    "matrox.com",
    "afox.eu",
    "afox.ru",
    # GPU: AIB-партнёры — добавлено на Этапе 2.5Б (2026-04-24, оркестратор)
    "sapphiretech.com",   # AMD AIB: Radeon
    "inno3d.com",         # NVIDIA AIB: Twin X2, iChill
    "maxsun.com",         # NVIDIA AIB (глобальный домен)
    "maxsun.com.cn",      # NVIDIA AIB (китайский домен, документация часто только там)
    # Добавлено на Этапе 2.5В (2026-04-24, проверено WebFetch):
    "afox-corp.com",      # AFOX International — головной сайт, активный каталог mb/GPU/SSD/PSU (whitelist afox.eu/afox.ru не содержит mining-плат AFHM65/AFB250)
    # Материнские платы (помимо AIB-вендоров выше)
    "supermicro.com",
    # Кулеры
    "thermalright.com",
    "arctic.de",
    "arctic.ac",
    "noctua.at",
    "corsair.com",
    "deepcool.com",
    "bequiet.com",
    "coolermaster.com",
    "alseye.com",
    # Кулеры — добавлено на Этапе 2.5Б (2026-04-24, оркестратор)
    "idcooling.com",      # ID-Cooling: серия SE/AF (SE-214-XT и др.)
    "pccooler.com.cn",    # PCCooler: БП KF550, YS1200
    # Кулеры — добавлено на Этапе 11.6.2.3.1 (2026-05-01, рассинхрон с
    # cooler.md и общим whitelist; AI отказывался ходить на эти домены,
    # хотя они приемлемы как официальные источники).
    "cooler-master.com",  # альтернативный домен Cooler Master
    "be-quiet.net",       # be quiet! — европейский домен
    "aerocool.com",       # Aerocool — англоязычный домен (есть кулеры/вентиляторы)
    "ekwb.com",           # EK Water Blocks — кастомные водоблоки/AIO
    "alphacool.com",      # Alphacool — кастомное жидкостное охлаждение
    "scythe-eu.com",      # Scythe — европейский домен
    "silverstonetek.com", # SilverStone — кулеры/корпуса/БП
    "evga.com",           # EVGA — БП и кулеры (CLC AIO)
    "endorfy.com",        # Endorfy (бывш. SilentiumPC) — кулеры/вентиляторы
    # Корпуса
    "jonsbo.com",
    "fractal-design.com",
    "lian-li.com",
    "nzxt.com",
    "phanteks.com",
    "thermaltake.com",
    "chenbro.com",
    "aerocool.io",
    "montechpc.com",
    "azza.com.tw",
    "aicipc.com",
    # Корпуса / серверные платформы — добавлено на Этапе 2.5Б (2026-04-24, оркестратор)
    "ocypus.com",         # Ocypus: корпусы Gamma, Iota + БП (крупнейший пробел: 72 позиции)
    "in-win.com",         # InWin: корпусы IW-RS436 и др.
    "hpe.com",            # HPE: фан-киты ProLiant Gen10/11 (enterprise-ветка, отделена от hp.com)
    # БП и PoE
    "seasonic.com",
    "zalman.com",
    "chieftec.com",
    "chieftec.eu",
    "ubnt.com",
    "ui.com",
    "ubiquiti.com",
    "cisco.com",
    # БП — добавлено на Этапе 2.5Б (2026-04-24, оркестратор)
    "fsp-group.com",      # FSP Group — корпоративный сайт
    "fsplifestyle.com",   # FSP Group — потребительская ветка
    # БП — добавлено на Этапе 2.5В (2026-04-24, проверено WebFetch):
    "gamerstorm.com",     # GamerStorm — активный суб-бренд Deepcool (серии PN-D/PN-M), для legacy-моделей вроде PN1000D datasheet только здесь
    # Накопители (HDD/SSD) — добавлено на Этапе 2.5Б (2026-04-24, оркестратор)
    "kingston.com",       # Kingston: SSD и аксессуары
    "westerndigital.com", # WD: HDD/SSD
    "seagate.com",        # Seagate: HDD
    "netac.com",          # Netac: SSD N600S, N5M, Z9
    "apacer.com",         # Apacer: SSD
    # SBC (одноплатные компьютеры и корпуса для них)
    "raspberrypi.com",
    "radxa.com",
    "orangepi.org",
    # Прочее
    "hp.com",
    "lenovo.com",
    # Российские сборщики / производители корпусов под собственной маркой
    "fox-line.ru",
    "formula-pc.ru",
    "accord-pc.ru",
    "kingprice.ru",
    "acd-group.com",
})

# Источник, под которым значения от Claude Code пишутся в
# component_field_sources.source.
SOURCE_NAME = "claude_code"

# Уверенность по умолчанию для значений от Claude Code. Ниже, чем у
# regex/derived (1.0), но выше, чем у предполагаемого AI-обогащения.
DEFAULT_CONFIDENCE = 0.90

# Подметка (component_field_sources.source_detail) для значений, полученных
# AI-обогащением через WebSearch/WebFetch в этапе 11.6.2.x. Отличает их от
# других возможных вариантов source='claude_code' (например, ручных правок,
# импортированных через тот же канал).
SOURCE_DETAIL_WEB_SEARCH = "from_web_search"
