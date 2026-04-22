# Интеграционные тесты selector.build_config с подменой кандидатов и цен.
#
# Мы не поднимаем реальную PostgreSQL: вместо этого патчим функции из
# app.services.configurator.candidates и .prices, возвращая из них нужные
# строки-словари. Также мокаем курс ЦБ через fx.get_usd_rub_rate.
#
# Такой подход позволяет быстро проверить большое число сценариев подбора,
# включая невозможные сейчас (BOX CPU, два поставщика, транзит).

from __future__ import annotations

import pytest

from app.services.configurator import candidates as C
from app.services.configurator import prices as P
from app.services.configurator import selector as S
from app.services.configurator.schema import (
    SupplierOffer,
    request_from_dict,
)


# -----------------------------------------------------------------------------
# Фикстуры-«строки БД»
# -----------------------------------------------------------------------------

def mk_cpu(
    cid: int, manufacturer: str, socket: str, price: float,
    *, cores: int = 6, threads: int = 12, base_ghz: float = 3.6,
    tdp: int = 65, igpu: bool = True, package: str = "OEM",
    mem_type: str = "DDR5",
) -> dict:
    return {
        "id": cid,
        "model": f"{manufacturer} CPU {cid}",
        "manufacturer": manufacturer,
        "sku": f"SKU-CPU-{cid}",
        "socket": socket,
        "cores": cores,
        "threads": threads,
        "base_clock_ghz": base_ghz,
        "turbo_clock_ghz": base_ghz + 1.0,
        "tdp_watts": tdp,
        "has_integrated_graphics": igpu,
        "memory_type": mem_type,
        "package_type": package,
        "price_usd_min": price,
    }


def mk_mb(mid: int, socket: str, ff: str, mem: str, price: float,
          *, slots: int | None = 4) -> dict:
    return {
        "id": mid,
        "model": f"MB-{mid}",
        "manufacturer": "AsRock",
        "sku": f"SKU-MB-{mid}",
        "socket": socket,
        "form_factor": ff,
        "memory_type": mem,
        "memory_slots": slots,
        "price_usd_min": price,
    }


def mk_ram(rid: int, mem: str, size: int, freq: int, price: float,
           *, ff: str = "DIMM") -> dict:
    return {
        "id": rid,
        "model": f"RAM-{rid}",
        "manufacturer": "Kingston",
        "sku": f"SKU-RAM-{rid}",
        "memory_type": mem,
        "form_factor": ff,
        "module_size_gb": size,
        "frequency_mhz": freq,
        "price_usd_min": price,
    }


def mk_gpu(gid: int, vram: int, price: float, *, length: int | None = None,
           needs_power: bool = True) -> dict:
    return {
        "id": gid,
        "model": f"GPU-{gid}",
        "manufacturer": "NVIDIA",
        "sku": f"SKU-GPU-{gid}",
        "vram_gb": vram,
        "tdp_watts": 180,
        "needs_extra_power": needs_power,
        "length_mm": length,
        "price_usd_min": price,
    }


def mk_storage(sid: int, gb: int, kind: str, price: float) -> dict:
    return {
        "id": sid,
        "model": f"SSD-{sid}",
        "manufacturer": "Kingston",
        "sku": f"SKU-ST-{sid}",
        "storage_type": kind,
        "form_factor": "M.2",
        "interface": "NVMe",
        "capacity_gb": gb,
        "price_usd_min": price,
    }


def mk_psu(pid: int, watts: int, price: float) -> dict:
    return {
        "id": pid,
        "model": f"PSU-{pid}",
        "manufacturer": "Corsair",
        "sku": f"SKU-PSU-{pid}",
        "power_watts": watts,
        "form_factor": "ATX",
        "price_usd_min": price,
    }


def mk_case(cid: int, ffs: list[str], price: float, *, max_gpu: int | None = None) -> dict:
    return {
        "id": cid,
        "model": f"Case-{cid}",
        "manufacturer": "NZXT",
        "sku": f"SKU-CS-{cid}",
        "supported_form_factors": ffs,
        "max_gpu_length_mm": max_gpu,
        "price_usd_min": price,
    }


