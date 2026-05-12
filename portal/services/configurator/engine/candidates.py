# SQL-поиск компонентов-кандидатов для подбора конфигурации.
#
# Все функции работают в рамках переданной сессии SQLAlchemy и возвращают
# list[dict] — словари-строки с характеристиками и ценой в USD.
# Цена в USD считается в SQL: RUB делится на переданный курс USD/RUB.
#
# Имена таблиц подставляются только из белого списка
# portal.services.configurator.enrichment.base.ALLOWED_TABLES.

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text

from portal.services.configurator.engine.schema import (
    BuildRequest,
    FixedRef,
    StorageRequirements,
)


# Паттерны manufacturer для верхнеуровневого разделения Intel/AMD.
# В БД встречается 'Intel Corporation' и 'AMD', поэтому для Intel используем ILIKE.
_INTEL_PATTERN = "Intel%"
_AMD_VALUE = "AMD"


# 9А.2: фильтр скрытых компонентов. Применяем как обязательное условие во
# всех 8 _get_*-функциях; UI на /admin/components позволяет админу выставлять
# is_hidden=TRUE на сломанные/несовместимые карточки (Netac USB-C SSD и т.п.).
def _hidden_filter(alias: str) -> str:
    return f"{alias}.is_hidden = FALSE"


def _price_in_usd_sql(alias: str, param_name: str = "usd_rub") -> str:
    """SQL-выражение: минимальная цена компонента в USD среди поставщиков.

    alias — алиас таблицы supplier_prices (например, 'sp').
    """
    return (
        f"MIN(CASE WHEN {alias}.currency = 'USD' "
        f"THEN {alias}.price "
        f"ELSE {alias}.price / :{param_name} END)"
    )


def _stock_where(alias: str, allow_transit: bool) -> str:
    """SQL-условие наличия: только stock или stock|transit."""
    if allow_transit:
        return f"({alias}.stock_qty > 0 OR {alias}.transit_qty > 0)"
    return f"{alias}.stock_qty > 0"


# -----------------------------------------------------------------------------
# CPU
# -----------------------------------------------------------------------------

def get_cpu_candidates(
    session,
    *,
    req: BuildRequest,
    manufacturer: str,       # "Intel" | "AMD"
    only_with_igpu: bool,    # путь A требует iGPU
    usd_rub: float,
    allow_transit: bool,
) -> list[dict]:
    """Подбирает CPU-кандидатов под требования запроса.

    Возвращает список словарей, отсортированных по минимальной цене в USD.
    Фильтруем на уровне SQL: только те, у кого заполнены критичные поля
    (сокет, ядра, потоки, tdp, iGPU, memory_type, package_type).

    Если CPU зафиксирован в запросе — возвращаем ровно одну строку по id/sku
    (без проверки min_* — зафиксированный выбор пользователя важнее).
    """
    params: dict[str, Any] = {"usd_rub": usd_rub}
    conditions: list[str] = [_hidden_filter("c")]

    fixed = req.cpu.fixed
    if fixed and fixed.is_set():
        if fixed.id is not None:
            conditions.append("c.id = :fid")
            params["fid"] = fixed.id
        elif fixed.sku:
            conditions.append("c.sku = :fsku")
            params["fsku"] = fixed.sku
    else:
        # Обычный поиск — Intel/AMD, критичные поля NOT NULL, минимумы.
        if manufacturer == "Intel":
            conditions.append("c.manufacturer ILIKE :mfr")
            params["mfr"] = _INTEL_PATTERN
        elif manufacturer == "AMD":
            conditions.append("c.manufacturer = :mfr")
            params["mfr"] = _AMD_VALUE
        else:
            raise ValueError(f"Неизвестный производитель: {manufacturer!r}")

        conditions.append("c.socket IS NOT NULL")
        conditions.append("c.cores IS NOT NULL")
        conditions.append("c.threads IS NOT NULL")
        conditions.append("c.base_clock_ghz IS NOT NULL")
        conditions.append("c.tdp_watts IS NOT NULL")
        conditions.append("c.has_integrated_graphics IS NOT NULL")
        conditions.append("c.memory_type IS NOT NULL")
        conditions.append("c.package_type IS NOT NULL")

        if req.cpu.min_cores:
            conditions.append("c.cores >= :min_cores")
            params["min_cores"] = int(req.cpu.min_cores)
        if req.cpu.min_threads:
            conditions.append("c.threads >= :min_threads")
            params["min_threads"] = int(req.cpu.min_threads)
        if req.cpu.min_base_ghz:
            conditions.append("c.base_clock_ghz >= :min_base")
            params["min_base"] = float(req.cpu.min_base_ghz)
        if only_with_igpu:
            conditions.append("c.has_integrated_graphics = TRUE")

        # Если пользователь явно указал manufacturer в блоке CPU — применим и его.
        if req.cpu.manufacturer:
            m = req.cpu.manufacturer.strip()
            if m.lower() == "intel" and manufacturer != "Intel":
                return []
            if m.lower() == "amd" and manufacturer != "AMD":
                return []

    where_sql = " AND ".join(conditions)
    stock_sql = _stock_where("sp", allow_transit)
    price_usd_sql = _price_in_usd_sql("sp")

    query = text(
        f"""
        SELECT c.id, c.model, c.manufacturer, c.sku,
               c.socket, c.cores, c.threads, c.base_clock_ghz,
               c.turbo_clock_ghz, c.tdp_watts,
               c.has_integrated_graphics, c.memory_type, c.package_type,
               {price_usd_sql} AS price_usd_min
        FROM cpus c
        JOIN supplier_prices sp
          ON sp.category = 'cpu' AND sp.component_id = c.id
        WHERE {where_sql}
          AND {stock_sql}
        GROUP BY c.id
        ORDER BY price_usd_min ASC
        """
    )
    rows = session.execute(query, params).mappings().all()
    return [dict(r) for r in rows]


