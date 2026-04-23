# Тесты форматтера: визуальная сборка финального текста для менеджера.
#
# Не подключаемся ни к OpenAI, ни к БД — это чистая презентация.

from __future__ import annotations

from app.services.configurator.schema import (
    BuildResult,
    ComponentChoice,
    SupplierOffer,
    Variant,
)
from app.services.nlu import formatter


def _offer(usd: float) -> SupplierOffer:
    return SupplierOffer(
        supplier="OCS", price_usd=usd, price_rub=usd * 90.0,
        stock=10, in_transit=False,
    )


def _variant(mfr: str, total: float, *, with_warning: bool = False) -> Variant:
    components = [
        ComponentChoice(
            category="cpu", component_id=1,
            model=f"Процессор/ CPU AM5 AMD Ryzen 5 7600X (Raphael, 6C/12T) OEM",
            sku="CPU-1", manufacturer=mfr, chosen=_offer(180),
            also_available_at=[], quantity=1,
        ),
        ComponentChoice(
            category="ram", component_id=2,
            model="Память/ Kingston Fury 16GB DDR5",
            sku="RAM-1", manufacturer="Kingston",
            chosen=_offer(34), also_available_at=[], quantity=2,
        ),
    ]
    return Variant(
        manufacturer=mfr, components=components,
        total_usd=total, total_rub=total * 90.0,
        warnings=["TDP кулера ниже TDP CPU"] if with_warning else [],
        used_transit=False, path_used="default",
    )


class TestFormatEmpty:
    def test_uses_provided_questions(self):
        text = formatter.format_empty(["Q1", "Q2"])
        assert "Q1" in text and "Q2" in text
        assert "Запрос слишком общий" in text


class TestFormatResponse:
    def test_ok_with_two_variants(self):
        result = BuildResult(
            status="ok",
            variants=[_variant("AMD", 850), _variant("Intel", 920)],
            refusal_reason=None,
            usd_rub_rate=92.5,
            fx_source="cbr",
        )
        text = formatter.format_response(
            interpretation="Понял запрос: игровой ПК.",
            result=result,
            comment="AMD дешевле на $70.",
            checks=["Достаточность мощности БП"],
            warnings=[],
        )
        assert "AMD" in text and "Intel" in text
        assert "Ryzen 5 7600X" in text       # удалили префикс «Процессор/»
        assert "AMD дешевле" in text
        assert "Достаточность мощности БП" in text
        assert "Курс ЦБ: 92.50" in text

    def test_failed_no_variants(self):
        result = BuildResult(
            status="failed",
            variants=[],
            refusal_reason={"intel": "нет совместимого CPU",
                            "amd":   "нет совместимого CPU"},
            usd_rub_rate=90.0,
            fx_source="fallback",
        )
        text = formatter.format_response(
            interpretation="Понял запрос: игровой ПК.",
            result=result,
        )
        assert "не удалось" in text.lower()
        assert "intel" in text.lower()
        assert "amd" in text.lower()

    def test_warnings_block_present(self):
        result = BuildResult(
            status="ok",
            variants=[_variant("AMD", 850)],
            refusal_reason=None,
            usd_rub_rate=90.0,
            fx_source="cache",
        )
        text = formatter.format_response(
            interpretation="Понял запрос.",
            result=result,
            warnings=["Запрошенная модель X не найдена"],
        )
        assert "Запрошенная модель X не найдена" in text
        assert "Предупреждения:" in text

    def test_no_comment_no_block(self):
        result = BuildResult(
            status="ok",
            variants=[_variant("Intel", 800)],
            refusal_reason=None,
            usd_rub_rate=90.0,
            fx_source="cbr",
        )
        text = formatter.format_response(
            interpretation="Понял запрос.",
            result=result,
            comment="",
        )
        assert "Комментарий:" not in text
