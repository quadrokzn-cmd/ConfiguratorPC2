# Юнит-тесты правил совместимости (не требуют БД).

from portal.services.configurator.compatibility.rules import (
    check_build,
    cooler_cpu,
    cpu_mb_socket,
    gpu_case_length,
    iron_invariant_gpu,
    mb_case_form_factor,
    mb_ram_match,
    required_cooler_tdp,
)


# -----------------------------------------------------------------------------
# CPU ↔ MB: сокет
# -----------------------------------------------------------------------------

class TestCpuMbSocket:
    def test_same_socket(self):
        assert cpu_mb_socket({"socket": "AM5"}, {"socket": "AM5"}) is True

    def test_different_socket(self):
        assert cpu_mb_socket({"socket": "AM5"}, {"socket": "LGA1700"}) is False

    def test_null_socket(self):
        assert cpu_mb_socket({"socket": None}, {"socket": "AM5"}) is False
        assert cpu_mb_socket({"socket": "AM5"}, {"socket": None}) is False


# -----------------------------------------------------------------------------
# MB ↔ RAM
# -----------------------------------------------------------------------------

class TestMbRamMatch:
    def test_type_mismatch(self):
        mb = {"memory_type": "DDR5"}
        ram = {"memory_type": "DDR4", "form_factor": "DIMM"}
        assert mb_ram_match(mb, ram) is False

    def test_type_match_dimm(self):
        mb = {"memory_type": "DDR5"}
        ram = {"memory_type": "DDR5", "form_factor": "DIMM"}
        assert mb_ram_match(mb, ram) is True

    def test_sodimm_rejected(self):
        mb = {"memory_type": "DDR5"}
        ram = {"memory_type": "DDR5", "form_factor": "SO-DIMM"}
        assert mb_ram_match(mb, ram) is False

    def test_null_form_factor(self):
        mb = {"memory_type": "DDR5"}
        ram = {"memory_type": "DDR5", "form_factor": None}
        assert mb_ram_match(mb, ram) is False


# -----------------------------------------------------------------------------
# MB ↔ Case: форм-фактор
# -----------------------------------------------------------------------------

class TestMbCaseFormFactor:
    def test_atx_in_supported(self):
        mb = {"form_factor": "ATX"}
        case = {"supported_form_factors": ["ATX", "mATX"]}
        assert mb_case_form_factor(mb, case) is True

    def test_atx_not_supported(self):
        mb = {"form_factor": "ATX"}
        case = {"supported_form_factors": ["mATX", "ITX"]}
        assert mb_case_form_factor(mb, case) is False

    def test_empty_supported(self):
        mb = {"form_factor": "ATX"}
        case = {"supported_form_factors": None}
        assert mb_case_form_factor(mb, case) is False


# -----------------------------------------------------------------------------
# Cooler ↔ CPU: сокет + запас TDP 30%
# -----------------------------------------------------------------------------

class TestCoolerCpu:
    def test_boundary_exact(self):
        # CPU 100W → требуется 130W. Ровно 130W — проходит.
        cpu = {"socket": "AM5", "tdp_watts": 100}
        cooler = {"supported_sockets": ["AM5"], "max_tdp_watts": 130}
        assert cooler_cpu(cooler, cpu) is True

    def test_boundary_one_below(self):
        # 129W при требовании 130W — не проходит.
        cpu = {"socket": "AM5", "tdp_watts": 100}
        cooler = {"supported_sockets": ["AM5"], "max_tdp_watts": 129}
        assert cooler_cpu(cooler, cpu) is False

    def test_socket_not_supported(self):
        cpu = {"socket": "AM5", "tdp_watts": 65}
        cooler = {"supported_sockets": ["LGA1700"], "max_tdp_watts": 200}
        assert cooler_cpu(cooler, cpu) is False

    def test_null_max_tdp(self):
        cpu = {"socket": "AM5", "tdp_watts": 65}
        cooler = {"supported_sockets": ["AM5"], "max_tdp_watts": None}
        assert cooler_cpu(cooler, cpu) is False

    def test_null_sockets(self):
        cpu = {"socket": "AM5", "tdp_watts": 65}
        cooler = {"supported_sockets": None, "max_tdp_watts": 200}
        assert cooler_cpu(cooler, cpu) is False

    def test_required_tdp_calculation(self):
        # 100W * 1.30 = 130W
        assert required_cooler_tdp({"tdp_watts": 100}) == 130
        # 65W * 1.30 = 84.5 → 84 (banker's rounding)
        assert required_cooler_tdp({"tdp_watts": 65}) in (84, 85)
        # 200W * 1.30 = 260
        assert required_cooler_tdp({"tdp_watts": 200}) == 260

    def test_required_tdp_none_when_cpu_tdp_null(self):
        assert required_cooler_tdp({"tdp_watts": None}) is None


