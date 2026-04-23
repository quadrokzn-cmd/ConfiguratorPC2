# Верхнеуровневая функция подбора конфигурации.
#
# build_config(req) оркестрирует:
#   1) Фиксирует курс USD/RUB через fx-модуль (этап 2.5В).
#   2) Пытается собрать конфигурацию из наличия (allow_transit=False).
#   3) Если ни одна не вышла — пробует с транзитом.
#   4) Для каждого производителя CPU (Intel, AMD) собирает свой вариант.
#   5) Внутри одного производителя: если GPU НЕ обязательна — считает путь A
#      (без дискретной GPU, CPU с iGPU) и путь B (с дискретной GPU) и берёт
#      минимум. Если GPU обязательна — один проход.
#   6) Формирует BuildResult с warning-ами, предложениями поставщиков и
#      отказами с причинами.

from __future__ import annotations

import logging
from typing import Any

from app.database import SessionLocal
from app.services.configurator import candidates as C
from app.services.configurator.builder import assemble_build
from app.services.configurator.prices import choose_supplier, fetch_offers
from app.services.configurator.schema import (
    BuildRequest,
    BuildResult,
    ComponentChoice,
    SupplierOffer,
    Variant,
)
from app.services.configurator.warnings import collect_warnings
from app.services.enrichment.openai_search.fx import get_usd_rub_rate

logger = logging.getLogger(__name__)


_ALL_MANUFACTURERS: tuple[str, ...] = ("Intel", "AMD")


def _is_gpu_required(req: BuildRequest) -> bool:
    """GPU обязательна, если явно required=True, или заданы минимумы, или зафиксирована."""
    if req.gpu.required:
        return True
    if req.gpu.min_vram_gb:
        return True
    if req.gpu.fixed and req.gpu.fixed.is_set():
        return True
    return False


def _pick_manufacturers(req: BuildRequest) -> list[str]:
    """Список производителей CPU для перебора.

    Если пользователь указал manufacturer в блоке cpu — оставляем только его;
    иначе пытаемся обоих.
    """
    m = (req.cpu.manufacturer or "").strip().lower()
    if m == "intel":
        return ["Intel"]
    if m == "amd":
        return ["AMD"]
    return list(_ALL_MANUFACTURERS)


def _best_build_for_manufacturer(
    session,
    *,
    req: BuildRequest,
    manufacturer: str,
    usd_rub: float,
    allow_transit: bool,
) -> tuple[dict | None, str, str | None]:
    """Подбирает лучшую (минимальную по цене) сборку для одного производителя.

    Возвращает (build_dict | None, path_used, refusal_reason_text | None).

    Логика:
      - если GPU обязательна: один проход, path_used='default';
      - если GPU не обязательна: считаем путь A (CPU с iGPU, без GPU) и путь B
        (любой CPU + дешёвая GPU), берём минимум.
    """
    gpu_required = _is_gpu_required(req)

    # ---------------- Вариант 1: GPU обязательна ----------------
    if gpu_required:
        cpus = C.get_cpu_candidates(
            session,
            req=req,
            manufacturer=manufacturer,
            only_with_igpu=False,
            usd_rub=usd_rub,
            allow_transit=allow_transit,
        )
        if not cpus:
            return None, "default", _cpu_refusal_text(manufacturer, req)

        best: dict | None = None
        for cpu in cpus:
            build = assemble_build(
                session,
                cpu=cpu, req=req,
                usd_rub=usd_rub, allow_transit=allow_transit,
                with_gpu=True,
            )
            if build is None:
                continue
            if best is None or build["total_usd"] < best["total_usd"]:
                best = build
        if best is None:
            return None, "default", (
                f"Не удалось собрать {manufacturer}-конфигурацию: не найдены "
                f"совместимые компоненты (MB/RAM/GPU/кулер/корпус/БП) для CPU "
                f"в наличии"
            )
        return best, "default", None

    # ---------------- Вариант 2: GPU не обязательна, считаем пути A и B -----
    # Путь A: CPU с iGPU, сборка без дискретной GPU
    cpus_a = C.get_cpu_candidates(
        session,
        req=req,
        manufacturer=manufacturer,
        only_with_igpu=True,
        usd_rub=usd_rub,
        allow_transit=allow_transit,
    )
    best_a: dict | None = None
    for cpu in cpus_a:
        build = assemble_build(
            session,
            cpu=cpu, req=req,
            usd_rub=usd_rub, allow_transit=allow_transit,
            with_gpu=False,
        )
        if build is None:
            continue
        if best_a is None or build["total_usd"] < best_a["total_usd"]:
            best_a = build

    # Путь B: любой CPU + дешёвая дискретная GPU
    cpus_b = C.get_cpu_candidates(
        session,
        req=req,
        manufacturer=manufacturer,
        only_with_igpu=False,
        usd_rub=usd_rub,
        allow_transit=allow_transit,
    )
    best_b: dict | None = None
    for cpu in cpus_b:
        build = assemble_build(
            session,
            cpu=cpu, req=req,
            usd_rub=usd_rub, allow_transit=allow_transit,
            with_gpu=True,
        )
        if build is None:
            continue
        if best_b is None or build["total_usd"] < best_b["total_usd"]:
            best_b = build

    if best_a is None and best_b is None:
        if not cpus_a and not cpus_b:
            return None, "default", _cpu_refusal_text(manufacturer, req)
        return None, "default", (
            f"Не удалось собрать {manufacturer}-конфигурацию: нет совместимых "
            f"компонентов в наличии"
        )

    if best_a is not None and best_b is not None:
        if best_a["total_usd"] <= best_b["total_usd"]:
            return best_a, "A", None
        return best_b, "B", None
    if best_a is not None:
        return best_a, "A", None
    return best_b, "B", None


