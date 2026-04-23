# Интеграционные тесты pipeline.process_query.
#
# Здесь мокаем:
#   - parser.parse (возвращает заранее подготовленный ParsedRequest);
#   - fuzzy_lookup.find (если упомянута модель);
#   - configurator.build_config (возвращает фиктивный BuildResult);
#   - commentator.comment (без реального OpenAI).
#   - SessionLocal — фиктивная сессия (никаких SQL).
#
# Цель — убедиться, что точка входа process_query собирает все эти
# куски в правильный FinalResponse, и что текст для менеджера
# содержит ключевые элементы.

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.configurator.schema import (
    BuildResult,
    ComponentChoice,
    SupplierOffer,
    Variant,
)
from app.services.nlu import pipeline as pipeline_mod
from app.services.nlu import (
    commentator as commentator_mod,
    fuzzy_lookup as fuzzy_mod,
    parser as parser_mod,
)
from app.services.nlu.commentator import CommentOutcome
from app.services.nlu.parser import ParseOutcome
from app.services.nlu.schema import ModelMention, ParsedRequest, ResolvedMention


# -----------------------------------------------------------------------------
# Фикстуры-помощники
# -----------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_session_and_logging(monkeypatch):
    """Подменяем SessionLocal в pipeline на фиктивную сессию."""
    class _FakeSession:
        def execute(self, *a, **kw):
            r = MagicMock()
            r.mappings.return_value.all.return_value = []
            r.mappings.return_value.first.return_value = None
            return r
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass
    monkeypatch.setattr(pipeline_mod, "SessionLocal", lambda: _FakeSession())


def _mk_parsed(**kw) -> ParsedRequest:
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


def _mk_supplier_offer(price_usd: float, supplier: str = "OCS") -> SupplierOffer:
    return SupplierOffer(
        supplier=supplier,
        price_usd=price_usd,
        price_rub=price_usd * 90.0,
        stock=10,
        in_transit=False,
    )


def _mk_variant(mfr: str, total_usd: float) -> Variant:
    components = [
        ComponentChoice(
            category="cpu", component_id=1,
            model=f"{mfr} CPU example", sku="C1",
            manufacturer=mfr, chosen=_mk_supplier_offer(180.0),
            also_available_at=[], quantity=1,
        ),
        ComponentChoice(
            category="ram", component_id=2,
            model="Kingston Fury 16GB", sku="R1",
            manufacturer="Kingston",
            chosen=_mk_supplier_offer(34.0),
            also_available_at=[], quantity=2,
        ),
    ]
    return Variant(
        manufacturer=mfr,
        components=components,
        total_usd=total_usd,
        total_rub=total_usd * 90.0,
        warnings=[],
        used_transit=False,
        path_used="default",
    )


def _mk_build_result(variants: list[Variant], status: str = "ok") -> BuildResult:
    return BuildResult(
        status=status,
        variants=variants,
        refusal_reason=None,
        usd_rub_rate=90.0,
        fx_source="fallback",
    )


def _patch_parser(monkeypatch, parsed: ParsedRequest, *, cost: float = 0.0003):
    monkeypatch.setattr(
        pipeline_mod.parser_mod, "parse",
        lambda text, usd_rub_rate, **kw: ParseOutcome(
            parsed=parsed, cost_usd=cost,
            tokens_in=1000, tokens_out=300,
        ),
    )


def _patch_build_config(monkeypatch, result: BuildResult):
    captured: dict = {}
    def fake(req):
        captured["req"] = req
        return result
    monkeypatch.setattr(pipeline_mod, "build_config", fake)
    return captured


def _patch_commentator(monkeypatch, *, comment: str = "AMD дешевле",
                       checks: list[str] | None = None,
                       cost: float = 0.0002):
    monkeypatch.setattr(
        pipeline_mod.commentator_mod, "comment",
        lambda result, **kw: CommentOutcome(
            comment=comment,
            checks=checks or [],
            cost_usd=cost,
            tokens_in=900, tokens_out=80,
        ),
    )


