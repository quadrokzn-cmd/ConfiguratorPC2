# Точечные тесты на схему результата configurator.
# Главное, чтобы supplier_sku не терялся на пути до JSON-представления.

from __future__ import annotations

from app.services.configurator.schema import (
    BuildResult,
    ComponentChoice,
    SupplierOffer,
    Variant,
    result_to_dict,
)


def test_result_to_dict_preserves_supplier_sku():
    offer = SupplierOffer(
        supplier="OCS",
        supplier_sku="1000659869",
        price_usd=100.0,
        price_rub=9000.0,
        stock=5,
    )
    comp = ComponentChoice(
        category="cpu",
        component_id=42,
        model="Intel Core i5-12400",
        sku="BX8071512400",
        manufacturer="Intel",
        chosen=offer,
    )
    result = BuildResult(
        status="ok",
        variants=[Variant(manufacturer="Intel", components=[comp],
                          total_usd=100.0, total_rub=9000.0)],
        refusal_reason=None,
        usd_rub_rate=90.0,
        fx_source="fallback",
    )
    d = result_to_dict(result)
    comp_dict = d["variants"][0]["components"][0]
    assert comp_dict["sku"] == "BX8071512400"
    assert comp_dict["supplier_sku"] == "1000659869"
    assert comp_dict["supplier"] == "OCS"


def test_supplier_offer_supplier_sku_default_is_none():
    offer = SupplierOffer(
        supplier="Any", price_usd=1.0, price_rub=90.0, stock=1,
    )
    assert offer.supplier_sku is None