# -----------------------------------------------------------------------------
# Материнская плата
# -----------------------------------------------------------------------------

def get_cheapest_motherboard(
    session,
    *,
    cpu_socket: str,
    fixed: FixedRef | None,
    usd_rub: float,
    allow_transit: bool,
) -> dict | None:
    """Самая дешёвая материнская плата под сокет CPU.

    Если плата зафиксирована — возвращаем именно её (без проверки сокета:
    проверка совместимости сделает check_build). Иначе — минимальная по цене
    из подходящих по сокету, с NOT NULL на form_factor и memory_type.
    """
    params: dict[str, Any] = {"usd_rub": usd_rub}
    conditions: list[str] = [_hidden_filter("mb")]

    if fixed and fixed.is_set():
        if fixed.id is not None:
            conditions.append("mb.id = :fid")
            params["fid"] = fixed.id
        elif fixed.sku:
            conditions.append("mb.sku = :fsku")
            params["fsku"] = fixed.sku
    else:
        conditions.append("mb.socket = :sock")
        conditions.append("mb.form_factor IS NOT NULL")
        conditions.append("mb.memory_type IS NOT NULL")
        params["sock"] = cpu_socket

    stock_sql = _stock_where("sp", allow_transit)
    price_usd_sql = _price_in_usd_sql("sp")

    query = text(
        f"""
        SELECT mb.id, mb.model, mb.manufacturer, mb.sku,
               mb.socket, mb.form_factor, mb.memory_type,
               mb.memory_slots,
               {price_usd_sql} AS price_usd_min
        FROM motherboards mb
        JOIN supplier_prices sp
          ON sp.category = 'motherboard' AND sp.component_id = mb.id
        WHERE {' AND '.join(conditions)}
          AND {stock_sql}
        GROUP BY mb.id
        ORDER BY price_usd_min ASC
        LIMIT 1
        """
    )
    row = session.execute(query, params).mappings().first()
    return dict(row) if row else None


# -----------------------------------------------------------------------------
# RAM
# -----------------------------------------------------------------------------

