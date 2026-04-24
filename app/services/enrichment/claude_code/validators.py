# Валидация значений, возвращённых Claude Code, перед записью в БД.
#
# Каждая проверка возвращает либо нормализованное значение, либо ValidationError
# с конкретной причиной. Причины собираются в отчёт импорта.

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse

from app.services.enrichment.claude_code.schema import OFFICIAL_DOMAINS


class ValidationError(Exception):
    """Значение не прошло валидацию. message — короткий код причины."""


@dataclass(frozen=True)
class ValidatedField:
    """Результат валидации одного поля: значение и URL источника.

    value уже приведён к нужному Python-типу (int / bool / str / Decimal / list[str]).
    """
    value: Any
    source_url: str


# -----------------------------------------------------------------------------
# Парсеры базовых типов
# -----------------------------------------------------------------------------
def _as_int(value: Any, *, lo: int, hi: int) -> int:
    if isinstance(value, bool):
        raise ValidationError("wrong_type:bool_for_int")
    if isinstance(value, int):
        v = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValidationError("wrong_type:float_not_integer")
        v = int(value)
    elif isinstance(value, str):
        s = value.strip().replace(" ", "")
        try:
            v = int(s)
        except ValueError:
            raise ValidationError(f"wrong_type:not_int({value!r})")
    else:
        raise ValidationError(f"wrong_type:{type(value).__name__}")
    if not (lo <= v <= hi):
        raise ValidationError(f"out_of_range:{v}_not_in_[{lo},{hi}]")
    return v


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "yes", "y", "1", "да"}:
            return True
        if s in {"false", "no", "n", "0", "нет"}:
            return False
    raise ValidationError(f"wrong_type:bool({value!r})")


def _as_decimal(value: Any, *, lo: float, hi: float) -> Decimal:
    if isinstance(value, bool):
        raise ValidationError("wrong_type:bool_for_decimal")
    try:
        d = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"wrong_type:not_decimal({value!r})")
    if not (Decimal(str(lo)) <= d <= Decimal(str(hi))):
        raise ValidationError(f"out_of_range:{d}_not_in_[{lo},{hi}]")
    return d


def _as_str(value: Any, *, min_len: int, max_len: int) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"wrong_type:not_str({type(value).__name__})")
    s = value.strip()
    if not (min_len <= len(s) <= max_len):
        raise ValidationError(f"bad_length:{len(s)}_not_in_[{min_len},{max_len}]")
    return s


def _as_enum(value: Any, *, allowed: set[str], normalize_map: dict[str, str] | None = None) -> str:
    s = _as_str(value, min_len=1, max_len=50).upper()
    if normalize_map:
        s = normalize_map.get(s, s)
    if s not in allowed:
        raise ValidationError(f"bad_value:{s}_not_in_{sorted(allowed)}")
    return s