def mk_cooler(kid: int, sockets: list[str], max_tdp: int, price: float) -> dict:
    return {
        "id": kid,
        "model": f"Cooler-{kid}",
        "manufacturer": "DeepCool",
        "sku": f"SKU-COOL-{kid}",
        "supported_sockets": sockets,
        "max_tdp_watts": max_tdp,
        "price_usd_min": price,
    }


# -----------------------------------------------------------------------------
# Собираем полный мок-мир
# -----------------------------------------------------------------------------

class MockWorld:
    """Набор кандидатов для всех категорий + управление транзитом."""

    def __init__(self):
        self.cpus: list[dict] = []
        self.motherboards: list[dict] = []
        self.rams: list[dict] = []
        self.gpus: list[dict] = []
        self.storages: list[dict] = []
        self.psus: list[dict] = []
        self.cases: list[dict] = []
        self.coolers: list[dict] = []
        # Какие component_id доступны только из транзита
        self.transit_only: set[tuple[str, int]] = set()

    def _filter_stock(self, cat: str, rows: list[dict], allow_transit: bool) -> list[dict]:
        if allow_transit:
            return rows
        return [r for r in rows if (cat, r["id"]) not in self.transit_only]


@pytest.fixture
def world(monkeypatch) -> MockWorld:
    w = MockWorld()

    # Мок курса — чтобы не лазить в интернет
    monkeypatch.setattr(S, "get_usd_rub_rate", lambda: (100.0, "fallback"))

    # Подменяем SessionLocal фиктивной сессией (её методы никто не зовёт)
    class _FakeSession:
        def close(self):
            pass
        def execute(self, *a, **kw):
            raise RuntimeError("FakeSession.execute не должен вызываться в этих тестах")
    monkeypatch.setattr(S, "SessionLocal", lambda: _FakeSession())

    # --- кандидаты -----------------------------------------------------------

    def fake_cpu_candidates(session, *, req, manufacturer, only_with_igpu,
                            usd_rub, allow_transit):
        rows = [
            c for c in w.cpus
            if (
                (manufacturer == "Intel" and c["manufacturer"].lower().startswith("intel"))
                or (manufacturer == "AMD" and c["manufacturer"] == "AMD")
            )
            and (not only_with_igpu or c["has_integrated_graphics"] is True)
        ]
        # простая фильтрация по минимумам
        if req.cpu.min_cores:
            rows = [c for c in rows if (c.get("cores") or 0) >= req.cpu.min_cores]
        if req.cpu.min_threads:
            rows = [c for c in rows if (c.get("threads") or 0) >= req.cpu.min_threads]
        if req.cpu.min_base_ghz:
            rows = [c for c in rows if (c.get("base_clock_ghz") or 0) >= req.cpu.min_base_ghz]
        # фикс
        fixed = req.cpu.fixed
        if fixed and fixed.is_set():
            if fixed.id is not None:
                rows = [c for c in w.cpus if c["id"] == fixed.id]
            elif fixed.sku:
                rows = [c for c in w.cpus if c["sku"] == fixed.sku]
        rows = w._filter_stock("cpu", rows, allow_transit)
        rows.sort(key=lambda c: c["price_usd_min"])
        return rows

    def fake_cheapest_mb(session, *, cpu_socket, fixed, usd_rub, allow_transit):
        rows = w.motherboards
        if fixed and fixed.is_set():
            rows = [m for m in rows if m["id"] == fixed.id or m["sku"] == fixed.sku]
        else:
            rows = [m for m in rows if m["socket"] == cpu_socket]
        rows = w._filter_stock("motherboard", rows, allow_transit)
        if not rows:
            return None
        return sorted(rows, key=lambda m: m["price_usd_min"])[0]

    def fake_ram_candidates(session, *, memory_type, min_frequency_mhz,
                            usd_rub, allow_transit):
        rows = [
            r for r in w.rams
            if r["memory_type"] == memory_type
            and r["form_factor"] == "DIMM"
            and (not min_frequency_mhz or r["frequency_mhz"] >= min_frequency_mhz)
        ]
        rows = w._filter_stock("ram", rows, allow_transit)
        rows.sort(key=lambda r: r["price_usd_min"])
        return rows

    def fake_cheapest_gpu(session, *, min_vram_gb, fixed, usd_rub, allow_transit):
        if fixed and fixed.is_set():
            rows = [g for g in w.gpus if g["id"] == fixed.id or g["sku"] == fixed.sku]
        else:
            rows = [g for g in w.gpus if not min_vram_gb or g["vram_gb"] >= min_vram_gb]
        rows = w._filter_stock("gpu", rows, allow_transit)
        if not rows:
            return None
        return sorted(rows, key=lambda g: g["price_usd_min"])[0]

    def fake_cheapest_storage(session, *, req, usd_rub, allow_transit):
        rows = [
            s for s in w.storages
            if (not req.min_gb or s["capacity_gb"] >= req.min_gb)
            and (not req.preferred_type or s["storage_type"] == req.preferred_type)
        ]
        rows = w._filter_stock("storage", rows, allow_transit)
        if not rows:
            return None
        return sorted(rows, key=lambda s: s["price_usd_min"])[0]

    def fake_cheapest_psu(session, *, fixed, usd_rub, allow_transit):
        if fixed and fixed.is_set():
            rows = [p for p in w.psus if p["id"] == fixed.id or p["sku"] == fixed.sku]
        else:
            rows = list(w.psus)
        rows = w._filter_stock("psu", rows, allow_transit)
        if not rows:
            return None
        return sorted(rows, key=lambda p: p["price_usd_min"])[0]

    def fake_cheapest_case(session, *, mb_form_factor, fixed, usd_rub, allow_transit):
        if fixed and fixed.is_set():
            rows = [c for c in w.cases if c["id"] == fixed.id or c["sku"] == fixed.sku]
        else:
            rows = [c for c in w.cases if mb_form_factor in c["supported_form_factors"]]
        rows = w._filter_stock("case", rows, allow_transit)
        if not rows:
            return None
        return sorted(rows, key=lambda c: c["price_usd_min"])[0]

    def fake_cheapest_cooler(session, *, cpu_socket, required_tdp, fixed,
                             usd_rub, allow_transit):
        if fixed and fixed.is_set():
            rows = [k for k in w.coolers if k["id"] == fixed.id or k["sku"] == fixed.sku]
        else:
            rows = [
                k for k in w.coolers
                if cpu_socket in (k["supported_sockets"] or [])
                and k["max_tdp_watts"] is not None
                and k["max_tdp_watts"] >= required_tdp
            ]
        rows = w._filter_stock("cooler", rows, allow_transit)
        if not rows:
            return None
        return sorted(rows, key=lambda k: k["price_usd_min"])[0]

    # подмена всего слоя candidates
    from app.services.configurator import builder as B
    monkeypatch.setattr(B.C, "get_cpu_candidates",     fake_cpu_candidates)
    monkeypatch.setattr(B.C, "get_cheapest_motherboard", fake_cheapest_mb)
    monkeypatch.setattr(B.C, "get_ram_candidates",     fake_ram_candidates)
    monkeypatch.setattr(B.C, "get_cheapest_gpu",       fake_cheapest_gpu)
    monkeypatch.setattr(B.C, "get_cheapest_storage",   fake_cheapest_storage)
    monkeypatch.setattr(B.C, "get_cheapest_psu",       fake_cheapest_psu)
    monkeypatch.setattr(B.C, "get_cheapest_case",      fake_cheapest_case)
    monkeypatch.setattr(B.C, "get_cheapest_cooler",    fake_cheapest_cooler)
    # selector вызывает C напрямую для cpu-кандидатов
    monkeypatch.setattr(S.C, "get_cpu_candidates", fake_cpu_candidates)

    # --- предложения поставщиков ---------------------------------------------

    def fake_fetch_offers(session, *, category, component_id, usd_rub, allow_transit):
        # Одно предложение — цена берётся из нашего мока. Если компонент
        # помечен как transit_only и allow_transit=False — никто его не выберет,
        # но если выбрали, значит allow_transit=True.
        row = None
        for storage in (w.cpus, w.motherboards, w.rams, w.gpus, w.storages,
                        w.psus, w.cases, w.coolers):
            for r in storage:
                if r["id"] == component_id:
                    row = r
                    break
            if row:
                break
        if row is None:
            return []
        price_usd = float(row["price_usd_min"])
        in_transit = (category, component_id) in w.transit_only
        if in_transit and not allow_transit:
            return []
        return [SupplierOffer(
            supplier="OCS",
            price_usd=round(price_usd, 2),
            price_rub=round(price_usd * usd_rub, 2),
            stock=(0 if in_transit else 10),
            in_transit=in_transit,
        )]
    monkeypatch.setattr(S, "fetch_offers", fake_fetch_offers)

    return w