def get_ram_candidates(
    session,
    *,
    memory_type: str,          # DDR4 / DDR5
    min_frequency_mhz: int | None,
    usd_rub: float,
    allow_transit: bool,
) -> list[dict]:
    """Все подходящие модули RAM для данного типа памяти, DIMM, не медленнее min_freq.

    Возвращает строки с ценой в USD; сортировка по цене.
    Дальше в builder.py перебираются комбинации N модулей.
    """
    params: dict[str, Any] = {
        "usd_rub": usd_rub,
        "mem_type": memory_type,
    }
    freq_cond = ""
    if min_frequency_mhz:
        freq_cond = " AND r.frequency_mhz >= :min_freq"
        params["min_freq"] = int(min_frequency_mhz)

    stock_sql = _stock_where("sp", allow_transit)
    price_usd_sql = _price_in_usd_sql("sp")

    query = text(
        f"""
        SELECT r.id, r.model, r.manufacturer, r.sku,
               r.memory_type, r.form_factor,
               r.module_size_gb, r.frequency_mhz,
               {price_usd_sql} AS price_usd_min
        FROM rams r
        JOIN supplier_prices sp
          ON sp.category = 'ram' AND sp.component_id = r.id
        WHERE {_hidden_filter("r")}
          AND r.memory_type = :mem_type
          AND r.form_factor = 'DIMM'
          AND r.module_size_gb IS NOT NULL
          AND r.frequency_mhz IS NOT NULL
          {freq_cond}
          AND {stock_sql}
        GROUP BY r.id
        ORDER BY price_usd_min ASC
        """
    )
    rows = session.execute(query, params).mappings().all()
    return [dict(r) for r in rows]


# -----------------------------------------------------------------------------
# GPU
# -----------------------------------------------------------------------------

def get_cheapest_gpu(
    session,
    *,
    min_vram_gb: int | None,
    fixed: FixedRef | None,
    usd_rub: float,
    allow_transit: bool,
) -> dict | None:
    """Самая дешёвая дискретная GPU по требованиям.

    Если GPU зафиксирована — возвращаем её. Иначе — минимальная по цене,
    с vram_gb >= min_vram_gb (если задано).
    """
    params: dict[str, Any] = {"usd_rub": usd_rub}
    conditions: list[str] = [_hidden_filter("g")]

    if fixed and fixed.is_set():
        if fixed.id is not None:
            conditions.append("g.id = :fid")
            params["fid"] = fixed.id
        elif fixed.sku:
            conditions.append("g.sku = :fsku")
            params["fsku"] = fixed.sku
    else:
        # Требуем заполненного vram_gb — это критичное поле для выбора.
        conditions.append("g.vram_gb IS NOT NULL")
        if min_vram_gb:
            conditions.append("g.vram_gb >= :min_vram")
            params["min_vram"] = int(min_vram_gb)

    stock_sql = _stock_where("sp", allow_transit)
    price_usd_sql = _price_in_usd_sql("sp")

    query = text(
        f"""
        SELECT g.id, g.model, g.manufacturer, g.sku,
               g.vram_gb, g.tdp_watts, g.needs_extra_power,
               g.length_mm,
               {price_usd_sql} AS price_usd_min
        FROM gpus g
        JOIN supplier_prices sp
          ON sp.category = 'gpu' AND sp.component_id = g.id
        WHERE {' AND '.join(conditions)}
          AND {stock_sql}
        GROUP BY g.id
        ORDER BY price_usd_min ASC
        LIMIT 1
        """
    )
    row = session.execute(query, params).mappings().first()
    return dict(row) if row else None


# -----------------------------------------------------------------------------
# Накопитель
# -----------------------------------------------------------------------------

