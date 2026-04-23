# Тесты сборки BuildRequest из ParsedRequest + профиля + найденных моделей.
#
# Эти тесты НЕ ходят в БД, не дёргают OpenAI и не вызывают подбор —
# проверяют только превращение «понимания запроса» в структуру для
# модуля configurator.

from __future__ import annotations

from app.services.nlu import request_builder
from app.services.nlu.schema import ModelMention, ParsedRequest, ResolvedMention


def _parsed(**kw) -> ParsedRequest:
    base = dict(
        is_empty=False,
        purpose=None,
        budget_usd=None,
        cpu_manufacturer=None,
        overrides={},
        model_mentions=[],
        clarifying_questions=[],
        raw_summary="",
    )
    base.update(kw)
    return ParsedRequest(**base)


class TestBuildFromProfile:
    def test_office_defaults(self):
        req = request_builder.build(_parsed(purpose="office"))
        assert req.cpu.min_cores == 2
        assert req.ram.min_gb == 8
        assert req.ram.min_frequency_mhz == 2666
        assert req.storage.min_gb == 240
        assert req.storage.preferred_type == "SSD"
        assert req.gpu.required is False

    def test_gaming_defaults_with_budget(self):
        req = request_builder.build(_parsed(
            purpose="gaming",
            budget_usd=1111,
        ))
        assert req.cpu.min_cores == 6
        assert req.cpu.min_threads == 12
        assert req.gpu.required is True
        assert req.gpu.min_vram_gb == 8
        assert req.budget_usd == 1111

    def test_workstation_defaults(self):
        req = request_builder.build(_parsed(purpose="workstation"))
        assert req.cpu.min_cores == 8
        assert req.cpu.min_threads == 16
        assert req.ram.min_gb == 32
        assert req.storage.min_gb == 1000


class TestOverrides:
    def test_overrides_replace_profile(self):
        # OFFICE по дефолту 8GB, но менеджер просит 16
        req = request_builder.build(_parsed(
            purpose="office",
            overrides={"ram_min_gb": 16},
        ))
        assert req.ram.min_gb == 16
        # остальные поля профиля сохранились
        assert req.storage.min_gb == 240

    def test_office_with_discrete_gpu(self):
        # «Офисный ПК но с дискретной видеокартой 8GB»
        req = request_builder.build(_parsed(
            purpose="office",
            overrides={"gpu_required": True, "gpu_min_vram_gb": 8},
        ))
        assert req.gpu.required is True
        assert req.gpu.min_vram_gb == 8
        # OFFICE базовые поля по-прежнему на месте
        assert req.ram.min_gb == 8

    def test_ddr5_and_ssd(self):
        req = request_builder.build(_parsed(
            purpose="home",
            overrides={"ram_memory_type": "DDR5", "storage_type": "SSD"},
        ))
        assert req.ram.memory_type == "DDR5"
        assert req.storage.preferred_type == "SSD"

    def test_cpu_manufacturer_lowercased_for_selector(self):
        req = request_builder.build(_parsed(
            purpose="gaming",
            cpu_manufacturer="amd",
        ))
        assert req.cpu.manufacturer == "amd"


class TestResolvedMentions:
    def test_resolved_cpu_sets_fixed(self):
        parsed = _parsed(
            purpose="gaming",
            model_mentions=[ModelMention(category="cpu", query="Ryzen 5 7600")],
        )
        resolved = [
            ResolvedMention(
                mention=parsed.model_mentions[0],
                found_id=42, found_model="Ryzen 5 7600X OEM", found_sku="SKU-42",
            ),
        ]
        req = request_builder.build(parsed, resolved=resolved)
        assert req.cpu.fixed is not None
        assert req.cpu.fixed.id == 42
        assert req.cpu.fixed.sku == "SKU-42"

    def test_resolved_gpu_marks_required(self):
        parsed = _parsed(
            purpose="office",  # OFFICE по дефолту gpu_required=False
            model_mentions=[ModelMention(category="gpu", query="RTX 4060")],
        )
        resolved = [
            ResolvedMention(
                mention=parsed.model_mentions[0],
                found_id=99, found_model="RTX 4060 EVO",
            ),
        ]
        req = request_builder.build(parsed, resolved=resolved)
        assert req.gpu.fixed is not None
        assert req.gpu.fixed.id == 99
        # Если зафиксирована конкретная GPU — она обязательна
        assert req.gpu.required is True

    def test_unresolved_mention_ignored(self):
        # found_id=None → mention просто не применяется к req
        parsed = _parsed(
            purpose="gaming",
            model_mentions=[ModelMention(category="gpu", query="RTX 9999")],
        )
        resolved = [
            ResolvedMention(
                mention=parsed.model_mentions[0],
                found_id=None,
                note="не найдена",
            ),
        ]
        req = request_builder.build(parsed, resolved=resolved)
        assert req.gpu.fixed is None
        # gpu.required уже True из gaming-профиля
        assert req.gpu.required is True


class TestNoPurpose:
    def test_only_overrides_no_profile(self):
        # Нет purpose, есть только overrides — профиль не применяется
        req = request_builder.build(_parsed(
            overrides={"ram_min_gb": 32},
        ))
        assert req.ram.min_gb == 32
        # ничего лишнего из профилей не подмешалось
        assert req.cpu.min_cores is None
        assert req.storage.min_gb is None