def _patch_fuzzy(monkeypatch, results_by_query: dict[str, ResolvedMention]):
    def fake_find(session, mention: ModelMention) -> ResolvedMention:
        if mention.query in results_by_query:
            return results_by_query[mention.query]
        return ResolvedMention(mention=mention, note=f"не найдено: {mention.query}")
    monkeypatch.setattr(pipeline_mod.fuzzy_lookup, "find", fake_find)


# -----------------------------------------------------------------------------
# Сценарии
# -----------------------------------------------------------------------------

class TestEmptyQuery:
    def test_returns_clarifying_questions(self, monkeypatch):
        _patch_parser(monkeypatch, _mk_parsed(
            is_empty=True,
            clarifying_questions=["Для чего ПК?", "Какой бюджет?"],
        ))
        # build_config не должен быть вызван — но ставим заглушку на всякий случай
        _patch_build_config(monkeypatch, _mk_build_result([]))

        resp = pipeline_mod.process_query("нужен ПК")
        assert resp.kind == "empty"
        assert "Для чего ПК?" in resp.formatted_text
        assert "Какой бюджет?" in resp.formatted_text
        assert resp.build_request is None


class TestOfficeProfile:
    def test_teacher_default_office(self, monkeypatch):
        _patch_parser(monkeypatch, _mk_parsed(purpose="office"))
        cap = _patch_build_config(monkeypatch, _mk_build_result([_mk_variant("Intel", 450)]))
        _patch_commentator(monkeypatch, comment="Бюджетная сборка для офиса")

        resp = pipeline_mod.process_query("нужен ПК для учителя")
        assert resp.kind == "ok"
        req = cap["req"]
        assert req.ram.min_gb == 8
        assert req.storage.min_gb == 240
        assert req.gpu.required is False
        assert "Intel" in resp.formatted_text

    def test_teacher_with_16gb_override(self, monkeypatch):
        _patch_parser(monkeypatch, _mk_parsed(
            purpose="office",
            overrides={"ram_min_gb": 16},
        ))
        cap = _patch_build_config(monkeypatch, _mk_build_result([_mk_variant("AMD", 480)]))
        _patch_commentator(monkeypatch)
        resp = pipeline_mod.process_query("нужен ПК для учителя с 16 ГБ оперативки")
        assert cap["req"].ram.min_gb == 16


class TestGaming:
    def test_gaming_with_budget(self, monkeypatch):
        _patch_parser(monkeypatch, _mk_parsed(
            purpose="gaming",
            budget_usd=1111,
        ))
        cap = _patch_build_config(monkeypatch, _mk_build_result([
            _mk_variant("AMD",   888),
            _mk_variant("Intel", 920),
        ]))
        _patch_commentator(monkeypatch, comment="AMD дешевле на $32")

        resp = pipeline_mod.process_query("игровой ПК до 100к")
        assert resp.kind == "ok"
        req = cap["req"]
        assert req.budget_usd == 1111
        assert req.cpu.min_cores == 6
        assert req.gpu.required is True
        assert "AMD" in resp.formatted_text and "Intel" in resp.formatted_text
        assert "AMD дешевле" in resp.formatted_text


