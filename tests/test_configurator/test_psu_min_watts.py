# Тесты 2026-05-14: фильтр PSU по запрошенной пользователем мощности.
#
# Баг: пользователь пишет «Компьютер (..., 550W)», NLU не извлекает 550W,
# selector использует только base_req_watts=400W, в итоге выдаётся
# Exegate 450W вместо запрошенных ≥550W. Никакого warning'а нет.
#
# Fix: BuildRequest.min_psu_watts передаётся из NLU в engine; selector
# берёт max(base_req_watts, min_psu_watts) как фильтр. Если БП такой
# мощности нет — fallback на base_req_watts с shortage-warning.

from __future__ import annotations

import pytest

from portal.services.configurator.engine import selector as S
from portal.services.configurator.engine.schema import request_from_dict

# Переиспользуем фикстуры из соседнего файла
from tests.test_configurator.test_selector import (  # noqa: F401
    mk_cpu, mk_case, mk_cooler, mk_psu, world, MockWorld,
    _std_common,
)


def _build_with_min_watts(world: MockWorld, min_watts: int):
    """Утилита: построить сборку с явным min_psu_watts из request_from_dict."""
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 100, igpu=True))
    req = request_from_dict({
        "cpu":           {"min_cores": 4},
        "min_psu_watts": min_watts,
    })
    return S.build_config(req)


def test_min_psu_watts_selects_psu_above_requested(world: MockWorld):
    """Запрошен БП 550W → selector выбирает 650W (есть в наличии)."""
    _std_common(world)  # psu 650W
    result = _build_with_min_watts(world, 550)

    assert result.status in ("ok", "partial")
    variant = result.variants[0]
    psu_choice = next(c for c in variant.components if c.category == "psu")
    # 650W >= 550W — никакого shortage'а быть не должно.
    psu_row = next(p for p in world.psus if p["id"] == psu_choice.component_id)
    assert psu_row["power_watts"] >= 550
    assert not any("550W" in w and "недостаточно" in w for w in variant.warnings)


def test_min_psu_watts_fallback_when_no_psu_strong_enough(world: MockWorld):
    """Запрошен 550W, в наличии только 450W (≥400 base) → fallback + warning."""
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 100, igpu=True))
    # ВАЖНО: НЕ зовём _std_common, чтобы не было лишнего 650W PSU.
    from tests.test_configurator.test_selector import (
        mk_mb, mk_ram, mk_storage,
    )
    world.motherboards.append(mk_mb(102, "LGA1700", "ATX", "DDR5", 120, slots=4))
    world.rams.append(mk_ram(201, "DDR5", 16, 5200, 40))
    world.storages.append(mk_storage(401, 500, "SSD", 45))
    world.psus.append(mk_psu(501, watts=450, price=30))   # только 450W
    world.cases.append(mk_case(601, ["ATX", "mATX"], 60, max_gpu=380))
    world.coolers.append(mk_cooler(701, ["LGA1700"], 200, 25))

    req = request_from_dict({
        "cpu":           {"min_cores": 4},
        "min_psu_watts": 550,
    })
    result = S.build_config(req)

    assert result.status in ("ok", "partial"), \
        "Селектор обязан подобрать сборку через fallback, а не отказать"
    variant = result.variants[0]
    psu_choice = next(c for c in variant.components if c.category == "psu")
    assert psu_choice.component_id == 501  # тот самый 450W
    # Warning о shortage должен присутствовать.
    shortage_warnings = [
        w for w in variant.warnings
        if "450" in w and "550" in w
    ]
    assert shortage_warnings, (
        f"Ожидался warning о shortage БП 450W при запрошенных 550W, "
        f"получено: {variant.warnings}"
    )


def test_min_psu_watts_below_base_uses_base(world: MockWorld):
    """Запрошен 350W (ниже базы 400W) → используется base, no shortage warning."""
    _std_common(world)  # psu 650W в наличии
    result = _build_with_min_watts(world, 350)

    assert result.status in ("ok", "partial")
    variant = result.variants[0]
    # Никаких shortage'ов — пользователь не запросил больше, чем мы можем.
    shortage_warnings = [
        w for w in variant.warnings
        if "недостаточно мощности" in w
    ]
    assert not shortage_warnings


def test_min_psu_watts_scenario_b_case_included(world: MockWorld):
    """Сценарий B: corpus со встроенным БП тоже фильтруется по min_psu_watts."""
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 100, igpu=True))
    from tests.test_configurator.test_selector import (
        mk_mb, mk_ram, mk_storage,
    )
    world.motherboards.append(mk_mb(102, "LGA1700", "ATX", "DDR5", 120, slots=4))
    world.rams.append(mk_ram(201, "DDR5", 16, 5200, 40))
    world.storages.append(mk_storage(401, 500, "SSD", 45))
    world.coolers.append(mk_cooler(701, ["LGA1700"], 200, 25))
    # Только корпуса со встроенным БП: 450W и 600W, без отдельных БП.
    world.cases += [
        mk_case(701, ["ATX", "mATX"], 70, max_gpu=380,
                has_psu=True, builtin_watts=450),
        mk_case(702, ["ATX", "mATX"], 90, max_gpu=380,
                has_psu=True, builtin_watts=600),
    ]

    req = request_from_dict({
        "cpu":           {"min_cores": 4},
        "min_psu_watts": 550,
    })
    result = S.build_config(req)

    assert result.status in ("ok", "partial")
    variant = result.variants[0]
    case_choice = next(c for c in variant.components if c.category == "case")
    # Корпус 702 с 600W >= 550W должен быть выбран, не 701 с 450W.
    assert case_choice.component_id == 702