# -----------------------------------------------------------------------------
# Сценарии подбора
# -----------------------------------------------------------------------------

def _std_common(world: MockWorld):
    """Стандартный набор компонентов, общий для большинства сценариев."""
    world.motherboards += [
        mk_mb(101, "AM5", "ATX", "DDR5", 150, slots=4),
        mk_mb(102, "LGA1700", "ATX", "DDR5", 120, slots=4),
    ]
    world.rams += [
        mk_ram(201, "DDR5", 16, 5200, 40),
        mk_ram(202, "DDR5", 8, 5200, 22),
        mk_ram(203, "DDR4", 16, 3200, 35),
    ]
    world.gpus += [
        mk_gpu(301, vram=8, price=250, length=280),
        mk_gpu(302, vram=12, price=500, length=300),
    ]
    world.storages += [
        mk_storage(401, 500, "SSD", 45),
        mk_storage(402, 1000, "SSD", 80),
    ]
    world.psus += [
        mk_psu(501, 650, 55),
    ]
    world.cases += [
        mk_case(601, ["ATX", "mATX"], 60, max_gpu=380),
    ]
    world.coolers += [
        mk_cooler(701, ["AM5", "LGA1700"], 200, 25),
    ]


# -- Тест 1. GPU требуется → Intel вариант --
def test_gpu_required_intel_only(world):
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 200))
    _std_common(world)

    req = request_from_dict({"gpu": {"required": True}})
    result = S.build_config(req)

    assert result.status in ("ok", "partial")
    assert len(result.variants) == 1
    assert result.variants[0].manufacturer == "Intel"
    # GPU обязательна → путь default
    assert result.variants[0].path_used == "default"
    cats = [c.category for c in result.variants[0].components]
    assert "gpu" in cats