def get_cheapest_storage(
    session,
    *,
    req: StorageRequirements,
    usd_rub: float,
    allow_transit: bool,
    exclude_ids: list[int] | None = None,
) -> dict | None:
    """Самый дешёвый накопитель по требованиям.

    exclude_ids — список id storage-компонентов, которые уже были выбраны
    в этой сборке; их нельзя выбрать повторно (multi-storage NLU,
    backlog #7). Если None или пустой — фильтр не применяется.
    """
    params: dict[str, Any] = {"usd_rub": usd_rub}
    conditions: list[str] = [
        _hidden_filter("s"),
        "s.storage_type IS NOT NULL",
        "s.capacity_gb IS NOT NULL",
    ]
    if req.min_gb:
        conditions.append("s.capacity_gb >= :min_gb")
        params["min_gb"] = int(req.min_gb)
    if req.preferred_type:
        conditions.append("s.storage_type = :st")
        params["st"] = req.preferred_type
    if exclude_ids:
        conditions.append("s.id <> ALL(:excl_ids)")
        params["excl_ids"] = [int(i) for i in exclude_ids]

    stock_sql = _stock_where("sp", allow_transit)
    price_usd_sql = _price_in_usd_sql("sp")

    query = text(
        f"""
        SELECT s.id, s.model, s.manufacturer, s.sku,
               s.storage_type, s.form_factor, s.interface, s.capacity_gb,
               {price_usd_sql} AS price_usd_min
        FROM storages s
        JOIN supplier_prices sp
          ON sp.category = 'storage' AND sp.component_id = s.id
        WHERE {' AND '.join(conditions)}
          AND {stock_sql}
        GROUP BY s.id
        ORDER BY price_usd_min ASC
        LIMIT 1
        """
    )
    row = session.execute(query, params).mappings().first()
    return dict(row) if row else None


# -----------------------------------------------------------------------------
# PSU
# -----------------------------------------------------------------------------

def get_cheapest_psu(
    session,
    *,
    fixed: FixedRef | None,
    usd_rub: float,
    allow_transit: bool,
    min_watts: int | None = None,
) -> dict | None:
    """Самый дешёвый БП из наличия.

    min_watts — минимально допустимая мощность (power_watts >= min_watts).
    Этап 7.1: в builder этот параметр передаётся всегда (= 400 по умолчанию).
    """
    params: dict[str, Any] = {"usd_rub": usd_rub}
    conditions: list[str] = [_hidden_filter("p")]

    if fixed and fixed.is_set():
        if fixed.id is not None:
            conditions.append("p.id = :fid")
            params["fid"] = fixed.id
        elif fixed.sku:
            conditions.append("p.sku = :fsku")
            params["fsku"] = fixed.sku
    else:
        conditions.append("p.power_watts IS NOT NULL")
        if min_watts is not None:
            conditions.append("p.power_watts >= :min_w")
            params["min_w"] = int(min_watts)

    stock_sql = _stock_where("sp", allow_transit)
    price_usd_sql = _price_in_usd_sql("sp")

    query = text(
        f"""
        SELECT p.id, p.model, p.manufacturer, p.sku,
               p.power_watts, p.form_factor,
               {price_usd_sql} AS price_usd_min
        FROM psus p
        JOIN supplier_prices sp
          ON sp.category = 'psu' AND sp.component_id = p.id
        WHERE {' AND '.join(conditions) if conditions else 'TRUE'}
          AND {stock_sql}
        GROUP BY p.id
        ORDER BY price_usd_min ASC
        LIMIT 1
        """
    )
    row = session.execute(query, params).mappings().first()
    return dict(row) if row else None


# -----------------------------------------------------------------------------
# Корпус
# -----------------------------------------------------------------------------

