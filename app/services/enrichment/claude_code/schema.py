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
    # Корпуса — добавлено на Этапе 11.6.2.4.0 (2026-05-01, аудит cases).
    # Бренды реально присутствуют в локальной БД kvadro_tech (видимые
    # cases), но в существующем whitelist их нет — AI отказывался ходить
    # на эти домены. Перед AI-обогащением 11.6.2.4.1 расширяем список.
    "gamemax.com",        # GameMax — 15 позиций (Asgard, Vega, Diamond и др.)
    "raijintek.com",      # RAIJINTEK — 13 позиций (Ophion / Asterion / Thetis)
    "xpg.com",            # XPG (ADATA gaming) — INVADER X и др.
    "powerman-pc.ru",     # POWERMAN — 23 позиции, российский OEM-бренд
    "digma.ru",           # DIGMA — 5 позиций, российский бренд бюджетных корпусов
    "hiper.ru",           # HIPER — 3 позиции, российский OEM-бренд
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
    # БП — добавлено на Этапе 11.6.2.5.0c (2026-05-05, проверено WebFetch).
    # Перед AI-обогащением 11.6.2.5.1 закрываем основные пробелы по
    # топ-вендорам с NULL.power_watts (ExeGate 30, Crown 1, Formula V Line
    # 11+, GameMax 7, Super Flower — топ-PSU-производитель). thermaltake/
    # deepcool/aerocool/coolermaster/corsair/bequiet/evga/xpg/silverstonetek/
    # raijintek/lian-li/asus/msi/gigabyte уже присутствуют выше.
    "exegate.ru",         # ExeGate — официальный сайт, БП в каталоге
    "crown-micro.com",    # Crown Micro — серия CM-PS500/600/650/700/850
    "gamemaxpc.com",      # GameMax — официальный сайт (не gamemax.com, см. техдолг §14)
    "formulav-line.com",  # Formula V Line (Тайвань) — БП/корпуса/кулеры
    "super-flower.com.tw", # Super Flower — серия LEADEX (топ-OEM PSU)
    # Накопители (HDD/SSD) — добавлено на Этапе 2.5Б (2026-04-24, оркестратор)
    "kingston.com",       # Kingston: SSD и аксессуары
    "westerndigital.com", # WD: HDD/SSD
    "seagate.com",        # Seagate: HDD
    "netac.com",          # Netac: SSD N600S, N5M, Z9
    "apacer.com",         # Apacer: SSD
    # Накопители — добавлено на Этапе 11.6.2.6.0b (2026-05-05, проверено
    # WebFetch). Закрывают пробелы перед AI-обогащением 11.6.2.6.1: бренды
    # с большой долей видимых SSD/HDD в текущем каталоге, но без своих
    # доменов в whitelist (AI отказывался ходить на datasheet'ы).
    "crucial.com",         # Crucial (Micron): MX/BX/P3/T700 — SATA/NVMe SSD
    "samsung.com",         # Samsung: 980 PRO / 990 PRO / 870 EVO (semiconductor.samsung.com — поддомен ловится через subdomain match)
    "transcend-info.com",  # Transcend: SSD220S/MTE220S/MSA452T mSATA, Embedded SSDs
    "adata.com",           # A-DATA: SU650/SU750/SU800/Legend SSD-серии
    "solidigm.com",        # Solidigm (бывшая Intel SSD division): P41 Plus, D7, D5
    "silicon-power.com",   # Silicon Power: A55/A58/A60, PX10/PC60 портативные, UD/PA внутренние
    "patriotmemory.com",   # Patriot Memory: P210/P310/P400, Viper VP4300 NVMe
    "sandisk.com",         # SanDisk: Plus/Ultra/Extreme внутренние и портативные SSD
    "synology.com",        # Synology: SAT5210 / SNV3410 SSDs — собственная enterprise-серия
    "kioxia.com",          # KIOXIA (бывш. Toshiba Memory): EXCERIA G2 / EXCERIA PLUS G3 — современные NVMe SSD заменили линейку Toshiba
    # Накопители — добавлено на Этапе 11.6.2.7 (2026-05-05, проверено
    # WebFetch / WebSearch). Закрывают whitelist gaps по итогам прохода
    # 11.6.2.6.1b: для qumo.ru (12 honest-null), micron.com (2),
    # hikvision.com (1) AI-обогащение возвращало null+reason «домен не в
    # whitelist», хотя это легитимные оф. сайты вендоров.
    "qumo.ru",             # QUMO: каталог /catalog/ssd/ — Novation/Forsage/Compass потребительские SSD (российский бренд)
    "micron.com",          # Micron Technology: client/data-center/auto SSDs — родительский бренд Crucial, datasheet'ы только на головном сайте
    "hikvision.com",       # Hikvision: серии D210pro / T100 Portable / E1000 — собственные SSD под маркой Hikvision
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