# -----------------------------------------------------------------------------
# Проверка URL источника: только HTTPS, домен из белого списка
# -----------------------------------------------------------------------------
def _validate_source_url(url: Any) -> str:
    if url is None or (isinstance(url, str) and not url.strip()):
        raise ValidationError("missing_url")
    if not isinstance(url, str):
        raise ValidationError(f"wrong_type:url_not_str({type(url).__name__})")
    s = url.strip()
    if len(s) > 500:
        raise ValidationError(f"url_too_long:{len(s)}")
    parsed = urlparse(s)
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValidationError(f"bad_scheme:{parsed.scheme!r}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValidationError("bad_url:no_host")
    # Поддомены любых доменов из белого списка тоже разрешены
    for dom in OFFICIAL_DOMAINS:
        if host == dom or host.endswith("." + dom):
            return s
    raise ValidationError(f"bad_domain:{host}")


# -----------------------------------------------------------------------------
# Валидаторы для целевых полей. Возвращают ValidatedField или бросают
# ValidationError. На вход — словарь {"value": ..., "source_url": ...} как
# в JSON от Claude Code.
# -----------------------------------------------------------------------------

# Допустимые формы памяти видеокарт.
_VRAM_TYPES = {"DDR3", "DDR4", "DDR5",
               "GDDR5", "GDDR5X", "GDDR6", "GDDR6X", "GDDR7",
               "HBM", "HBM2", "HBM2E", "HBM3"}

# Форм-факторы корпусов и плат.
_FORM_FACTORS = {"E-ATX", "ATX", "MATX", "ITX", "XL-ATX", "SSI-EEB", "SSI-CEB"}
_FORM_FACTOR_NORMALIZE = {
    "MICRO-ATX": "MATX",
    "MICROATX":  "MATX",
    "M-ATX":     "MATX",
    "MINI-ITX":  "ITX",
    "MINIITX":   "ITX",
    "EATX":      "E-ATX",
}

# Допустимые типы памяти материнских плат.
_MB_MEMORY_TYPES = {"DDR3", "DDR4", "DDR5", "DDR4+DDR5"}

# Тип упаковки CPU.
_CPU_PACKAGE_TYPES = {"OEM", "BOX"}

# storage: значения в БД хранятся с сохранением регистра/кавычек, поэтому
# обычный _as_enum (он делает .upper()) не подходит. Свой нормализатор ниже.


def _v_gpu_tdp_watts(payload):       return _as_int(payload, lo=10, hi=600)
def _v_gpu_needs_extra_power(payload): return _as_bool(payload)
def _v_gpu_video_outputs(payload):    return _as_str(payload, min_len=3, max_len=200)
def _v_gpu_core_clock_mhz(payload):   return _as_int(payload, lo=100, hi=4000)
def _v_gpu_memory_clock_mhz(payload): return _as_int(payload, lo=500, hi=40000)
def _v_gpu_vram_gb(payload):          return _as_int(payload, lo=1, hi=128)
def _v_gpu_vram_type(payload):        return _as_enum(payload, allowed=_VRAM_TYPES)

def _v_mb_memory_type(payload): return _as_enum(payload, allowed=_MB_MEMORY_TYPES)
def _v_mb_has_m2_slot(payload): return _as_bool(payload)

def _v_cooler_max_tdp_watts(payload): return _as_int(payload, lo=30, hi=500)

def _v_case_has_psu_included(payload): return _as_bool(payload)

def _v_case_supported_form_factors(value):
    if not isinstance(value, list) or not value:
        raise ValidationError(f"wrong_type:not_nonempty_list({type(value).__name__})")
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        ff = _as_enum(item, allowed=_FORM_FACTORS, normalize_map=_FORM_FACTOR_NORMALIZE)
        if ff not in seen:
            seen.add(ff)
            out.append(ff)
    return out

def _v_case_included_psu_watts(payload): return _as_int(payload, lo=100, hi=2000)

def _v_cpu_base_clock_ghz(payload):  return _as_decimal(payload, lo=0.5, hi=6.0)
def _v_cpu_turbo_clock_ghz(payload): return _as_decimal(payload, lo=0.5, hi=7.0)
def _v_cpu_package_type(payload):    return _as_enum(payload, allowed=_CPU_PACKAGE_TYPES)

def _v_psu_power_watts(payload): return _as_int(payload, lo=5, hi=3000)


# storage-валидаторы. Регистр и кавычки значений в БД нестандартные ("2.5\"",
# "M.2", "mSATA"), поэтому _as_enum не подходит: он делает .upper() и
# допускает только ASCII.
def _v_storage_type(value):
    if not isinstance(value, str):
        raise ValidationError(f"wrong_type:not_str({type(value).__name__})")
    s = value.strip().upper()
    mapping = {"SSD": "SSD", "HDD": "HDD",
               "NVME": "SSD", "SOLID STATE DRIVE": "SSD", "HARD DISK DRIVE": "HDD"}
    out = mapping.get(s)
    if out is None:
        raise ValidationError(f"bad_value:{s}_not_in_['HDD','SSD']")
    return out


def _v_storage_form_factor(value):
    if not isinstance(value, str):
        raise ValidationError(f"wrong_type:not_str({type(value).__name__})")
    s = value.strip()
    norm = s.replace("''", '"').replace("”", '"').replace("’", '"').replace("″", '"')
    norm = norm.replace(",", ".").upper()
    if norm in {"2.5\"", "2.5", "2.5''"}: return "2.5\""
    if norm in {"3.5\"", "3.5", "3.5''"}: return "3.5\""
    if norm in {"M.2", "M2"}:             return "M.2"
    if norm in {"MSATA"}:                 return "mSATA"
    raise ValidationError(f"bad_value:{s!r}_not_a_storage_ff")


def _v_storage_interface(value):
    if not isinstance(value, str):
        raise ValidationError(f"wrong_type:not_str({type(value).__name__})")
    s = value.strip().upper().replace("-", "").replace(" ", "")
    if s in {"NVME"}:                              return "NVMe"
    if s in {"SAS"}:                               return "SAS"
    if s.startswith("SATA") or s in {"PCIE", "PCI", "PCIEXPRESS"}:
        # PCIe-only интерфейс в прайсе встречается как M.2 NVMe — интерфейс NVMe.
        # Если это PCIe + NOT NVMe — всё равно NVMe (M.2 NVMe-SSD).
        if s.startswith("SATA"):
            return "SATA"
        return "NVMe"
    raise ValidationError(f"bad_value:{s!r}_not_a_storage_iface")


def _v_storage_capacity_gb(payload): return _as_int(payload, lo=1, hi=256000)


# Регистр валидаторов: (категория, имя поля) -> функция-валидатор значения.
_VALIDATORS: dict[tuple[str, str], callable] = {
    ("gpu", "tdp_watts"):              _v_gpu_tdp_watts,
    ("gpu", "needs_extra_power"):      _v_gpu_needs_extra_power,
    ("gpu", "video_outputs"):          _v_gpu_video_outputs,
    ("gpu", "core_clock_mhz"):         _v_gpu_core_clock_mhz,
    ("gpu", "memory_clock_mhz"):       _v_gpu_memory_clock_mhz,
    ("gpu", "vram_gb"):                _v_gpu_vram_gb,
    ("gpu", "vram_type"):              _v_gpu_vram_type,

    ("motherboard", "memory_type"):    _v_mb_memory_type,
    ("motherboard", "has_m2_slot"):    _v_mb_has_m2_slot,

    ("cooler", "max_tdp_watts"):       _v_cooler_max_tdp_watts,

    ("case", "has_psu_included"):       _v_case_has_psu_included,
    ("case", "supported_form_factors"): _v_case_supported_form_factors,
    ("case", "included_psu_watts"):     _v_case_included_psu_watts,

    ("cpu", "base_clock_ghz"):         _v_cpu_base_clock_ghz,
    ("cpu", "turbo_clock_ghz"):        _v_cpu_turbo_clock_ghz,
    ("cpu", "package_type"):           _v_cpu_package_type,

    ("psu", "power_watts"):            _v_psu_power_watts,

    ("storage", "storage_type"):       _v_storage_type,
    ("storage", "form_factor"):        _v_storage_form_factor,
    ("storage", "interface"):          _v_storage_interface,
    ("storage", "capacity_gb"):        _v_storage_capacity_gb,
}


def is_target_field(category: str, field_name: str) -> bool:
    """True, если поле описано в нашей схеме обогащения."""
    return (category, field_name) in _VALIDATORS


def validate_field(category: str, field_name: str, raw: Any) -> ValidatedField:
    """Валидирует одно поле из ответа Claude Code.

    raw — это объект из JSON: {"value": ..., "source_url": "..."} либо
    что-то иное (тогда ошибка структуры).
    """
    key = (category, field_name)
    if key not in _VALIDATORS:
        raise ValidationError(f"unknown_field:{category}.{field_name}")

    if not isinstance(raw, dict):
        raise ValidationError(f"bad_payload:{type(raw).__name__}")

    value = raw.get("value")
    if value is None:
        # null от Claude Code = «не нашёл», не ошибка, просто пропускаем
        raise ValidationError("null_value")

    url = _validate_source_url(raw.get("source_url"))
    parsed_value = _VALIDATORS[key](value)
    return ValidatedField(value=parsed_value, source_url=url)