def get_cheapest_case(
    session,
    *,
    mb_form_factor: str,
    fixed: FixedRef | None,
    usd_rub: float,
    allow_transit: bool,
    scenario: str = "A",
    min_watts: int | None = None,
) -> dict | None:
    """Самый дешёвый корпус, поддерживающий форм-фактор MB.

    scenario:
      'A' — корпус БЕЗ встроенного БП (has_psu_included=false или NULL);
            отдельный БП подбирается в get_cheapest_psu.
      'B' — корпус СО встроенным БП (has_psu_included=true);
            included_psu_watts >= :min_watts (параметр обязателен для B).
    Если fixed — scenario игнорируется (как и раньше, приоритет у пользователя).
    """
    params: dict[str, Any] = {"usd_rub": usd_rub}
    conditions: list[str] = [_hidden_filter("cs")]

    if fixed and fixed.is_set():
        if fixed.id is not None:
            conditions.append("cs.id = :fid")
            params["fid"] = fixed.id
        elif fixed.sku:
            conditions.append("cs.sku = :fsku")
            params["fsku"] = fixed.sku
    else:
        conditions.append("cs.supported_form_factors IS NOT NULL")
        conditions.append(":ff = ANY(cs.supported_form_factors)")
        params["ff"] = mb_form_factor

        if scenario == "A":
            # Корпуса без встроенного БП: false ИЛИ NULL (страхуемся от
            # неполных записей-скелетов у Merlion/Treolan).
            conditions.append(
                "(cs.has_psu_included = FALSE OR cs.has_psu_included IS NULL)"
            )
        elif scenario == "B":
            # Корпуса со встроенным БП достаточной мощности.
            conditions.append("cs.has_psu_included = TRUE")
            conditions.append("cs.included_psu_watts IS NOT NULL")
            if min_watts is not None:
                conditions.append("cs.included_psu_watts >= :min_w")
                params["min_w"] = int(min_watts)
        else:
            raise ValueError(f"Неизвестный scenario: {scenario!r}")

    stock_sql = _stock_where("sp", allow_transit)
    price_usd_sql = _price_in_usd_sql("sp")

    query = text(
        f"""
        SELECT cs.id, cs.model, cs.manufacturer, cs.sku,
               cs.supported_form_factors, cs.max_gpu_length_mm,
               cs.has_psu_included, cs.included_psu_watts,
               {price_usd_sql} AS price_usd_min
        FROM cases cs
        JOIN supplier_prices sp
          ON sp.category = 'case' AND sp.component_id = cs.id
        WHERE {' AND '.join(conditions)}
          AND {stock_sql}
        GROUP BY cs.id
        ORDER BY price_usd_min ASC
        LIMIT 1
        """
    )
    row = session.execute(query, params).mappings().first()
    return dict(row) if row else None


# -----------------------------------------------------------------------------
# Кулер
# -----------------------------------------------------------------------------

def get_cheapest_cooler(
    session,
    *,
    cpu_socket: str,
    required_tdp: int,
    fixed: FixedRef | None,
    usd_rub: float,
    allow_transit: bool,
) -> dict | None:
    """Самый дешёвый кулер, поддерживающий сокет CPU с запасом по TDP.

    Кулеры без supported_sockets или без max_tdp_watts не рассматриваются —
    нельзя гарантировать «запас 30%» без данных.
    """
    params: dict[str, Any] = {"usd_rub": usd_rub}
    conditions: list[str] = [_hidden_filter("cl")]

    if fixed and fixed.is_set():
        if fixed.id is not None:
            conditions.append("cl.id = :fid")
            params["fid"] = fixed.id
        elif fixed.sku:
            conditions.append("cl.sku = :fsku")
            params["fsku"] = fixed.sku
    else:
        conditions.append("cl.supported_sockets IS NOT NULL")
        conditions.append("cl.max_tdp_watts IS NOT NULL")
        conditions.append(":sock = ANY(cl.supported_sockets)")
        conditions.append("cl.max_tdp_watts >= :req_tdp")
        params["sock"] = cpu_socket
        params["req_tdp"] = int(required_tdp)

    stock_sql = _stock_where("sp", allow_transit)
    price_usd_sql = _price_in_usd_sql("sp")

    query = text(
        f"""
        SELECT cl.id, cl.model, cl.manufacturer, cl.sku,
               cl.supported_sockets, cl.max_tdp_watts,
               {price_usd_sql} AS price_usd_min
        FROM coolers cl
        JOIN supplier_prices sp
          ON sp.category = 'cooler' AND sp.component_id = cl.id
        WHERE {' AND '.join(conditions)}
          AND {stock_sql}
        GROUP BY cl.id
        ORDER BY price_usd_min ASC
        LIMIT 1
        """
    )
    row = session.execute(query, params).mappings().first()
    return dict(row) if row else None


# -----------------------------------------------------------------------------
# Вспомогательное: приведение price_usd_min из Decimal к float
# -----------------------------------------------------------------------------

def to_float(value: Any) -> float:
    """Приводит Decimal/int/float к float. None → 0.0."""
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)
