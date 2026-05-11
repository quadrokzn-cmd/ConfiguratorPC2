# Тесты OpenAI-парсера (без реальных вызовов).
#
# Покрытие:
#   - валидация структуры ответа (validate_response);
#   - fallback при невалидном JSON / нарушении схемы;
#   - корректный возврат стоимости и токенов;
#   - обработка пустого ввода без обращения к API.

from __future__ import annotations

import json

import pytest

from portal.services.configurator.nlu import parser as parser_mod
from portal.services.configurator.nlu.schema import ParsedRequest

from .conftest import fake_openai_client, make_openai_response


# -----------------------------------------------------------------------------
# validate_response — чистая валидация без OpenAI
# -----------------------------------------------------------------------------

class TestValidate:
    def test_minimal_empty(self):
        p = parser_mod.validate_response({
            "is_empty": True,
            "purpose": None,
            "budget_usd": None,
            "cpu_manufacturer": None,
            "overrides": {},
            "model_mentions": [],
            "clarifying_questions": ["А?", "Б?"],
            "raw_summary": "",
        })
        assert p.is_empty is True
        assert p.clarifying_questions == ["А?", "Б?"]
        assert p.purpose is None

    def test_full_office_with_overrides(self):
        p = parser_mod.validate_response({
            "is_empty": False,
            "purpose": "office",
            "budget_usd": 600,
            "cpu_manufacturer": None,
            "overrides": {"ram_min_gb": 16, "ram_memory_type": "DDR5"},
            "model_mentions": [],
            "clarifying_questions": [],
            "raw_summary": "Понял запрос: офисный ПК",
        })
        assert p.purpose == "office"
        assert p.budget_usd == 600
        assert p.overrides == {"ram_min_gb": 16, "ram_memory_type": "DDR5"}

    def test_invalid_purpose_rejected(self):
        with pytest.raises(parser_mod.ParseValidationError):
            parser_mod.validate_response({
                "is_empty": False,
                "purpose": "СУПЕР",
                "overrides": {},
                "model_mentions": [],
                "clarifying_questions": [],
                "raw_summary": "",
            })

    def test_unknown_storage_type_silently_dropped(self):
        # Если модель вернула экзотический тип — поле просто игнорируется.
        p = parser_mod.validate_response({
            "is_empty": False,
            "purpose": "office",
            "overrides": {"storage_type": "M.2X"},
            "model_mentions": [],
            "clarifying_questions": [],
            "raw_summary": "",
        })
        assert "storage_type" not in p.overrides

    def test_model_mention_unknown_category(self):
        with pytest.raises(parser_mod.ParseValidationError):
            parser_mod.validate_response({
                "is_empty": False,
                "purpose": "gaming",
                "overrides": {},
                "model_mentions": [{"category": "wifi", "query": "TP-Link"}],
                "clarifying_questions": [],
                "raw_summary": "",
            })

    def test_negative_budget_rejected(self):
        with pytest.raises(parser_mod.ParseValidationError):
            parser_mod.validate_response({
                "is_empty": False,
                "purpose": "office",
                "budget_usd": -5,
                "overrides": {},
                "model_mentions": [],
                "clarifying_questions": [],
                "raw_summary": "",
            })

    def test_cpu_manufacturer_intel(self):
        p = parser_mod.validate_response({
            "is_empty": False,
            "purpose": "gaming",
            "cpu_manufacturer": "intel",
            "overrides": {},
            "model_mentions": [],
            "clarifying_questions": [],
            "raw_summary": "",
        })
        assert p.cpu_manufacturer == "intel"


# -----------------------------------------------------------------------------
# parse() — с моком OpenAI-клиента
# -----------------------------------------------------------------------------

class TestParseWithMock:
    def test_empty_input_no_api_call(self, monkeypatch):
        called = {"n": 0}
        def fake_get_client():
            called["n"] += 1
            raise AssertionError("get_client не должен быть вызван при пустом тексте")
        monkeypatch.setattr(parser_mod, "get_client", fake_get_client)

        out = parser_mod.parse("   ", usd_rub_rate=90.0)
        assert out.parsed.is_empty is True
        assert out.cost_usd == 0
        assert called["n"] == 0

    def test_valid_json_response(self):
        payload = {
            "is_empty": False,
            "purpose": "office",
            "budget_usd": 600,
            "cpu_manufacturer": None,
            "overrides": {"ram_min_gb": 16},
            "model_mentions": [],
            "clarifying_questions": [],
            "raw_summary": "Понял запрос: офисный ПК до $600",
        }
        cli = fake_openai_client(json.dumps(payload))
        out = parser_mod.parse("офисный ПК", usd_rub_rate=90.0, client=cli, model="gpt-4o-mini")
        assert out.parse_error is None
        assert out.parsed.purpose == "office"
        assert out.parsed.overrides == {"ram_min_gb": 16}
        assert out.tokens_in > 0
        # Стоимость должна быть положительной и крошечной
        assert 0 < out.cost_usd < 0.01

    def test_invalid_json_falls_back(self):
        cli = fake_openai_client("это не JSON, а проза с {скобками не парсится")
        out = parser_mod.parse("любой текст", usd_rub_rate=90.0, client=cli)
        assert out.parsed.is_empty is True
        assert out.parsed.clarifying_questions  # дефолтные вопросы
        assert out.parse_error is not None
        assert out.parse_error.startswith("bad_json")
        # Стоимость считается даже при ошибке, по фактическим токенам
        assert out.cost_usd > 0

    def test_invalid_shape_falls_back(self):
        # JSON валидный, но без обязательного is_empty
        cli = fake_openai_client('{"purpose": "office"}')
        out = parser_mod.parse("любой текст", usd_rub_rate=90.0, client=cli)
        assert out.parsed.is_empty is True
        assert out.parse_error is not None
        assert out.parse_error.startswith("bad_shape")

    def test_user_prompt_includes_rate(self):
        prompt = parser_mod.build_user_prompt("игровой ПК", 95.42)
        assert "95.42" in prompt
        assert "игровой ПК" in prompt
