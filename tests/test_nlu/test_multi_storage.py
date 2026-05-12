# Тесты multi-storage NLU (backlog #7).
#
# Проверяют, что несколько накопителей в запросе («SSD 512 ГБ и HDD 2 ТБ»)
# корректно проходят весь NLU-конвейер: валидация ответа парсера →
# overrides.storages → BuildRequest.storages. Также есть regression-тесты
# с одиночным storage'ом, чтобы не сломать существующее поведение.

from __future__ import annotations

import json

import pytest

from portal.services.configurator.nlu import parser as parser_mod
from portal.services.configurator.nlu import request_builder
from portal.services.configurator.nlu.schema import ParsedRequest

from .conftest import fake_openai_client


# -----------------------------------------------------------------------------
# validate_response — структура ответа парсера
# -----------------------------------------------------------------------------

class TestValidateOverridesStorages:
    def test_two_storages_ssd_plus_hdd(self):
        p = parser_mod.validate_response({
            "is_empty": False,
            "purpose": "home",
            "overrides": {
                "storages": [
                    {"min_gb": 512,  "type": "SSD"},
                    {"min_gb": 2000, "type": "HDD"},
                ],
            },
            "model_mentions": [],
            "clarifying_questions": [],
            "raw_summary": "Понял запрос: SSD 512 ГБ + HDD 2 ТБ",
        })
        assert p.overrides.get("storages") == [
            {"min_gb": 512,  "preferred_type": "SSD"},
            {"min_gb": 2000, "preferred_type": "HDD"},
        ]

    def test_two_nvme_same_type(self):
        # «Два NVMe по 1 ТБ» — два одинаковых требования
        p = parser_mod.validate_response({
            "is_empty": False,
            "purpose": "workstation",
            "overrides": {
                "storages": [
                    {"min_gb": 1000, "type": "SSD"},
                    {"min_gb": 1000, "type": "SSD"},
                ],
            },
            "model_mentions": [],
            "clarifying_questions": [],
            "raw_summary": "",
        })
        assert len(p.overrides["storages"]) == 2
        assert all(s["preferred_type"] == "SSD" for s in p.overrides["storages"])

    def test_only_single_storage_no_storages_key(self):
        # Regression: одиночный запрос storage по-прежнему работает по
        # старому пути через storage_min_gb / storage_type.
        p = parser_mod.validate_response({
            "is_empty": False,
            "purpose": "office",
            "overrides": {
                "storage_min_gb": 240,
                "storage_type":   "SSD",
            },
            "model_mentions": [],
            "clarifying_questions": [],
            "raw_summary": "",
        })
        assert "storages" not in p.overrides
        assert p.overrides["storage_min_gb"] == 240
        assert p.overrides["storage_type"] == "SSD"

    def test_storages_with_unknown_type_drops_type(self):
        # Битый type («M.2X») просто отбрасывается — но min_gb сохраняется
        p = parser_mod.validate_response({
            "is_empty": False,
            "purpose": "home",
            "overrides": {
                "storages": [
                    {"min_gb": 500, "type": "M.2X"},
                ],
            },
            "model_mentions": [],
            "clarifying_questions": [],
            "raw_summary": "",
        })
        assert p.overrides["storages"] == [{"min_gb": 500}]

    def test_storages_must_be_array(self):
        with pytest.raises(parser_mod.ParseValidationError):
            parser_mod.validate_response({
                "is_empty": False,
                "purpose": "home",
                "overrides": {"storages": "не массив"},
                "model_mentions": [],
                "clarifying_questions": [],
                "raw_summary": "",
            })

    def test_storages_negative_min_gb_rejected(self):
        with pytest.raises(parser_mod.ParseValidationError):
            parser_mod.validate_response({
                "is_empty": False,
                "purpose": "home",
                "overrides": {
                    "storages": [{"min_gb": -1, "type": "SSD"}],
                },
                "model_mentions": [],
                "clarifying_questions": [],
                "raw_summary": "",
            })


# -----------------------------------------------------------------------------
# parse() — с моком OpenAI: сквозной парсинг ответа модели
# -----------------------------------------------------------------------------

class TestParseEndToEnd:
    def test_parse_response_with_storages(self):
        payload = {
            "is_empty": False,
            "purpose": "home",
            "budget_usd": None,
            "cpu_manufacturer": None,
            "overrides": {
                "storages": [
                    {"min_gb": 512,  "type": "SSD"},
                    {"min_gb": 2000, "type": "HDD"},
                ],
            },
            "model_mentions": [],
            "clarifying_questions": [],
            "raw_summary": "Понял запрос: SSD 512 + HDD 2TB",
        }
        cli = fake_openai_client(json.dumps(payload))
        out = parser_mod.parse(
            "ПК с SSD 512 ГБ и HDD 2 ТБ",
            usd_rub_rate=90.0,
            client=cli,
        )
        assert out.parse_error is None
        storages = out.parsed.overrides.get("storages")
        assert storages is not None
        assert len(storages) == 2
        assert storages[0] == {"min_gb": 512,  "preferred_type": "SSD"}
        assert storages[1] == {"min_gb": 2000, "preferred_type": "HDD"}


# -----------------------------------------------------------------------------
# request_builder — превращение overrides.storages в BuildRequest.storages
# -----------------------------------------------------------------------------

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


class TestRequestBuilderStorages:
    def test_overrides_storages_propagate_to_request(self):
        # «SSD 512 + HDD 2ТБ» → req.storages == [SSD/512, HDD/2000]
        parsed = _parsed(
            purpose=None,    # без профиля — чисто overrides
            overrides={
                "storages": [
                    {"min_gb": 512,  "preferred_type": "SSD"},
                    {"min_gb": 2000, "preferred_type": "HDD"},
                ],
            },
        )
        req = request_builder.build(parsed)
        assert len(req.storages) == 2
        assert req.storages[0].min_gb == 512
        assert req.storages[0].preferred_type == "SSD"
        assert req.storages[1].min_gb == 2000
        assert req.storages[1].preferred_type == "HDD"

    def test_single_storage_via_overrides_keeps_old_path(self):
        # Regression: одиночный storage через storage_min_gb/storage_type
        # → req.storage заполнен, req.storages пустой.
        parsed = _parsed(
            overrides={"storage_min_gb": 500, "storage_type": "SSD"},
        )
        req = request_builder.build(parsed)
        assert req.storage.min_gb == 500
        assert req.storage.preferred_type == "SSD"
        assert req.storages == []

    def test_effective_storages_prefers_list_over_singleton(self):
        # Если есть и одиночный, и список — effective_storages возвращает
        # список (он приоритетнее).
        parsed = _parsed(
            purpose="home",   # профиль выставит singleton storage_min_gb=500
            overrides={
                "storages": [
                    {"min_gb": 1000, "preferred_type": "SSD"},
                    {"min_gb": 4000, "preferred_type": "HDD"},
                ],
            },
        )
        req = request_builder.build(parsed)
        eff = req.effective_storages()
        assert len(eff) == 2
        assert eff[0].min_gb == 1000
        assert eff[0].preferred_type == "SSD"
        assert eff[1].min_gb == 4000
        assert eff[1].preferred_type == "HDD"
