# Тесты модуля OpenAI Web Search (этап 2.5В).
#
# Проверяем логику без реальных вызовов API:
#   - skip_rules на различных манипуляторах и категориях;
#   - cost_guard: допустимые границы, отказы, non-interactive режим;
#   - fx: fallback при недоступности ЦБ (через моки);
#   - парсинг JSON-ответа от модели (в т.ч. с markdown-обёрткой).

import json
from unittest.mock import patch

import pytest

from portal.services.configurator.enrichment.openai_search import cost_guard, fx, skip_rules


# -----------------------------------------------------------------------------
# skip_rules
# -----------------------------------------------------------------------------

class TestSkipRules:
    def test_case_without_psu(self):
        row = {"manufacturer": "Fractal", "has_psu_included": False}
        assert skip_rules.should_skip("case", "included_psu_watts", row) \
            == "case_without_psu"

    def test_case_with_psu_not_skipped(self):
        row = {"manufacturer": "AeroCool", "has_psu_included": True}
        assert skip_rules.should_skip("case", "included_psu_watts", row) is None

    def test_case_psu_unknown_not_skipped(self):
        row = {"manufacturer": "AeroCool", "has_psu_included": None}
        assert skip_rules.should_skip("case", "included_psu_watts", row) is None

    def test_cooler_thermalright_tdp(self):
        assert skip_rules.should_skip(
            "cooler", "max_tdp_watts",
            {"manufacturer": "Thermalright"},
        ) == "mfg_does_not_publish_tdp"

    def test_cooler_noctua_not_skipped(self):
        assert skip_rules.should_skip(
            "cooler", "max_tdp_watts",
            {"manufacturer": "Noctua"},
        ) is None

    def test_gpu_afox(self):
        assert skip_rules.should_skip(
            "gpu", "tdp_watts",
            {"manufacturer": "AFOX CORPORATION"},
        ) == "mfg_no_public_gpu_specs"

    def test_gpu_asus_not_skipped(self):
        assert skip_rules.should_skip(
            "gpu", "tdp_watts",
            {"manufacturer": "ASUS"},
        ) is None

    def test_psu_poe_injector(self):
        row = {
            "manufacturer": "Ubiquiti",
            "model": "UBNT POE-15-12W Power Injector",
        }
        assert skip_rules.should_skip("psu", "power_watts", row) == "not_a_pc_psu"


# -----------------------------------------------------------------------------
# cost_guard
# -----------------------------------------------------------------------------

class TestCostGuard:
    def test_zero_candidates_allowed(self, monkeypatch):
        monkeypatch.setattr(fx, "get_usd_rub_rate", lambda **kw: (90.0, "fallback"))
        est = cost_guard.estimate(0)
        ok, reason = cost_guard.confirm(est, non_interactive=True)
        assert ok is True
        assert reason == "no_candidates"

    def test_under_auto_limit_allowed(self, monkeypatch):
        monkeypatch.setattr(fx, "get_usd_rub_rate", lambda **kw: (90.0, "fallback"))
        monkeypatch.setenv("OPENAI_ENRICH_AUTO_LIMIT", "20")
        monkeypatch.setenv("OPENAI_ENRICH_MAX", "200")
        est = cost_guard.estimate(10)
        ok, reason = cost_guard.confirm(est, non_interactive=True)
        assert ok is True
        assert reason == "within_auto_limit"

    def test_between_limit_and_max_non_interactive_denied(self, monkeypatch):
        monkeypatch.setattr(fx, "get_usd_rub_rate", lambda **kw: (90.0, "fallback"))
        monkeypatch.setenv("OPENAI_ENRICH_AUTO_LIMIT", "20")
        monkeypatch.setenv("OPENAI_ENRICH_MAX", "200")
        est = cost_guard.estimate(100)
        ok, reason = cost_guard.confirm(est, non_interactive=True)
        assert ok is False
        assert "AUTO_LIMIT" in reason

    def test_over_hard_max_denied(self, monkeypatch):
        monkeypatch.setattr(fx, "get_usd_rub_rate", lambda **kw: (90.0, "fallback"))
        monkeypatch.setenv("OPENAI_ENRICH_AUTO_LIMIT", "20")
        monkeypatch.setenv("OPENAI_ENRICH_MAX", "200")
        est = cost_guard.estimate(500)
        ok, reason = cost_guard.confirm(est, non_interactive=False,
                                         prompt_fn=lambda _t: "да")
        assert ok is False
        assert "OPENAI_ENRICH_MAX" in reason

    def test_interactive_yes(self, monkeypatch):
        monkeypatch.setattr(fx, "get_usd_rub_rate", lambda **kw: (90.0, "fallback"))
        monkeypatch.setenv("OPENAI_ENRICH_AUTO_LIMIT", "20")
        monkeypatch.setenv("OPENAI_ENRICH_MAX", "200")
        est = cost_guard.estimate(50)
        ok, reason = cost_guard.confirm(est, non_interactive=False,
                                         prompt_fn=lambda _t: "да")
        assert ok is True
        assert reason == "user_confirmed"

    def test_interactive_no(self, monkeypatch):
        monkeypatch.setattr(fx, "get_usd_rub_rate", lambda **kw: (90.0, "fallback"))
        monkeypatch.setenv("OPENAI_ENRICH_AUTO_LIMIT", "20")
        monkeypatch.setenv("OPENAI_ENRICH_MAX", "200")
        est = cost_guard.estimate(50)
        ok, reason = cost_guard.confirm(est, non_interactive=False,
                                         prompt_fn=lambda _t: "nope")
        assert ok is False
        assert reason == "user_declined"

    def test_cost_estimate_math(self, monkeypatch):
        monkeypatch.setattr(fx, "get_usd_rub_rate", lambda **kw: (100.0, "fallback"))
        monkeypatch.setenv("OPENAI_ENRICH_COST_PER_CALL_USD", "0.05")
        est = cost_guard.estimate(10)
        assert est.total_usd == pytest.approx(0.50)
        assert est.total_rub == pytest.approx(50.0)