def _cpu_refusal_text(manufacturer: str, req: BuildRequest) -> str:
    """Текст отказа, если не нашлось ни одного CPU под требования."""
    bits: list[str] = []
    if req.cpu.min_cores:
        bits.append(f"минимум {req.cpu.min_cores} ядер")
    if req.cpu.min_threads:
        bits.append(f"минимум {req.cpu.min_threads} потоков")
    if req.cpu.min_base_ghz:
        bits.append(f"базовая частота от {req.cpu.min_base_ghz} ГГц")
    suffix = f" ({', '.join(bits)})" if bits else ""
    return f"Не найдено подходящих CPU производителя {manufacturer}{suffix} в наличии"


def _build_to_variant(
    session,
    *,
    build: dict,
    manufacturer_label: str,
    path_used: str,
    usd_rub: float,
    allow_transit: bool,
    used_transit_fallback: bool,
) -> Variant:
    """Преобразует словарь-сборку из builder в Variant c предложениями поставщиков."""
    def make_choice(category: str, row: dict, quantity: int = 1) -> ComponentChoice:
        offers = fetch_offers(
            session,
            category=category,
            component_id=row["id"],
            usd_rub=usd_rub,
            allow_transit=allow_transit,
        )
        if not offers:
            # Теоретически невозможно — мы только что выбрали этот компонент
            # по тем же условиям. Но на всякий случай — fallback.
            chosen = SupplierOffer(
                supplier="—",
                price_usd=float(row.get("price_usd_min") or 0.0),
                price_rub=float(row.get("price_usd_min") or 0.0) * usd_rub,
                stock=0,
                in_transit=False,
            )
            others: list[SupplierOffer] = []
        else:
            chosen, others = choose_supplier(offers)
        return ComponentChoice(
            category=category,
            component_id=int(row["id"]),
            model=row.get("model") or "",
            sku=row.get("sku"),
            manufacturer=row.get("manufacturer") or "",
            chosen=chosen,
            also_available_at=others,
            quantity=quantity,
        )

    components: list[ComponentChoice] = []
    components.append(make_choice("cpu",         build["cpu"]))
    components.append(make_choice("motherboard", build["motherboard"]))

    ram_block = build["ram"]
    components.append(make_choice("ram", ram_block["row"], quantity=int(ram_block["quantity"])))

    if build["gpu"] is not None:
        components.append(make_choice("gpu", build["gpu"]))

    components.append(make_choice("storage", build["storage"]))
    # Если корпус со встроенным БП (сценарий B) — отдельного PSU нет.
    if build["psu"] is not None:
        components.append(make_choice("psu", build["psu"]))
    components.append(make_choice("case",    build["case"]))
    if build["cooler"] is not None:
        components.append(make_choice("cooler", build["cooler"]))

    # Итог пересчитываем по выбранным поставщикам (могут отличаться от
    # минимумов, если у одного поставщика нет stock, но есть у другого).
    total_usd = 0.0
    for ch in components:
        total_usd += ch.chosen.price_usd * ch.quantity
    total_rub = total_usd * usd_rub

    # Используем ли в этой сборке транзит хотя бы у одного компонента
    any_in_transit = any(ch.chosen.in_transit for ch in components) or used_transit_fallback

    warnings = collect_warnings(
        cpu=build["cpu"],
        motherboard=build["motherboard"],
        gpu=build["gpu"],
        case=build["case"],
        used_transit=any_in_transit,
        extra_warnings=list(build.get("rule_warnings") or []),
    )

    return Variant(
        manufacturer=manufacturer_label,
        components=components,
        total_usd=round(total_usd, 2),
        total_rub=round(total_rub, 2),
        warnings=warnings,
        used_transit=any_in_transit,
        path_used=path_used,
    )