# -- Тест 2. GPU требуется → AMD вариант --
def test_gpu_required_amd_only(world):
    world.cpus.append(mk_cpu(2, "AMD", "AM5", 180))
    _std_common(world)

    req = request_from_dict({"gpu": {"required": True}})
    result = S.build_config(req)

    assert result.status in ("ok", "partial")
    assert len(result.variants) == 1
    assert result.variants[0].manufacturer == "AMD"


# -- Тест 3. GPU требуется → оба --
def test_gpu_required_both(world):
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 200))
    world.cpus.append(mk_cpu(2, "AMD", "AM5", 180))
    _std_common(world)

    req = request_from_dict({"gpu": {"required": True}})
    result = S.build_config(req)
    assert result.status == "ok"
    labels = sorted(v.manufacturer for v in result.variants)
    assert labels == ["AMD", "Intel"]


# -- Тест 4. GPU не требуется → Путь A выигрывает --
def test_gpu_optional_path_a_wins(world):
    # CPU с iGPU дёшев, дискретная GPU дорогая → путь A (без GPU) должен выиграть
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 150, igpu=True))
    _std_common(world)

    req = request_from_dict({})    # пустые требования по GPU
    # но пустой запрос считается отказом — добавим минимальное требование
    req = request_from_dict({"cpu": {"min_cores": 4}})
    result = S.build_config(req)

    assert len(result.variants) >= 1
    intel = next((v for v in result.variants if v.manufacturer == "Intel"), None)
    assert intel is not None
    assert intel.path_used == "A"
    cats = [c.category for c in intel.components]
    assert "gpu" not in cats


