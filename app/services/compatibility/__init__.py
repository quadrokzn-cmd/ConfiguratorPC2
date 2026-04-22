# Правила совместимости комплектующих (этап 3).
#
# Каждое правило — отдельная функция в rules.py. Функции возвращают либо
# RuleResult (ok / reason / warning), либо bool для самых простых проверок.
# Модуль configurator.builder вызывает check_build(build) и получает список
# нарушений — по нему принимается решение, принимать сборку или нет.

from app.services.compatibility.rules import (
    RuleResult,
    check_build,
    cooler_cpu,
    cpu_mb_socket,
    gpu_case_length,
    iron_invariant_gpu,
    mb_case_form_factor,
    mb_ram_match,
    required_cooler_tdp,
)

__all__ = [
    "RuleResult",
    "check_build",
    "cooler_cpu",
    "cpu_mb_socket",
    "gpu_case_length",
    "iron_invariant_gpu",
    "mb_case_form_factor",
    "mb_ram_match",
    "required_cooler_tdp",
]
