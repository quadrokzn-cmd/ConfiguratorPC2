# Правила совместимости комплектующих.
#
# Каждое правило — отдельная функция с понятной сигнатурой и одной
# ответственностью. Аргументы — словари-строки из БД (как их возвращает
# candidates.py), чтобы правила оставались независимы от SQLAlchemy ORM.
#
# Правила делятся на два типа:
#   1) Простые bool-правила — используются точечно в builder.py при фильтрации
#      кандидатов (например, при поиске кулера).
#   2) Композиционное check_build — финальная валидация уже собранной
#      конфигурации перед выдачей пользователю. Возвращает список нарушений.

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# Коэффициент запаса кулера по TDP: требуем, чтобы max_tdp_watts
# был не меньше CPU.tdp_watts * этот множитель.
COOLER_TDP_MARGIN: float = 1.30


@dataclass
class RuleResult:
    """Результат проверки одного правила для финальной валидации сборки.

    ok       — правило выполнено (или не применимо — тогда True);
    reason   — причина отказа, если ok=False;
    warning  — нестрогое замечание (например, поле пустое и проверка пропущена),
               которое нужно показать пользователю, но не блокировать сборку.
    """
    ok: bool
    reason: str | None = None
    warning: str | None = None


def _norm(value: Any) -> Any:
    """Нормализация для сравнения: строки — trim, пустые строки → None."""
    if isinstance(value, str):
        v = value.strip()
        return v if v else None
    return value


# -----------------------------------------------------------------------------
# Простые булевы правила — для фильтрации кандидатов в builder.py
# -----------------------------------------------------------------------------

def cpu_mb_socket(cpu: dict, motherboard: dict) -> bool:
    """CPU подходит к материнской плате по сокету."""
    return _norm(cpu.get("socket")) == _norm(motherboard.get("socket")) \
        and _norm(cpu.get("socket")) is not None


def mb_ram_match(motherboard: dict, ram: dict) -> bool:
    """Совпадение типа памяти и форм-фактора модулей."""
    mb_type = _norm(motherboard.get("memory_type"))
    ram_type = _norm(ram.get("memory_type"))
    ram_ff = _norm(ram.get("form_factor"))
    if not mb_type or not ram_type or not ram_ff:
        return False
    if mb_type != ram_type:
        return False
    # На настольные платы ставим только DIMM. SO-DIMM — для ноутбуков.
    return ram_ff == "DIMM"


def mb_case_form_factor(motherboard: dict, case: dict) -> bool:
    """Форм-фактор MB входит в поддерживаемые корпусом форм-факторы."""
    mb_ff = _norm(motherboard.get("form_factor"))
    supported = case.get("supported_form_factors")
    if not mb_ff or not supported:
        return False
    return mb_ff in supported


def required_cooler_tdp(cpu: dict) -> int | None:
    """Минимальный max_tdp_watts кулера для данного CPU с запасом 30%.

    Возвращает None, если у CPU не заполнен tdp_watts — в этом случае кулер
    подобрать нельзя (возвращаем отказ выше по стеку).
    """
    tdp = cpu.get("tdp_watts")
    if tdp is None:
        return None
    return int(round(float(tdp) * COOLER_TDP_MARGIN))


def cooler_cpu(cooler: dict, cpu: dict) -> bool:
    """Кулер подходит к CPU: поддерживает сокет и выдерживает TDP с запасом 30%.

    Если у кулера не заполнены supported_sockets или max_tdp_watts — отказ
    (такие кулеры вообще не должны попадать в подбор, но перестрахуемся).
    """
    sockets = cooler.get("supported_sockets")
    max_tdp = cooler.get("max_tdp_watts")
    cpu_socket = _norm(cpu.get("socket"))
    required = required_cooler_tdp(cpu)
    if not sockets or max_tdp is None or cpu_socket is None or required is None:
        return False
    return cpu_socket in sockets and int(max_tdp) >= required


# -----------------------------------------------------------------------------
# Правила с возможным «пропуском» при NULL — для финальной валидации
# -----------------------------------------------------------------------------

