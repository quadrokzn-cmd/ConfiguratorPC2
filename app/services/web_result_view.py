# View-слой для страницы /query/{id}.
#
# Задача модуля — обогатить view-модель вариантов (результат
# _prepare_variants в main_router) короткой строкой характеристик
# specs_short для каждого компонента. Строка собирается из
# структурированных полей в таблицах cpus/motherboards/rams/gpus/
# storages/psus/cases/coolers — тех, что появились на этапах 2.5А-В.
#
# Схему БД модуль не меняет. Логика подбора/совместимости/цен не
# затрагивается — мы только читаем уже сохранённые компоненты по id,
# чтобы показать их на странице.

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
    if not ffs:
        return None
    # supported_form_factors в БД — TEXT[]; psycopg2 отдаёт list[str].
    # На всякий случай страхуемся от строки.
    if isinstance(ffs, str):
        ffs = [p.strip() for p in ffs.strip("{}").split(",") if p.strip()]
    clean = [str(x).strip() for x in ffs if x]
    if not clean:
        return None
    return "/".join(clean)


def _fmt_cooler(row: Any) -> str | None:
    tdp = _fmt_num(row.max_tdp_watts) if row.max_tdp_watts is not None else None
    if not tdp:
        return None
    return f"TDP до {tdp}W"


# -----------------------------------------------------------------------------
# Описание категорий: таблица + SELECT + форматтер
# -----------------------------------------------------------------------------

_CATEGORY_QUERIES: dict[str, tuple[str, Callable[[Any], str | None]]] = {
    "cpu": (
        "SELECT id, socket, cores, threads, base_clock_ghz, turbo_clock_ghz "
        "FROM cpus WHERE id = ANY(:ids)",
        _fmt_cpu,
    ),
    "motherboard": (
        "SELECT id, socket, form_factor, memory_type "
        "FROM motherboards WHERE id = ANY(:ids)",
        _fmt_motherboard,
    ),
    "ram": (
        "SELECT id, memory_type, module_size_gb, modules_count, frequency_mhz "
        "FROM rams WHERE id = ANY(:ids)",
        _fmt_ram,
    ),
    "gpu": (
        "SELECT id, vram_gb, vram_type, tdp_watts "
        "FROM gpus WHERE id = ANY(:ids)",
        _fmt_gpu,
    ),
    "storage": (
        "SELECT id, capacity_gb, storage_type, interface "
        "FROM storages WHERE id = ANY(:ids)",
        _fmt_storage,
    ),
    "psu": (
        "SELECT id, power_watts FROM psus WHERE id = ANY(:ids)",
        _fmt_psu,
    ),
    "case": (
        "SELECT id, supported_form_factors FROM cases WHERE id = ANY(:ids)",
        _fmt_case,
    ),
    "cooler": (
        "SELECT id, max_tdp_watts FROM coolers WHERE id = ANY(:ids)",
        _fmt_cooler,
    ),
}


# -----------------------------------------------------------------------------
# Публичная функция
# -----------------------------------------------------------------------------

def enrich_variants_with_specs(
    variants: list[dict],
    session: Session,
) -> list[dict]:
    """Добавляет specs_short в каждый компонент каждого варианта.

    Принимает на вход результат main_router._prepare_variants: список
    вариантов, где components — dict по категориям со структурой из
    schema.result_to_dict. Изменяет dict-ы компонентов по месту и
    возвращает тот же список (удобно для совместимости с вызывающим
    кодом).

    specs_short добавляется всегда; если подтянуть характеристики
    не удалось (компонент удалён из БД или все поля NULL) — поле
    будет равно None и шаблон его не отрисует.
    """
    if not variants:
        return variants

    # Собираем id-ы по категориям: {category: {id, id, ...}}
    ids_by_cat: dict[str, set[int]] = {}
    for v in variants:
        comps = v.get("components") or {}
        for cat, c in comps.items():
            if not isinstance(c, dict):
                continue
            cid = c.get("component_id")
            if cid is None:
                continue
            ids_by_cat.setdefault(cat, set()).add(int(cid))

    # Для каждой категории — один SELECT, складываем специ в {id: specs_short}.
    specs_by_cat: dict[str, dict[int, str | None]] = {}
    for cat, ids in ids_by_cat.items():
        query_info = _CATEGORY_QUERIES.get(cat)
        if query_info is None:
            # Неизвестная категория — просто пропускаем (не ломаем страницу).
            continue
        sql, formatter = query_info
        rows = session.execute(text(sql), {"ids": list(ids)}).all()
        bucket: dict[int, str | None] = {}
        for row in rows:
            bucket[int(row.id)] = formatter(row)
        specs_by_cat[cat] = bucket

    # Раскладываем обратно по вариантам.
    for v in variants:
        comps = v.get("components") or {}
        for cat, c in comps.items():
            if not isinstance(c, dict):
                continue
            cid = c.get("component_id")
            if cid is None:
                c["specs_short"] = None
                continue
            bucket = specs_by_cat.get(cat, {})
            c["specs_short"] = bucket.get(int(cid))

    return variants
