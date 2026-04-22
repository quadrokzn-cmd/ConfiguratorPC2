# Извлечение обязательных характеристик накопителя (HDD / SSD).
#
# Обязательные поля таблицы storages (см. 001_init.sql):
#   storage_type, form_factor, interface, capacity_gb.
#
# В прайсе OCS наименования разнообразны:
#   "HDD WD SATA3 1Tb Purple Video ..."
#   "ADATA SSD Ultimate SU630, 1920GB, 2.5\" 7mm, SATA3, 3D QLC, R/W 520/450MB/s, ..."
#   "SSD Netac PCIe 3.0 x4 1TB NT01NV3000Q-1T0-E4X M.2 2280"
#   "HPE 1.92TB SATA 6G Read Intensive SFF BC Multi Vendor"

import re

from app.services.enrichment.base import ExtractedField


# Тип накопителя: явные маркеры в строке.
_TYPE_SSD = re.compile(r"\b(SSD|Твердотельный)", re.IGNORECASE)
_TYPE_HDD = re.compile(r"\b(HDD|Жесткий диск|Жёсткий диск)", re.IGNORECASE)

# Форм-фактор.
# M.2 ловим либо явным "M.2", либо типовым размером "2280"/"22x80mm".
_FF_M2   = re.compile(r"\bM\.2\b|\b22x80mm\b|\b2280\b", re.IGNORECASE)
# 2.5" — встречается как '2.5"', '2.5""' (опечатка Seagate), '2.5\'\''
# (одинарные кавычки у AGI), а также опечатка 'SATA25"' без точки.
_FF_25 = re.compile(r"""2\.5\"+|2\.5\s*''|(?<=SATA)\s*25\"+|\bSFF\b""")
# 3.5" — или LFF.
_FF_35 = re.compile(r'3\.5\"+|\bLFF\b')

# Интерфейс: порядок важен — NVMe (самый специфичный) > SAS > SATA.
_IFACE_NVME = re.compile(r"\bNVMe\b", re.IGNORECASE)
_IFACE_SAS  = re.compile(r"\bSAS\b")
# SATA допускает версии: арабские (SATA2/SATA3) и римские (SATAII/SATAIII).
_IFACE_SATA = re.compile(r"\bSATA(?:I{1,3}|\d)?\b", re.IGNORECASE)
# Если есть PCIe и нет SATA — это NVMe (типично для M.2 без явного NVMe).
_IFACE_PCIE = re.compile(r"\bPCIe\b", re.IGNORECASE)

# Ёмкость. Ищем число + единица (TB/GB), с проверкой что дальше не "/s"
# или буква/цифра (иначе поймаем скорости интерфейса "6Gb/s", "MB/s").
# Регистр любой: встречается "1Tb", "4Tb", "14tb", "1920GB".
_CAPACITY_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(TB|GB)(?![/\w])",
    re.IGNORECASE,
)


def extract(model: str) -> dict[str, ExtractedField]:
    """Извлекает обязательные поля накопителя из наименования."""
    if not model:
        return {}

    fields: dict[str, ExtractedField] = {}

    # --- storage_type ---
    if _TYPE_SSD.search(model):
        fields["storage_type"] = ExtractedField("SSD", "regex", 1.0)
    elif _TYPE_HDD.search(model):
        fields["storage_type"] = ExtractedField("HDD", "regex", 1.0)

    # --- form_factor ---
    if _FF_M2.search(model):
        fields["form_factor"] = ExtractedField("M.2", "regex", 1.0)
    elif _FF_25.search(model):
        fields["form_factor"] = ExtractedField('2.5"', "regex", 1.0)
    elif _FF_35.search(model):
        fields["form_factor"] = ExtractedField('3.5"', "regex", 1.0)

    # --- interface ---
    if _IFACE_NVME.search(model):
        fields["interface"] = ExtractedField("NVMe", "regex", 1.0)
    elif _IFACE_SAS.search(model):
        fields["interface"] = ExtractedField("SAS", "regex", 1.0)
    elif _IFACE_SATA.search(model):
        fields["interface"] = ExtractedField("SATA", "regex", 1.0)
    elif _IFACE_PCIE.search(model):
        # M.2-диск без явного NVMe: если есть PCIe и ни SATA, ни SAS — NVMe.
        fields["interface"] = ExtractedField("NVMe", "regex", 0.9)

    # --- capacity_gb ---
    # Берём ПЕРВОЕ совпадение (обычно это общая ёмкость, которая в прайсе
    # идёт раньше деталей вроде DRAM-буфера или TBW).
    m = _CAPACITY_RE.search(model)
    if m:
        value = float(m.group(1))
        unit  = m.group(2).upper()
        # Маркетинговый стандарт дисков: 1 TB = 1000 GB
        gb = int(value * 1000) if unit == "TB" else int(value)
        if gb > 0:
            fields["capacity_gb"] = ExtractedField(gb, "regex", 1.0)

    # --- производные значения (derived) ---
    # 1) Десктопные HDD без явного формфактора: 3.5" по умолчанию.
    #    Исключаем мобильные диски (Mobile/Laptop/Scorpio) и типоразмер 1.8".
    if "form_factor" not in fields \
            and fields.get("storage_type") \
            and fields["storage_type"].value == "HDD" \
            and not re.search(r"\bMobile\b|\bLaptop\b|\bScorpio\b|1\.8\"", model, re.IGNORECASE):
        fields["form_factor"] = ExtractedField('3.5"', "derived", 0.9)

    # 2) SSD 2.5" без явного интерфейса: SATA (других 2.5"-SSD интерфейсов
    #    в потребительском/серверном сегменте практически нет).
    if "interface" not in fields \
            and fields.get("storage_type") \
            and fields["storage_type"].value == "SSD" \
            and fields.get("form_factor") \
            and fields["form_factor"].value == '2.5"':
        fields["interface"] = ExtractedField("SATA", "derived", 0.9)

    return fields