def gpu_case_length(gpu: dict | None, case: dict) -> RuleResult:
    """Длина GPU не превышает допустимую в корпусе.

    Если хотя бы одно из полей NULL — проверка пропускается и добавляется
    предупреждение; это предусмотрено задачей, т.к. поля массово пусты.
    """
    if gpu is None:
        return RuleResult(ok=True)
    gpu_len = gpu.get("length_mm")
    case_max = case.get("max_gpu_length_mm")
    if gpu_len is None or case_max is None:
        return RuleResult(
            ok=True,
            warning=(
                "Совместимость GPU и корпуса по длине не подтверждена, "
                "требуется проверка менеджером"
            ),
        )
    if int(gpu_len) <= int(case_max):
        return RuleResult(ok=True)
    return RuleResult(
        ok=False,
        reason=(
            f"Видеокарта длиной {gpu_len} мм не помещается в корпус "
            f"(допустимо до {case_max} мм)"
        ),
    )


def iron_invariant_gpu(cpu: dict, gpu: dict | None) -> RuleResult:
    """Железный инвариант: без iGPU и без дискретной GPU — сборка не покажет изображение.

    Если has_integrated_graphics = NULL — это критично: не можем гарантировать
    вывод изображения, считаем сборку невалидной.
    """
    has_igpu = cpu.get("has_integrated_graphics")
    if gpu is not None:
        return RuleResult(ok=True)
    if has_igpu is True:
        return RuleResult(ok=True)
    return RuleResult(
        ok=False,
        reason=(
            "У процессора нет встроенной графики, а дискретная видеокарта "
            "в сборке отсутствует — изображение выводить нечем"
        ),
    )


# -----------------------------------------------------------------------------
# Композиционная проверка собранной конфигурации
# -----------------------------------------------------------------------------

def check_build(build: dict) -> tuple[list[str], list[str]]:
    """Финальная валидация сборки.

    Принимает словарь с ключами: cpu, motherboard, ram, gpu (опц.), storage,
    psu, case, cooler (опц.). Значения — словари-строки из БД.

    Возвращает (errors, warnings):
      - errors   — список причин, по которым сборка невалидна (если не пусто —
                   сборку выдавать нельзя);
      - warnings — список предупреждений, которые нужно показать в итоге.
    """
    errors: list[str] = []
    warnings: list[str] = []

    cpu = build.get("cpu")
    mb = build.get("motherboard")
    ram = build.get("ram")
    gpu = build.get("gpu")
    case_ = build.get("case")
    cooler = build.get("cooler")

    if cpu is None or mb is None:
        errors.append("В сборке отсутствует CPU или материнская плата")
        return errors, warnings

    # 1. CPU ↔ MB: сокет
    if not cpu_mb_socket(cpu, mb):
        errors.append(
            f"Сокет процессора ({cpu.get('socket')!r}) не совпадает с сокетом "
            f"материнской платы ({mb.get('socket')!r})"
        )

    # 2. MB ↔ RAM
    if ram is not None and not mb_ram_match(mb, ram):
        errors.append(
            "Тип или форм-фактор оперативной памяти несовместим с материнской платой"
        )

    # 3. MB ↔ Case
    if case_ is not None and not mb_case_form_factor(mb, case_):
        errors.append(
            f"Форм-фактор материнской платы ({mb.get('form_factor')!r}) не "
            f"поддерживается корпусом"
        )

    # 4. Cooler ↔ CPU — только если кулер присутствует в сборке
    if cooler is not None and not cooler_cpu(cooler, cpu):
        errors.append(
            "Кулер не подходит к процессору по сокету или по запасу мощности"
        )

    # 5. GPU ↔ Case — длина
    if case_ is not None:
        res = gpu_case_length(gpu, case_)
        if not res.ok:
            errors.append(res.reason or "GPU не проходит по длине")
        if res.warning:
            warnings.append(res.warning)

    # 6. Железный инвариант GPU
    res = iron_invariant_gpu(cpu, gpu)
    if not res.ok:
        errors.append(res.reason or "Нарушен железный инвариант GPU")

    return errors, warnings