# -- Тест 5. GPU не требуется → Путь B выигрывает --
def test_gpu_optional_path_b_wins(world):
    # CPU без iGPU ОЧЕНЬ дёшев, дискретная GPU тоже дёшева → путь B
    # CPU с iGPU вообще нет в наличии
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 50, igpu=False))
    _std_common(world)
    # Пусть будет дешёвая GPU
    world.gpus.append(mk_gpu(303, vram=4, price=60, length=180))

    req = request_from_dict({"cpu": {"min_cores": 4}})
    result = S.build_config(req)

    intel = next((v for v in result.variants if v.manufacturer == "Intel"), None)
    assert intel is not None
    # Путь B — потому что iGPU нет, дискретная обязательна
    assert intel.path_used == "B"
    cats = [c.category for c in intel.components]
    assert "gpu" in cats


# -- Тест 6. Железный инвариант: CPU без iGPU всегда с GPU --
def test_iron_invariant_cpu_without_igpu_without_gpu_refused(world):
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 100, igpu=False))
    _std_common(world)
    # Убираем все GPU
    world.gpus.clear()

    req = request_from_dict({"cpu": {"min_cores": 4}})
    result = S.build_config(req)

    # Ни одна сборка не валидна — без iGPU и без GPU нельзя
    assert result.status == "failed"
    assert result.refusal_reason is not None


# -- Тест 7. BOX vs OEM: для BOX не добавляется кулер --
def test_box_cpu_no_cooler_in_build(world):
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 150,
                             package="BOX", igpu=True))
    _std_common(world)

    req = request_from_dict({"cpu": {"min_cores": 4}})
    result = S.build_config(req)
    assert len(result.variants) >= 1
    variant = result.variants[0]
    cats = [c.category for c in variant.components]
    assert "cooler" not in cats


# -- Тест 8. RAM: один модуль 16 ГБ дешевле двух по 8 ГБ --
def test_ram_single_16gb_cheaper(world):
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 100, igpu=True))
    _std_common(world)
    # В _std_common: RAM 16GB = $40, RAM 8GB = $22 (2×8=$44 дороже 1×16).
    # Удалим DDR4 чтобы не путать.

    req = request_from_dict({
        "cpu": {"min_cores": 4},
        "ram": {"min_gb": 16, "memory_type": "DDR5"},
    })
    result = S.build_config(req)
    assert len(result.variants) >= 1
    ram_choice = next(c for c in result.variants[0].components if c.category == "ram")
    # должен быть выбран модуль 16GB, quantity=1
    assert ram_choice.quantity == 1


# -- Тест 9. RAM: два модуля по 8 ГБ дешевле одного 16 ГБ --
def test_ram_two_8gb_cheaper(world):
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 100, igpu=True))
    # Специально: модуль 16 стоит $100, модуль 8 — $30 (2×$30 = $60 < $100)
    world.motherboards += [mk_mb(101, "LGA1700", "ATX", "DDR5", 120, slots=4)]
    world.rams += [
        mk_ram(201, "DDR5", 16, 5200, 100),
        mk_ram(202, "DDR5", 8, 5200, 30),
    ]
    world.storages += [mk_storage(401, 500, "SSD", 45)]
    world.psus += [mk_psu(501, 650, 55)]
    world.cases += [mk_case(601, ["ATX", "mATX"], 60, max_gpu=380)]
    world.coolers += [mk_cooler(701, ["LGA1700"], 200, 25)]

    req = request_from_dict({
        "cpu": {"min_cores": 4},
        "ram": {"min_gb": 16, "memory_type": "DDR5"},
    })
    result = S.build_config(req)

    ram_choice = next(c for c in result.variants[0].components if c.category == "ram")
    assert ram_choice.quantity == 2
    assert ram_choice.component_id == 202


# -- Тест 10. Зафиксирован CPU по ID --
def test_fixed_cpu_by_id(world):
    world.cpus += [
        mk_cpu(1, "Intel Corporation", "LGA1700", 100, igpu=True),
        mk_cpu(2, "Intel Corporation", "LGA1700", 500, igpu=True),
    ]
    _std_common(world)

    req = request_from_dict({
        "cpu": {"fixed_id": 2},
    })
    result = S.build_config(req)
    cpu_choice = next(c for c in result.variants[0].components if c.category == "cpu")
    assert cpu_choice.component_id == 2


