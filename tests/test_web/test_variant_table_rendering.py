# Smoke-тесты компактной таблицы варианта (этап 6.2, правка после
# live-проверки).
#
# variant_table заменяет карточки variant_block на странице проекта
# и на /query/{id}. Проверяем, что таблица появляется, основные
# колонки и классы Tailwind применены, данные строк совпадают с
# тем, что пришло из подбора.

from __future__ import annotations

from app.services.configurator.schema import (
    BuildRequest, BuildResult, ComponentChoice, SupplierOffer, Variant,
)
from app.services.nlu.schema import FinalResponse, ParsedRequest

from tests.test_web.conftest import extract_csrf, qid_from_submit_redirect


def _component(
    category: str, cid: int, model: str,
    supplier: str, price_usd: float, price_rub: float,
    *, sku: str | None = None, supplier_sku: str | None = None,
    in_transit: bool = False, quantity: int = 1,
) -> ComponentChoice:
    return ComponentChoice(
        category=category, component_id=cid, model=model, sku=sku,
        manufacturer="X", quantity=quantity,
        chosen=SupplierOffer(
            supplier=supplier, price_usd=price_usd, price_rub=price_rub,
            stock=5, in_transit=in_transit, supplier_sku=supplier_sku,
        ),
    )


def _set_response(mock_process_query, variant: Variant) -> None:
    mock_process_query.return_value = FinalResponse(
        kind="ok", interpretation="",
        formatted_text="", build_request=BuildRequest(),
        build_result=BuildResult(
            status="ok", variants=[variant],
            refusal_reason=None, usd_rub_rate=90.0, fx_source="fallback",
        ),
        parsed=ParsedRequest(is_empty=False, purpose="office"),
        resolved=[], warnings=[], cost_usd=0.0,
    )


def _submit(manager_client) -> str:
    r = manager_client.get("/")
    token = extract_csrf(r.text)
    r = manager_client.post("/query", data={
        "project_name": "Табличный тест", "raw_text": "любой",
        "csrf_token": token,
    })
    qid = qid_from_submit_redirect(r.headers["location"])
    return manager_client.get(f"/query/{qid}").text


def test_variant_table_has_header_and_columns(manager_client, mock_process_query):
    variant = Variant(
        manufacturer="Intel",
        components=[
            _component("cpu", 1, "Intel Core i5-12400F",
                       "OCS", 180, 16200,
                       sku="BX8071512400F", supplier_sku="1000815468"),
            _component("ram", 2, "Kingston Fury Beast 16GB DDR4",
                       "Merlion", 40, 3600, supplier_sku="3000000789"),
        ],
        total_usd=220, total_rub=19800,
    )
    _set_response(mock_process_query, variant)
    html = _submit(manager_client)

    # Таблица есть, шапка есть.
    assert "<table" in html
    assert "<thead>" in html
    # Все шесть колонок по тексту заголовка.
    for col in ("Категория", "Название", "Артикул", "Поставщик", "Цена $", "Цена ₽"):
        assert col in html


def test_variant_table_rows_and_tailwind_classes(manager_client, mock_process_query):
    variant = Variant(
        manufacturer="Intel",
        components=[
            _component("cpu", 11, "Intel Core i5-12400F",
                       "OCS", 180, 16200,
                       sku="BX8071512400F", supplier_sku="1000815468"),
            _component("gpu", 12, "GIGABYTE RTX 4060",
                       "Merlion", 320, 28800, supplier_sku="3000000777",
                       in_transit=True),
        ],
        total_usd=500, total_rub=45000,
    )
    _set_response(mock_process_query, variant)
    html = _submit(manager_client)

    # Строки с моделями компонентов отрисованы.
    assert "Intel Core i5-12400F" in html
    assert "GIGABYTE RTX 4060" in html
    # Артикул / supplier_sku выведены.
    assert "BX8071512400F" in html
    assert "1000815468" in html
    assert "3000000777" in html
    # Поставщики.
    assert "OCS" in html and "Merlion" in html
    # Стилевые классы, которые ожидает ТЗ от variant_table.
    assert "table-auto" in html
    assert "border-b border-zinc-800" in html
    assert "hover:bg-zinc-800/40" in html
    assert "font-mono" in html   # артикулы моноширинным
    # Транзит отмечен внутри ячейки Название.
    assert "транзит" in html


def test_variant_table_model_title_on_hover(manager_client, mock_process_query):
    """Полное имя модели попадает в title="..." — для tooltip при обрезке."""
    long_model = "ASUS ROG Strix GeForce RTX 4070 Ti SUPER OC Edition 16GB GDDR6X"
    variant = Variant(
        manufacturer="Intel",
        components=[
            _component("cpu", 21, "Intel Core i5-12400F",
                       "OCS", 180, 16200),
            _component("gpu", 22, long_model,
                       "OCS", 900, 81000),
        ],
        total_usd=1200, total_rub=108000,
    )
    _set_response(mock_process_query, variant)
    html = _submit(manager_client)

    # title атрибут содержит полное имя.
    assert f'title="{long_model}"' in html


def test_variant_table_no_sku_shown_as_dash(manager_client, mock_process_query):
    variant = Variant(
        manufacturer="Intel",
        components=[
            _component("cpu", 31, "Intel Core i3-12100F",
                       "OCS", 100, 9000),  # sku=None, supplier_sku=None
        ],
        total_usd=100, total_rub=9000,
    )
    _set_response(mock_process_query, variant)
    html = _submit(manager_client)

    assert "Intel Core i3-12100F" in html
    # Для пустого SKU — прочерк.
    assert "—" in html
