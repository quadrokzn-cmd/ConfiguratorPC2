# Сборка одной конфигурации вокруг конкретного CPU.
#
# Вход: CPU-словарь и параметры запроса. Выход: либо полностью собранная
# конфигурация (словарь с компонентами и ценой), либо None, если что-то
# не удалось подобрать или сборка не прошла финальную валидацию.
#
# Важные детали:
#   - Cooler не подбираем для BOX-процессоров (по условию задачи).
#   - RAM подбираем перебором: для N = 1..memory_slots ищем самый дешёвый
#     модуль с module_size_gb * N >= min_gb и берём глобальный минимум.
#     Если memory_slots = NULL у MB — считаем 4 слота и добавляем warning.
#   - Финальная валидация сборки через compatibility.check_build.

from __future__ import annotations

from typing import Any

from app.services.compatibility import check_build
from app.services.compatibility.rules import required_cooler_tdp
from app.services.configurator import candidates as C
from app.services.configurator.schema import BuildRequest


# Фоллбэк на количество слотов DIMM, если у MB memory_slots = NULL.
_DEFAULT_RAM_SLOTS = 4


def _pick_ram_combo(
    ram_candidates: list[dict],
    *,
    memory_slots: int,
    min_gb: int,
) -> tuple[dict, int, float] | None:
    """Выбирает (модуль, количество, суммарная цена в USD).

    Для каждого модуля и для N = 1..memory_slots проверяет, что N * module_size_gb
    покрывает требование. Среди подходящих — минимум по суммарной цене.
    Если требование по объёму не задано (0), берёт самый дешёвый одиночный модуль.
    """
    best: tuple[dict, int, float] | None = None
    for r in ram_candidates:
        size_gb = int(r["module_size_gb"])
        price = float(r["price_usd_min"])
        if size_gb <= 0 or price <= 0:
            continue
        # Перебираем количество модулей
        for n in range(1, max(1, memory_slots) + 1):
            if min_gb > 0 and size_gb * n < min_gb:
                continue
            total = price * n
            if best is None or total < best[2]:
                best = (r, n, total)
            # Дальше увеличивать N смысла нет — только дороже
            break
    return best


