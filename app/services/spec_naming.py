# Генерация автоназвания конфигурации (этап 6.2).
#
# Функция generate_auto_name превращает выбранный variant в строку
# вида:
#   «Системный блок / LGA1700 / Intel Core i5-12400F 2.5/4.4GHz / ...»
#
# Эта строка сохраняется в specification_items.auto_name в момент
# выбора и больше не меняется — это «снимок», даже если прайс
# поменялся и цены стали другими.
#
# Чистая функция, зависящая только от variant_dict (уже обогащённого
# raw_specs через enrich_variants_with_specs). БД не трогает.

from __future__ import annotations

import re
from typing import Any


# ---------------------------------------------------------------------
# Вспомогалки
# ---------------------------------------------------------------------

def _fmt_num(value: Any) -> str | None:
    """2.5 → '2.5', 2.0 → '2', None → None."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f == int(f):
        return str(int(f))
    return f"{f:.2f}".rstrip("0").rstrip(".")


_CPU_MARKERS = (
    "Intel Core Ultra",
    "Intel Core",
    "Intel Xeon",
    "Intel Pentium",
    "Intel Celeron",
    "AMD Ryzen",
    "AMD Athlon",
    "AMD EPYC",
)


def _short_cpu_model(raw: str | None) -> str | None:
    """Короткое имя процессора: «Intel Core i5-12400F» / «AMD Ryzen 5 7600».

    - срезает скобки с их содержимым (Alder Lake, 6C/12T, ...);
    - срезает хвостовой OEM/BOX/TRAY;
    - ищет маркер серии и отбрасывает всё до него;
    - PRO-маркер в названии Ryzen игнорируется в выводе, если он
      стоит между серией и номером (AMD Ryzen 5 PRO 5650G → AMD Ryzen 5 5650G)?
      НЕТ: по ТЗ «без скобок и OEM/BOX» — PRO оставляем, чтобы не
      исказить модель. Менеджер при желании перепишет руками.
    """
    if not raw:
        return None
    s = re.sub(r"\s*\(.*?\)\s*", " ", raw)
    s = re.sub(r"\s+(?:OEM|BOX|TRAY)\b\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip()

    # Ищем первое вхождение одного из маркеров. Сортируем по длине
    # от длинного к короткому, чтобы «Intel Core Ultra» матчился раньше
    # «Intel Core».
    lowered = s.lower()
    best_idx: int | None = None
    for marker in sorted(_CPU_MARKERS, key=len, reverse=True):
        idx = lowered.find(marker.lower())
        if idx != -1 and (best_idx is None or idx < best_idx):
            best_idx = idx
    if best_idx is not None:
        s = s[best_idx:].strip()
    else:
        # Маркера нет (редкий случай, OEM-бренд без Intel/AMD в имени) —
        # уберём хотя бы префикс «Процессор/ CPU …», чтобы он не попал
        # в автоназвание.
        s = _strip_category_prefix(s)
    return s or None


# Порядок важен: длинные маркеры (Radeon RX) раньше коротких (RX),
# чтобы из «Sapphire Radeon RX 7600» получить «Radeon RX 7600»,
# а не «RX 7600».
_GPU_MARKERS = ("Radeon RX", "Radeon", "GeForce RTX", "GeForce GTX", "RTX", "GTX", "Arc")

# Регулярка срезает префикс категории в имени компонента — такой
# префикс часто встречается в прайсах OCS: «Видеокарта/ GT710…»,
# «Процессор/ CPU…», «Материнская плата/ PRIME…». Срабатывает только
# если слэш встречается в начале строки (первые ~30 символов), иначе
# риск отрезать что-то осмысленное.
_CATEGORY_PREFIX_RE = re.compile(r"^[^/]{1,30}/\s*")


def _strip_category_prefix(s: str) -> str:
    """Убирает ведущий «Категория/» из имени компонента, если он есть."""
    return _CATEGORY_PREFIX_RE.sub("", s, count=1)


def _short_gpu_model(raw: str | None) -> str | None:
    """Короткое имя видеокарты: «RTX 4060», «Radeon RX 7600», «Arc A770».

    Если маркер найден — возвращается он + номер модели.
    Если нет — возвращается исходная строка с ОБРЕЗАННЫМ префиксом
    категории (например, «Видеокарта/ GT710-SL-2GD5-BRK-EVO» →
    «GT710-SL-2GD5-BRK-EVO»), чтобы в автоназвании не появлялся
    хвост из прайса.
    """
    if not raw:
        return None
    s = re.sub(r"\s*\(.*?\)\s*", " ", raw)
    s = re.sub(r"\s+", " ", s).strip()

    best: tuple[int, str] | None = None
    lowered = s.lower()
    for marker in _GPU_MARKERS:
        idx = lowered.find(marker.lower())
        if idx == -1:
            continue
        if best is None or idx < best[0]:
            best = (idx, marker)
    if best is None:
        # Маркер не найден: чистим префикс «Видеокарта/ …» и отдаём
        # оставшееся как есть.
        return _strip_category_prefix(s) or None
    idx, marker = best
    tail = s[idx + len(marker):].strip()
    # Берём следующее «слово» (буквенно-цифровое), например «4060», «A770», «7600 XT».
    m = re.match(r"([A-Za-z0-9\-]+(?:\s+(?:XT|XTX|Ti|SUPER|LE))?)", tail)
    model_part = m.group(1).strip() if m else None

    # «GeForce RTX 4060» и «GeForce GTX 1660» свернём до «RTX 4060»/«GTX 1660»,
    # это ближе к бытовому названию.
    if marker.startswith("GeForce "):
        marker = marker.removeprefix("GeForce ")

    if model_part:
        return f"{marker} {model_part}"
    return marker


# ---------------------------------------------------------------------
# Блоки автоназвания
# ---------------------------------------------------------------------

def _block_socket(cpu_raw: dict, mb_raw: dict) -> str | None:
    """Socket — из CPU, если есть; иначе из материнки."""
    sock = cpu_raw.get("socket") or mb_raw.get("socket")
    if isinstance(sock, str):
        sock = sock.strip()
    return sock or None


def _block_cpu(cpu_model: str | None, cpu_raw: dict) -> str | None:
    """«Intel Core i5-12400F 2.5/4.4GHz» — модель + частоты."""
    model = _short_cpu_model(cpu_model)
    if not model:
        return None

    base = _fmt_num(cpu_raw.get("base_clock_ghz"))
    turbo = _fmt_num(cpu_raw.get("turbo_clock_ghz"))
    if base and turbo:
        freq = f"{base}/{turbo}GHz"
    elif base:
        freq = f"{base}GHz"
    elif turbo:
        freq = f"{turbo}GHz"
    else:
        freq = None

    return f"{model} {freq}" if freq else model


def _block_ram(ram_raw: dict) -> str | None:
    """«16GB DDR4» — суммарный объём × тип памяти."""
    size = ram_raw.get("module_size_gb")
    count = ram_raw.get("modules_count") or 1
    total_gb: int | None = None
    if size is not None:
        try:
            total_gb = int(float(size) * float(count))
        except (TypeError, ValueError):
            total_gb = None

    mtype = ram_raw.get("memory_type")
    if isinstance(mtype, str):
        mtype = mtype.strip() or None

    if total_gb and mtype:
        return f"{total_gb}GB {mtype}"
    if total_gb:
        return f"{total_gb}GB"
    return None


def _one_storage(raw: dict) -> str | None:
    cap = raw.get("capacity_gb")
    stype = raw.get("storage_type")
    if isinstance(stype, str):
        stype = stype.strip() or None

    cap_str: str | None = None
    if cap is not None:
        try:
            gb_val = int(float(cap))
        except (TypeError, ValueError):
            gb_val = None
        if gb_val is not None:
            if gb_val >= 1000 and gb_val % 1000 == 0:
                cap_str = f"{gb_val // 1000}TB"
            else:
                cap_str = f"{gb_val}GB"

    if cap_str and stype:
        return f"{cap_str} {stype}"
    if cap_str:
        return cap_str
    if stype:
        return stype
    return None


def _block_storage(storages: list[dict]) -> str | None:
    """Несколько накопителей склеиваются через « + »."""
    if not storages:
        return None
    parts: list[str] = []
    for st in storages:
        raw = st.get("raw_specs") or {}
        piece = _one_storage(raw)
        if piece:
            parts.append(piece)
    if not parts:
        return None
    return " + ".join(parts)


def _block_gpu(gpu_model: str | None) -> str | None:
    return _short_gpu_model(gpu_model)


def _block_case_ff(mb_raw: dict) -> str | None:
    """Форм-фактор сборки берём по материнке — она определяет
    физический размер системного блока (ATX/mATX/ITX)."""
    ff = mb_raw.get("form_factor")
    if isinstance(ff, str):
        ff = ff.strip()
    return ff or None


def _block_psu(psu_raw: dict) -> str | None:
    w = _fmt_num(psu_raw.get("power_watts"))
    return f"{w}W" if w else None


def _block_psu_or_builtin(
    psu_raw: dict | None, case_raw: dict,
) -> str | None:
    """БП-блок в автоназвании.

    Если PSU в сборке есть — «{power}W».
    Если PSU нет, но корпус со встроенным БП — «{included_psu_watts}W (встроен)».
    Если ни то, ни другое — None.
    """
    if psu_raw:
        block = _block_psu(psu_raw)
        if block:
            return block
    if case_raw and case_raw.get("has_psu_included"):
        w = _fmt_num(case_raw.get("included_psu_watts"))
        if w:
            return f"{w}W (встроен)"
        return "БП (встроен)"
    return None


# ---------------------------------------------------------------------
# Публичная функция
# ---------------------------------------------------------------------

_PREFIX = "Системный блок"


def generate_auto_name(variant: dict, *, fallback_id: int | None = None) -> str:
    """Строит автоназвание конфигурации из обогащённого варианта.

    variant ожидается уже после enrich_variants_with_specs — в каждом
    компоненте лежит raw_specs (dict с socket, power_watts, capacity_gb…).
    Несколько накопителей берутся из variant['storages_list'].

    Формат (правка после live-проверки 6.2):
      «Системный блок <CPU модель+частоты> / <socket> / <RAM> /
       <Storage> / <GPU?> / <case ff> / <PSU>W»

    Префикс «Системный блок» склеивается с CPU-блоком через пробел,
    остальное — через « / ». Если CPU-блока нет, но есть хоть
    что-то полезное — строка начинается с «Системный блок / …».
    Если блоков нет вообще — fallback «Конфигурация #<id> · <mfg>».
    """
    comps = variant.get("components") or {}

    def _raw(cat: str) -> dict:
        c = comps.get(cat) or {}
        return c.get("raw_specs") or {}

    def _model(cat: str) -> str | None:
        c = comps.get(cat)
        return c.get("model") if c else None

    cpu_raw = _raw("cpu")
    mb_raw = _raw("motherboard")
    ram_raw = _raw("ram")
    psu_raw = _raw("psu") if comps.get("psu") else None
    case_raw = _raw("case")
    storages = variant.get("storages_list") or []
    if not storages and comps.get("storage"):
        # Запасной путь, если storages_list не заполнен вызывающим кодом.
        storages = [comps["storage"]]

    cpu_block = _block_cpu(_model("cpu"), cpu_raw)

    # Остальные блоки — в том порядке, в котором они появляются в имени.
    tail_blocks: list[str | None] = [
        _block_socket(cpu_raw, mb_raw),
        _block_ram(ram_raw),
        _block_storage(storages),
        _block_gpu(_model("gpu")) if comps.get("gpu") else None,
        _block_case_ff(mb_raw),
        _block_psu_or_builtin(psu_raw, case_raw),
    ]
    tail = [b for b in tail_blocks if b]

    # Головной блок: «Системный блок Intel Core i5-12400F 2.5/4.4GHz»,
    # если CPU есть; иначе просто «Системный блок».
    if cpu_block:
        head = f"{_PREFIX} {cpu_block}"
    else:
        head = _PREFIX

    if cpu_block or tail:
        if tail:
            return head + " / " + " / ".join(tail)
        return head

    # Ничего нет — fallback, чтобы строка не была вырожденной.
    manuf = variant.get("manufacturer") or "—"
    if fallback_id is not None:
        return f"Конфигурация #{fallback_id} · {manuf}"
    return f"Конфигурация · {manuf}"
