# View-слой для страницы /query/{id} и страницы проекта.
#
# Задачи модуля:
#   1) Обогатить view-модель вариантов короткой строкой характеристик
#      specs_short для каждого компонента. Строка собирается из полей
#      в таблицах cpus/motherboards/rams/gpus/storages/psus/cases/
#      coolers — тех, что появились на этапах 2.5А-В.
#   2) Сложить в каждый компонент dict raw_specs с сырыми полями
#      (socket, base_clock_ghz, capacity_gb и т. п.) — они нужны
#      generate_auto_name на этапе 6.2 для формирования автоназвания
#      конфигурации.
#
# Схему БД модуль не меняет — читает уже сохранённые компоненты
# по component_id. Функция принимает «плоский» список вариантов
# (их может быть много — из разных BuildResult одного проекта)
# и делает один SELECT на категорию.

from __future__ import annotations

from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.orm import Session


# -----------------------------------------------------------------------------
# Форматтеры строки характеристик по категориям
# -----------------------------------------------------------------------------
#
# Каждый форматтер принимает row (sqlalchemy Row или dict) и возвращает
# готовую строку specs_short либо None, если ни одно релевантное поле
# не заполнено.

def _fmt_num(value: Any) -> str | None:
    """Форматирует число: целые без .0, дробные с минимумом знаков."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f == int(f):
        return str(int(f))
    # Убираем хвостовые нули: 2.50 -> 2.5, 4.40 -> 4.4
    return f"{f:.2f}".rstrip("0").rstrip(".")


def _join_dot(parts: list[str | None]) -> str | None:
    """Склеивает сегменты через « · », пропуская None и пустые."""
    clean = [p for p in parts if p]
    if not clean:
        return None
    return " · ".join(clean)


def _fmt_cpu(row: Any) -> str | None:
    # <cores>C/<threads>T
    cores = _fmt_num(row.cores) if row.cores is not None else None
    threads = _fmt_num(row.threads) if row.threads is not None else None
    if cores and threads:
        ct = f"{cores}C/{threads}T"
    elif cores:
        ct = f"{cores}C"
    elif threads:
        ct = f"{threads}T"
    else:
        ct = None

    # <base>/<turbo>GHz
    base = _fmt_num(row.base_clock_ghz) if row.base_clock_ghz is not None else None
    turbo = _fmt_num(row.turbo_clock_ghz) if row.turbo_clock_ghz is not None else None
    if base and turbo:
        freq = f"{base}/{turbo}GHz"
    elif base:
        freq = f"{base}GHz"
    elif turbo:
        freq = f"{turbo}GHz"
    else:
        freq = None

    socket = row.socket or None
    return _join_dot([ct, freq, socket])


def _fmt_motherboard(row: Any) -> str | None:
    return _join_dot([
        row.socket or None,
        row.form_factor or None,
        row.memory_type or None,
    ])


def _fmt_ram(row: Any) -> str | None:
    size = _fmt_num(row.module_size_gb) if row.module_size_gb is not None else None
    count = _fmt_num(row.modules_count) if row.modules_count is not None else None
    if size and count:
        modules = f"{size}GB × {count}"
    elif size:
        modules = f"{size}GB"
    else:
        # без size показывать "× N" бессмысленно
        modules = None

    mtype = row.memory_type or None
    freq = _fmt_num(row.frequency_mhz) if row.frequency_mhz is not None else None
    if mtype and freq:
        mem = f"{mtype}-{freq}"
    elif mtype:
        mem = mtype
    elif freq:
        mem = f"{freq} МГц"
    else:
        mem = None

    return _join_dot([modules, mem])


def _fmt_gpu(row: Any) -> str | None:
    vram_gb = _fmt_num(row.vram_gb) if row.vram_gb is not None else None
    vram_type = row.vram_type or None
    tdp = _fmt_num(row.tdp_watts) if row.tdp_watts is not None else None

    vram = None
    if vram_gb and vram_type:
        vram = f"{vram_gb}GB {vram_type}"
    elif vram_gb:
        vram = f"{vram_gb}GB"
    elif vram_type:
        vram = vram_type

    tdp_str = f"{tdp}W" if tdp else None
    return _join_dot([vram, tdp_str])


def _fmt_storage(row: Any) -> str | None:
    cap = _fmt_num(row.capacity_gb) if row.capacity_gb is not None else None
    # ТБ выглядит приятнее для больших объёмов
    if cap is not None:
        try:
            gb_val = int(cap)
            if gb_val >= 1000 and gb_val % 1000 == 0:
                cap_str: str | None = f"{gb_val // 1000}TB"
            else:
                cap_str = f"{gb_val}GB"
        except ValueError:
            cap_str = f"{cap}GB"
    else:
        cap_str = None

    return _join_dot([
        cap_str,
        row.storage_type or None,
        row.interface or None,
    ])


def _fmt_psu(row: Any) -> str | None:
    pw = _fmt_num(row.power_watts) if row.power_watts is not None else None
    if not pw:
        return None
    return f"{pw}W"


def _fmt_case(row: Any) -> str | None:
    ffs = row.supported_form_factors
    parts: list[str] = []
    if ffs:
        # supported_form_factors в БД — TEXT[]; psycopg2 отдаёт list[str].
        # На всякий случай страхуемся от строки.
        if isinstance(ffs, str):
            ffs = [p.strip() for p in ffs.strip("{}").split(",") if p.strip()]
        clean = [str(x).strip() for x in ffs if x]
        if clean:
            parts.append("/".join(clean))

    # Если у корпуса встроенный БП — показываем это в описании,
    # чтобы менеджер не спутал сборку со сценарием A (корпус + отдельный БП).
    if getattr(row, "has_psu_included", None):
        w = getattr(row, "included_psu_watts", None)
        if w is not None:
            parts.append(f"+ встроенный БП {int(w)}W")
        else:
            parts.append("+ встроенный БП")

    if not parts:
        return None
    return " · ".join(parts)


def _fmt_cooler(row: Any) -> str | None:
    tdp = _fmt_num(row.max_tdp_watts) if row.max_tdp_watts is not None else None
    if not tdp:
        return None
    return f"TDP до {tdp}W"


# -----------------------------------------------------------------------------
# Описание категорий: таблица + SELECT + форматтер
# -----------------------------------------------------------------------------

# Описание категории: SELECT, форматтер specs_short, список сырых полей,
# которые нужно положить в c["raw_specs"] (для generate_auto_name и пр.).
_CATEGORY_QUERIES: dict[str, tuple[str, Callable[[Any], str | None], tuple[str, ...]]] = {
    "cpu": (
        "SELECT id, socket, cores, threads, base_clock_ghz, turbo_clock_ghz "
        "FROM cpus WHERE id = ANY(:ids)",
        _fmt_cpu,
        ("socket", "cores", "threads", "base_clock_ghz", "turbo_clock_ghz"),
    ),
    "motherboard": (
        "SELECT id, socket, form_factor, memory_type "
        "FROM motherboards WHERE id = ANY(:ids)",
        _fmt_motherboard,
        ("socket", "form_factor", "memory_type"),
    ),
    "ram": (
        "SELECT id, memory_type, module_size_gb, modules_count, frequency_mhz "
        "FROM rams WHERE id = ANY(:ids)",
        _fmt_ram,
        ("memory_type", "module_size_gb", "modules_count", "frequency_mhz"),
    ),
    "gpu": (
        "SELECT id, vram_gb, vram_type, tdp_watts "
        "FROM gpus WHERE id = ANY(:ids)",
        _fmt_gpu,
        ("vram_gb", "vram_type", "tdp_watts"),
    ),
    "storage": (
        "SELECT id, capacity_gb, storage_type, interface "
        "FROM storages WHERE id = ANY(:ids)",
        _fmt_storage,
        ("capacity_gb", "storage_type", "interface"),
    ),
    "psu": (
        "SELECT id, power_watts FROM psus WHERE id = ANY(:ids)",
        _fmt_psu,
        ("power_watts",),
    ),
    "case": (
        "SELECT id, supported_form_factors, has_psu_included, included_psu_watts "
        "FROM cases WHERE id = ANY(:ids)",
        _fmt_case,
        ("supported_form_factors", "has_psu_included", "included_psu_watts"),
    ),
    "cooler": (
        "SELECT id, max_tdp_watts FROM coolers WHERE id = ANY(:ids)",
        _fmt_cooler,
        ("max_tdp_watts",),
    ),
}


# -----------------------------------------------------------------------------
# Публичная функция
# -----------------------------------------------------------------------------

def _row_to_raw(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """Выбирает из sqlalchemy Row указанные поля в обычный dict."""
    out: dict[str, Any] = {}
    for name in fields:
        try:
            val = getattr(row, name)
        except AttributeError:
            val = None
        out[name] = val
    return out


def enrich_variants_with_specs(
    variants: list[dict],
    session: Session,
) -> list[dict]:
    """Добавляет specs_short и raw_specs в каждый компонент варианта.

    Принимает плоский список вариантов (можно передать варианты
    из разных BuildResult — будет один SQL-проход на категорию).
    Структура варианта:
      - components: dict[cat → component-dict] (как в _prepare_variants).
      - опционально storages_list: list[component-dict] — все накопители
        варианта; если есть, их id тоже попадают в пакетный SELECT.

    Изменяет dict-ы компонентов по месту: добавляет
      c["specs_short"]  — человекочитаемая строка («6C/12T · 2.5/4.4GHz · LGA1700»);
      c["raw_specs"]    — сырые поля из БД для генерации автоназвания.

    Если подтянуть характеристики не удалось (id отсутствует или
    компонент удалён), specs_short=None и raw_specs={}.
    """
    if not variants:
        return variants

    # Собираем все компоненты, которые нужно обогатить. Один компонент
    # может встретиться и в components, и в storages_list — достаточно
    # обогатить его один раз (по ссылке).
    ids_by_cat: dict[str, set[int]] = {}
    components_to_fill: list[tuple[str, dict]] = []

    def _collect(cat: str, c: dict) -> None:
        cid = c.get("component_id")
        if cid is not None:
            ids_by_cat.setdefault(cat, set()).add(int(cid))
        components_to_fill.append((cat, c))

    for v in variants:
        comps = v.get("components") or {}
        for cat, c in comps.items():
            if isinstance(c, dict):
                _collect(cat, c)
        for c in v.get("storages_list") or []:
            if isinstance(c, dict):
                _collect("storage", c)

    # Пакетные SELECT-ы по категориям.
    specs_by_cat: dict[str, dict[int, str | None]] = {}
    raw_by_cat: dict[str, dict[int, dict]] = {}
    for cat, ids in ids_by_cat.items():
        query_info = _CATEGORY_QUERIES.get(cat)
        if query_info is None:
            continue
        sql, formatter, raw_fields = query_info
        rows = session.execute(text(sql), {"ids": list(ids)}).all()
        bucket_s: dict[int, str | None] = {}
        bucket_r: dict[int, dict] = {}
        for row in rows:
            rid = int(row.id)
            bucket_s[rid] = formatter(row)
            bucket_r[rid] = _row_to_raw(row, raw_fields)
        specs_by_cat[cat] = bucket_s
        raw_by_cat[cat] = bucket_r

    # Раскладываем обратно. Один и тот же dict может быть прописан
    # дважды (в components и в storages_list) — это ок, значения те же.
    for cat, c in components_to_fill:
        cid = c.get("component_id")
        if cid is None:
            c["specs_short"] = None
            c["raw_specs"] = {}
            continue
        c["specs_short"] = specs_by_cat.get(cat, {}).get(int(cid))
        c["raw_specs"] = raw_by_cat.get(cat, {}).get(int(cid), {})

    return variants