def assemble_build(
    session,
    *,
    cpu: dict,
    req: BuildRequest,
    usd_rub: float,
    allow_transit: bool,
    with_gpu: bool,
) -> dict | None:
    """Собирает полную конфигурацию вокруг данного CPU.

    with_gpu:
      - True  — подбираем дискретную GPU (обязательно или для пути B);
      - False — сборка без дискретной GPU (путь A, требует CPU с iGPU).

    Возвращает словарь:
      {
        'cpu':  {...},
        'motherboard': {...},
        'ram':  {'row': {...}, 'quantity': N, 'price_usd_total': float},
        'gpu':  {...} | None,
        'storage': {...},
        'psu':  {...},
        'case': {...},
        'cooler': {...} | None,
        'total_usd': float,
        'rule_warnings': [str, ...],
      }
    Или None, если сборка не получилась (нет кандидатов, нарушены правила).
    """
    # --- 1. Материнская плата ------------------------------------------------
    cpu_socket = cpu.get("socket")
    if not cpu_socket:
        return None

    motherboard = C.get_cheapest_motherboard(
        session,
        cpu_socket=cpu_socket,
        fixed=req.motherboard,
        usd_rub=usd_rub,
        allow_transit=allow_transit,
    )
    if motherboard is None:
        return None

    # --- 2. Кулер — только если CPU НЕ BOX ----------------------------------
    cooler: dict | None = None
    package_type = (cpu.get("package_type") or "").upper()
    if package_type != "BOX":
        req_tdp = required_cooler_tdp(cpu)
        if req_tdp is None:
            # У CPU не заполнен tdp_watts — кулер подобрать не можем.
            return None
        cooler = C.get_cheapest_cooler(
            session,
            cpu_socket=cpu_socket,
            required_tdp=req_tdp,
            fixed=req.cooler,
            usd_rub=usd_rub,
            allow_transit=allow_transit,
        )
        if cooler is None:
            return None

    # --- 3. RAM -------------------------------------------------------------
    # Тип памяти: если пользователь задал явно — используем; иначе берём из MB.
    ram_type = req.ram.memory_type or motherboard.get("memory_type")
    if not ram_type:
        return None

    ram_candidates = C.get_ram_candidates(
        session,
        memory_type=ram_type,
        min_frequency_mhz=req.ram.min_frequency_mhz,
        usd_rub=usd_rub,
        allow_transit=allow_transit,
    )
    if not ram_candidates:
        return None

    mb_slots = motherboard.get("memory_slots") or _DEFAULT_RAM_SLOTS
    try:
        mb_slots = int(mb_slots)
    except (TypeError, ValueError):
        mb_slots = _DEFAULT_RAM_SLOTS
    if mb_slots < 1:
        mb_slots = _DEFAULT_RAM_SLOTS

    ram_pick = _pick_ram_combo(
        ram_candidates,
        memory_slots=mb_slots,
        min_gb=int(req.ram.min_gb or 0),
    )
    if ram_pick is None:
        return None
    ram_row, ram_qty, ram_total_usd = ram_pick

    # --- 4. GPU -------------------------------------------------------------
    gpu: dict | None = None
    if with_gpu:
        gpu = C.get_cheapest_gpu(
            session,
            min_vram_gb=req.gpu.min_vram_gb,
            fixed=req.gpu.fixed,
            usd_rub=usd_rub,
            allow_transit=allow_transit,
        )
        if gpu is None:
            return None

    # --- 5. Storage ---------------------------------------------------------
    storage = C.get_cheapest_storage(
        session,
        req=req.storage,
        usd_rub=usd_rub,
        allow_transit=allow_transit,
    )
    if storage is None:
        return None

    # --- 6. PSU -------------------------------------------------------------
    psu = C.get_cheapest_psu(
        session,
        fixed=req.psu,
        usd_rub=usd_rub,
        allow_transit=allow_transit,
    )
    if psu is None:
        return None

    # --- 7. Case ------------------------------------------------------------
    mb_ff = motherboard.get("form_factor")
    if not mb_ff:
        return None
    case = C.get_cheapest_case(
        session,
        mb_form_factor=mb_ff,
        fixed=req.case,
        usd_rub=usd_rub,
        allow_transit=allow_transit,
    )
    if case is None:
        return None

    # --- 8. Финальная валидация правилами совместимости ---------------------
    errors, warnings = check_build({
        "cpu":         cpu,
        "motherboard": motherboard,
        "ram":         ram_row,
        "gpu":         gpu,
        "case":        case,
        "cooler":      cooler,
        "storage":     storage,
        "psu":         psu,
    })
    if errors:
        return None

    # --- 9. Итог ------------------------------------------------------------
    total_usd = 0.0
    total_usd += C.to_float(cpu.get("price_usd_min"))
    total_usd += C.to_float(motherboard.get("price_usd_min"))
    total_usd += ram_total_usd
    if gpu is not None:
        total_usd += C.to_float(gpu.get("price_usd_min"))
    total_usd += C.to_float(storage.get("price_usd_min"))
    total_usd += C.to_float(psu.get("price_usd_min"))
    total_usd += C.to_float(case.get("price_usd_min"))
    if cooler is not None:
        total_usd += C.to_float(cooler.get("price_usd_min"))

    return {
        "cpu":         cpu,
        "motherboard": motherboard,
        "ram":         {"row": ram_row, "quantity": ram_qty, "price_usd_total": ram_total_usd},
        "gpu":         gpu,
        "storage":     storage,
        "psu":         psu,
        "case":        case,
        "cooler":      cooler,
        "total_usd":   total_usd,
        "rule_warnings": warnings,
    }