def _apply_budget(
    variants: list[Variant],
    budget_usd: float,
) -> tuple[list[Variant], list[str]]:
    """Отсеивает варианты, не влезающие в бюджет.

    Возвращает (оставшиеся_варианты, список_причин_отказа_по_производителям).
    """
    kept: list[Variant] = []
    rejected_msgs: list[tuple[str, str]] = []
    for v in variants:
        if v.total_usd <= budget_usd:
            kept.append(v)
        else:
            rejected_msgs.append((
                v.manufacturer,
                f"Минимальная сборка {v.manufacturer} в наличии "
                f"(${v.total_usd:.2f}) превышает бюджет ${budget_usd:.2f}",
            ))
    return kept, [f"{m}: {msg}" for m, msg in rejected_msgs]


def build_config(req: BuildRequest) -> BuildResult:
    """Главная точка входа. Подбирает до двух вариантов (Intel + AMD)."""
    # Курс USD/RUB фиксируется один раз на весь запрос.
    usd_rub, fx_source = get_usd_rub_rate()

    # Пустой запрос — вежливый отказ
    if req.is_empty():
        return BuildResult(
            status="failed",
            variants=[],
            refusal_reason={
                "request": (
                    "Запрос пустой. Укажите хотя бы бюджет, назначение или "
                    "минимальные характеристики"
                ),
            },
            usd_rub_rate=usd_rub,
            fx_source=fx_source,
        )

    manufacturers = _pick_manufacturers(req)

    session = SessionLocal()
    try:
        variants, refusals, used_transit_run = _run_pass(
            session,
            req=req,
            manufacturers=manufacturers,
            usd_rub=usd_rub,
            allow_transit=req.allow_transit,
        )

        # Если ничего не вышло и транзит ещё не включали — повторяем с ним.
        if not variants and not req.allow_transit:
            variants, refusals, used_transit_run = _run_pass(
                session,
                req=req,
                manufacturers=manufacturers,
                usd_rub=usd_rub,
                allow_transit=True,
            )
    finally:
        session.close()

    # Применяем бюджет (если задан)
    budget_rejections: list[str] = []
    if req.budget_usd is not None and variants:
        variants, budget_msgs = _apply_budget(variants, float(req.budget_usd))
        budget_rejections.extend(budget_msgs)

    # Формируем статус и причины отказа
    status: str
    refusal_reason: dict[str, str] | None = None

    if variants:
        status = "ok"
        # Если по какому-то из производителей отказ — отразим в refusal_reason
        missing = {m.lower(): refusals[m] for m in refusals}
        if missing:
            refusal_reason = missing
            status = "partial"
    else:
        status = "failed"
        if not refusals and budget_rejections:
            refusal_reason = {"budget": "; ".join(budget_rejections)}
        elif refusals:
            refusal_reason = {m.lower(): refusals[m] for m in refusals}
        else:
            refusal_reason = {"unknown": "Не удалось собрать ни одной конфигурации"}

    return BuildResult(
        status=status,
        variants=variants,
        refusal_reason=refusal_reason,
        usd_rub_rate=usd_rub,
        fx_source=fx_source,
    )


def _run_pass(
    session,
    *,
    req: BuildRequest,
    manufacturers: list[str],
    usd_rub: float,
    allow_transit: bool,
) -> tuple[list[Variant], dict[str, str], bool]:
    """Один проход подбора для списка производителей.

    Возвращает (variants, refusals_per_manufacturer, used_transit_run_flag).
    """
    variants: list[Variant] = []
    refusals: dict[str, str] = {}
    used_transit_run = allow_transit

    for mfr in manufacturers:
        build, path, refusal = _best_build_for_manufacturer(
            session,
            req=req,
            manufacturer=mfr,
            usd_rub=usd_rub,
            allow_transit=allow_transit,
        )
        if build is None:
            if refusal:
                refusals[mfr] = refusal
            continue
        variant = _build_to_variant(
            session,
            build=build,
            manufacturer_label=mfr,
            path_used=path,
            usd_rub=usd_rub,
            allow_transit=allow_transit,
            used_transit_fallback=used_transit_run and not req.allow_transit,
        )
        variants.append(variant)

    return variants, refusals, used_transit_run