# -----------------------------------------------------------------------------
# GPU ↔ Case: длина
# -----------------------------------------------------------------------------

class TestGpuCaseLength:
    def test_both_known_fits(self):
        gpu = {"length_mm": 300}
        case = {"max_gpu_length_mm": 380}
        res = gpu_case_length(gpu, case)
        assert res.ok is True
        assert res.warning is None
        assert res.reason is None

    def test_both_known_too_long(self):
        gpu = {"length_mm": 400}
        case = {"max_gpu_length_mm": 380}
        res = gpu_case_length(gpu, case)
        assert res.ok is False
        assert "400" in (res.reason or "")

    def test_gpu_length_null_skips_with_warning(self):
        gpu = {"length_mm": None}
        case = {"max_gpu_length_mm": 380}
        res = gpu_case_length(gpu, case)
        assert res.ok is True
        assert res.warning is not None

    def test_case_max_null_skips_with_warning(self):
        gpu = {"length_mm": 300}
        case = {"max_gpu_length_mm": None}
        res = gpu_case_length(gpu, case)
        assert res.ok is True
        assert res.warning is not None

    def test_no_gpu_always_ok(self):
        res = gpu_case_length(None, {"max_gpu_length_mm": 300})
        assert res.ok is True


# -----------------------------------------------------------------------------
# Железный инвариант GPU
# -----------------------------------------------------------------------------

class TestIronInvariantGpu:
    def test_no_igpu_no_gpu_fails(self):
        cpu = {"has_integrated_graphics": False}
        res = iron_invariant_gpu(cpu, None)
        assert res.ok is False

    def test_no_igpu_with_gpu_ok(self):
        cpu = {"has_integrated_graphics": False}
        res = iron_invariant_gpu(cpu, {"vram_gb": 8})
        assert res.ok is True

    def test_igpu_no_gpu_ok(self):
        cpu = {"has_integrated_graphics": True}
        res = iron_invariant_gpu(cpu, None)
        assert res.ok is True

    def test_igpu_with_gpu_ok(self):
        cpu = {"has_integrated_graphics": True}
        res = iron_invariant_gpu(cpu, {"vram_gb": 8})
        assert res.ok is True

    def test_igpu_null_no_gpu_fails(self):
        # Если поле has_integrated_graphics не заполнено — перестраховываемся.
        cpu = {"has_integrated_graphics": None}
        res = iron_invariant_gpu(cpu, None)
        assert res.ok is False


# -----------------------------------------------------------------------------
# Композиционная check_build
# -----------------------------------------------------------------------------

class TestCheckBuild:
    def _base_build(self, **overrides) -> dict:
        b = {
            "cpu": {
                "socket": "AM5",
                "tdp_watts": 65,
                "has_integrated_graphics": True,
            },
            "motherboard": {
                "socket": "AM5",
                "form_factor": "mATX",
                "memory_type": "DDR5",
            },
            "ram": {"memory_type": "DDR5", "form_factor": "DIMM"},
            "gpu": None,
            "case": {
                "supported_form_factors": ["ATX", "mATX"],
                "max_gpu_length_mm": 380,
            },
            "cooler": {
                "supported_sockets": ["AM5"],
                "max_tdp_watts": 150,
            },
        }
        b.update(overrides)
        return b

    def test_happy_path(self):
        errors, warnings = check_build(self._base_build())
        assert errors == []
        assert warnings == []

    def test_socket_mismatch(self):
        b = self._base_build(
            motherboard={"socket": "LGA1700", "form_factor": "mATX", "memory_type": "DDR5"},
        )
        errors, _ = check_build(b)
        assert any("сокет" in e.lower() for e in errors)

    def test_ram_ddr4_on_ddr5_mb(self):
        b = self._base_build(ram={"memory_type": "DDR4", "form_factor": "DIMM"})
        errors, _ = check_build(b)
        assert any("памят" in e.lower() for e in errors)

    def test_case_ff_mismatch(self):
        b = self._base_build(
            case={"supported_form_factors": ["ITX"], "max_gpu_length_mm": 380},
        )
        errors, _ = check_build(b)
        assert any("форм-фактор" in e.lower() for e in errors)

    def test_iron_invariant_violation(self):
        b = self._base_build()
        b["cpu"]["has_integrated_graphics"] = False
        b["gpu"] = None
        errors, _ = check_build(b)
        assert any("изображени" in e.lower() for e in errors)

    def test_gpu_length_warning_when_null(self):
        b = self._base_build()
        b["gpu"] = {"length_mm": None}
        b["case"]["max_gpu_length_mm"] = None
        errors, warnings = check_build(b)
        assert errors == []
        assert any("длин" in w.lower() for w in warnings)