# -- Тест 11. Зафиксирован CPU по SKU --
def test_fixed_cpu_by_sku(world):
    world.cpus += [
        mk_cpu(1, "AMD", "AM5", 100, igpu=True),
        mk_cpu(2, "AMD", "AM5", 300, igpu=True),
    ]
    _std_common(world)

    req = request_from_dict({
        "cpu": {"fixed_sku": "SKU-CPU-2"},
    })
    result = S.build_config(req)
    cpu_choice = next(c for c in result.variants[0].components if c.category == "cpu")
    assert cpu_choice.component_id == 2


# -- Тест 12. Отказ с причиной (бюджета не хватает) --
def test_refusal_by_budget(world):
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 200, igpu=True))
    _std_common(world)

    # Бюджет заведомо меньше любой сборки
    req = request_from_dict({
        "cpu": {"min_cores": 4},
        "budget_usd": 50,
    })
    result = S.build_config(req)
    assert result.status == "failed"
    assert result.refusal_reason is not None


# -- Тест 13. Отказ с причиной (нет CPU по требованиям) --
def test_refusal_no_cpu_matches(world):
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 100, cores=4))
    _std_common(world)

    req = request_from_dict({"cpu": {"min_cores": 16}})
    result = S.build_config(req)
    assert result.status == "failed"
    assert "intel" in (result.refusal_reason or {})
    assert "amd" in (result.refusal_reason or {})


# -- Тест 14. Транзит как fallback --
def test_transit_fallback(world):
    # Единственный подходящий CPU доступен только в транзите
    world.cpus.append(mk_cpu(1, "AMD", "AM5", 150, igpu=True))
    world.transit_only.add(("cpu", 1))
    _std_common(world)

    req = request_from_dict({"cpu": {"min_cores": 4}})
    result = S.build_config(req)

    assert result.status in ("ok", "partial")
    assert len(result.variants) >= 1
    amd = next(v for v in result.variants if v.manufacturer == "AMD")
    assert amd.used_transit is True
    # И соответствующее предупреждение
    assert any("транзит" in w.lower() for w in amd.warnings)


# -- Тест 15. Все предупреждения корректно --
def test_all_warnings_generated(world):
    # MB без memory_slots, GPU с needs_extra_power=True, GPU.length_mm=None
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 100, igpu=False))
    world.motherboards.append(
        mk_mb(101, "LGA1700", "ATX", "DDR5", 120, slots=None)
    )
    world.rams.append(mk_ram(201, "DDR5", 16, 5200, 40))
    world.gpus.append(mk_gpu(301, vram=8, price=200, length=None, needs_power=True))
    world.storages.append(mk_storage(401, 500, "SSD", 45))
    world.psus.append(mk_psu(501, 650, 55))
    world.cases.append(mk_case(601, ["ATX", "mATX"], 60, max_gpu=None))
    world.coolers.append(mk_cooler(701, ["LGA1700"], 200, 25))

    req = request_from_dict({"cpu": {"min_cores": 4}, "gpu": {"required": True}})
    result = S.build_config(req)
    v = next(v for v in result.variants if v.manufacturer == "Intel")
    joined = " | ".join(v.warnings).lower()
    assert "слотов" in joined
    assert "длин" in joined
    assert "бп" in joined
    assert "разъём" in joined or "разъем" in joined


# -- Тест 16. Пустой запрос → вежливый отказ --
def test_empty_request_refused(world):
    # даже не добавляем никаких CPU — мы не должны дойти до подбора
    req = request_from_dict({})
    result = S.build_config(req)
    assert result.status == "failed"
    assert result.refusal_reason is not None
    assert "request" in (result.refusal_reason or {})


# -- Тест 17. Валютная конвертация: проверяем поле price_rub --
def test_currency_conversion(world):
    world.cpus.append(mk_cpu(1, "Intel Corporation", "LGA1700", 100, igpu=True))
    _std_common(world)

    req = request_from_dict({"cpu": {"min_cores": 4}})
    result = S.build_config(req)
    # курс в mock-фикстуре = 100.0
    v = result.variants[0]
    for c in v.components:
        assert abs(c.chosen.price_rub - c.chosen.price_usd * 100.0) < 0.02