# -----------------------------------------------------------------------------
# fx: fallback при недоступности ЦБ
# -----------------------------------------------------------------------------

class TestFx:
    def test_fallback_when_cbr_unreachable(self, monkeypatch, tmp_path):
        # подменяем путь кэша в tmp
        monkeypatch.setattr(fx, "_CACHE_FILE", tmp_path / ".fx_cache.json")
        monkeypatch.setattr(fx, "_fetch_from_cbr", lambda: None)
        monkeypatch.setenv("OPENAI_ENRICH_USD_RUB_FALLBACK", "88.8")
        rate, source = fx.get_usd_rub_rate(force_refresh=True)
        assert rate == pytest.approx(88.8)
        assert source == "fallback"

    def test_cbr_success_is_cached(self, monkeypatch, tmp_path):
        monkeypatch.setattr(fx, "_CACHE_FILE", tmp_path / ".fx_cache.json")
        calls = {"n": 0}
        def fake_fetch():
            calls["n"] += 1
            return 77.77
        monkeypatch.setattr(fx, "_fetch_from_cbr", fake_fetch)

        rate1, src1 = fx.get_usd_rub_rate(force_refresh=True)
        rate2, src2 = fx.get_usd_rub_rate()
        assert rate1 == rate2 == pytest.approx(77.77)
        assert src1 == "cbr"
        assert src2 == "cache"
        assert calls["n"] == 1  # второй раз не ходили в ЦБ


# -----------------------------------------------------------------------------
# Парсинг ответа модели
# -----------------------------------------------------------------------------

class TestParseResponse:
    def test_plain_json(self):
        from portal.services.configurator.enrichment.openai_search.client import _parse_model_response
        s = json.dumps({"fields": {"tdp_watts": {"value": 150, "source_url": "https://x"}}})
        obj = _parse_model_response(s)
        assert obj["fields"]["tdp_watts"]["value"] == 150

    def test_markdown_wrapped(self):
        from portal.services.configurator.enrichment.openai_search.client import _parse_model_response
        s = "```json\n{\"fields\": {\"tdp_watts\": {\"value\": 200}}}\n```"
        obj = _parse_model_response(s)
        assert obj["fields"]["tdp_watts"]["value"] == 200

    def test_prose_before_json(self):
        from portal.services.configurator.enrichment.openai_search.client import _parse_model_response
        s = "Вот результат:\n\n{\"fields\": {\"x\": {\"value\": 1}}}\n\nГотово."
        obj = _parse_model_response(s)
        assert obj["fields"]["x"]["value"] == 1