class TestModelMentionFound:
    def test_ryzen_5_7600_fixed(self, monkeypatch):
        _patch_parser(monkeypatch, _mk_parsed(
            purpose="gaming",
            model_mentions=[ModelMention(category="cpu", query="Ryzen 5 7600")],
        ))
        _patch_fuzzy(monkeypatch, {
            "Ryzen 5 7600": ResolvedMention(
                mention=ModelMention(category="cpu", query="Ryzen 5 7600"),
                found_id=7, found_model="Ryzen 5 7600X OEM",
                found_sku="SKU-7",
            ),
        })
        cap = _patch_build_config(monkeypatch, _mk_build_result([_mk_variant("AMD", 850)]))
        _patch_commentator(monkeypatch)

        resp = pipeline_mod.process_query("хочу сборку на Ryzen 5 7600")
        assert cap["req"].cpu.fixed is not None
        assert cap["req"].cpu.fixed.id == 7

    def test_rtx_4060_substitute_warning(self, monkeypatch):
        # RTX 4060 → нашли substitute
        _patch_parser(monkeypatch, _mk_parsed(
            purpose="gaming",
            model_mentions=[ModelMention(category="gpu", query="RTX 4060")],
        ))
        sub = ResolvedMention(
            mention=ModelMention(category="gpu", query="RTX 4060"),
            found_id=99, found_model="Palit RTX 4060 Ti EVO",
            is_substitute=True,
            note="Запрошенная модель «RTX 4060» точно не найдена; "
                 "подобран близкий вариант: Palit RTX 4060 Ti EVO.",
        )
        _patch_fuzzy(monkeypatch, {"RTX 4060": sub})
        cap = _patch_build_config(monkeypatch, _mk_build_result([_mk_variant("Intel", 950)]))
        _patch_commentator(monkeypatch)

        resp = pipeline_mod.process_query("С RTX 4060")
        assert cap["req"].gpu.fixed is not None
        assert cap["req"].gpu.fixed.id == 99
        # warning должен попасть в текст и в FinalResponse.warnings
        assert any("близкий вариант" in w for w in resp.warnings)
        assert "близкий вариант" in resp.formatted_text


class TestModelMentionNotFound:
    def test_unknown_gpu(self, monkeypatch):
        _patch_parser(monkeypatch, _mk_parsed(
            purpose="gaming",
            model_mentions=[ModelMention(category="gpu", query="RTX 9999")],
        ))
        _patch_fuzzy(monkeypatch, {
            "RTX 9999": ResolvedMention(
                mention=ModelMention(category="gpu", query="RTX 9999"),
                note='Модель «RTX 9999» в каталоге не найдена. '
                     'Подбор пройдёт без её фиксации, по характеристикам.',
            ),
        })
        cap = _patch_build_config(monkeypatch, _mk_build_result([_mk_variant("AMD", 800)]))
        _patch_commentator(monkeypatch)

        resp = pipeline_mod.process_query("хочу с RTX 9999")
        # GPU не зафиксирована, но gaming-профиль уже сделал её обязательной
        assert cap["req"].gpu.fixed is None
        assert cap["req"].gpu.required is True
        assert any("не найдена" in w for w in resp.warnings)


class TestFailedBuild:
    def test_no_variants_returns_failed_kind(self, monkeypatch):
        _patch_parser(monkeypatch, _mk_parsed(
            purpose="gaming",
            budget_usd=100,  # очень мало
        ))
        result = BuildResult(
            status="failed",
            variants=[],
            refusal_reason={"intel": "Минимальная сборка дороже бюджета",
                            "amd":   "Минимальная сборка дороже бюджета"},
            usd_rub_rate=90.0,
            fx_source="fallback",
        )
        _patch_build_config(monkeypatch, result)
        # commentator не должен быть вызван при отсутствии вариантов
        called = {"n": 0}
        def fake_comment(*a, **kw):
            called["n"] += 1
            return CommentOutcome(checks=[])
        monkeypatch.setattr(pipeline_mod.commentator_mod, "comment", fake_comment)

        resp = pipeline_mod.process_query("игровой ПК до 100 рублей")
        assert resp.kind == "failed"
        assert "не удалось" in resp.formatted_text.lower()
        assert called["n"] == 0


class TestParserFallback:
    def test_invalid_json_falls_back_to_empty(self, monkeypatch):
        # Парсер встретил невалидный ответ от OpenAI и вернул is_empty=True
        # с дефолтными уточняющими вопросами; ошибка лежит в parse_error.
        fallback = ParsedRequest(
            is_empty=True,
            clarifying_questions=list(parser_mod._DEFAULT_CLARIFYING_QUESTIONS),
        )
        monkeypatch.setattr(
            pipeline_mod.parser_mod, "parse",
            lambda text, usd_rub_rate, **kw: ParseOutcome(
                parsed=fallback,
                cost_usd=0.0003, tokens_in=900, tokens_out=10,
                parse_error="bad_json:test",
            ),
        )

        resp = pipeline_mod.process_query("любой странный текст")
        assert resp.kind == "empty"
        assert resp.clarifying_questions
        # Стоимость парсера всё равно засчитана
        assert resp.cost_usd > 0
