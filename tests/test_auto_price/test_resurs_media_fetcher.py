# Тесты ResursMediaApiFetcher (этап 12.4-РМ-1).
#
# Все SOAP-вызовы подменяем через monkeypatch'инг _invoke / _get_client —
# реальный zeep.Client не поднимается, в сеть тесты не ходят.
# БД-логика (одна позиция дойдёт до supplier_prices) — на реальном test-БД
# через orchestrator.

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text


# ---- helpers ----------------------------------------------------------

class _FakeClient:
    """Мок zeep.Client. Сам по себе ничего не делает — _invoke у нас
    статический, и тесты его подменяют отдельно. Объект нужен только
    чтобы _get_client() возвращал хоть что-то."""


def _patch_client_and_invoke(
    monkeypatch, response_or_handler,
):
    """Подменяет ResursMediaApiFetcher._get_client (→ _FakeClient)
    и ResursMediaApiFetcher._invoke (→ handler).

    response_or_handler:
      - dict           → одинаковый ответ на любую operation;
      - list[dict]     → последовательность ответов (по индексу вызова);
      - callable(operation, kwargs) → произвольный handler.

    Возвращает list calls = [(operation, kwargs), ...] для проверки
    тестом аргументов вызовов.
    """
    import portal.services.configurator.auto_price.fetchers.resurs_media as rm

    calls: list[tuple[str, dict]] = []

    if callable(response_or_handler):
        handler = response_or_handler
    elif isinstance(response_or_handler, list):
        seq = list(response_or_handler)
        def handler(operation, kwargs):
            return seq.pop(0)
    else:
        single = response_or_handler
        def handler(operation, kwargs):
            return single

    def fake_invoke(_client, operation, kwargs):
        calls.append((operation, kwargs))
        return handler(operation, kwargs)

    monkeypatch.setattr(
        rm.ResursMediaApiFetcher, "_get_client",
        lambda self: _FakeClient(),
    )
    monkeypatch.setattr(
        rm.ResursMediaApiFetcher, "_invoke",
        staticmethod(fake_invoke),
    )
    return calls


# Минимальный валидный ответ GetMaterialData для одной позиции.
def _md_response(items: list[dict]) -> dict:
    return {
        "Result": 0,
        "ErrorMessage": None,
        "MaterialData_Tab": {"Item": items},
    }


# Минимальный валидный ответ GetPrices.
def _prices_response(items: list[dict]) -> dict:
    return {
        "Result": 0,
        "ErrorMessage": None,
        "Material_Tab": items,
    }


# =====================================================================
# 1. GetPrices аргументы — warehouse + все 12 group_id (8 категорий)
# =====================================================================

def test_get_prices_call_uses_correct_warehouse_and_groups(resurs_media_env, monkeypatch):
    from portal.services.configurator.auto_price.fetchers.resurs_media import (
        ResursMediaApiFetcher, _ALL_GROUP_IDS,
    )

    calls = _patch_client_and_invoke(monkeypatch, [
        _prices_response([]),  # пусто → NoNewDataException, тестируем только аргументы
    ])

    fetcher = ResursMediaApiFetcher()

    from portal.services.configurator.auto_price.fetchers.base_imap import NoNewDataException
    with pytest.raises(NoNewDataException):
        fetcher.fetch_and_save()

    assert len(calls) == 1
    operation, kwargs = calls[0]
    assert operation == "GetPrices"
    assert kwargs["WareHouseID"] == "00011"  # Москва, hardcoded
    assert kwargs["GetAvailableCount"] is True

    # MaterialGroup_Tab в формате {"Item": [{"MaterialGroup": gid}, ...]}
    tab = kwargs["MaterialGroup_Tab"]
    assert "Item" in tab
    sent_groups = [it["MaterialGroup"] for it in tab["Item"]]
    assert sorted(sent_groups) == sorted(_ALL_GROUP_IDS)
    # 11 group_id (8 категорий + по доп.группе у psu и storage).
    # Если меняется _CATEGORY_GROUP_MAP — обновите и этот счётчик.
    assert len(sent_groups) == 11
    # Все 8 наших категорий должны быть представлены.
    from portal.services.configurator.auto_price.fetchers.resurs_media import _GROUP_TO_OUR_CATEGORY
    covered_categories = {_GROUP_TO_OUR_CATEGORY[g] for g in sent_groups}
    assert covered_categories == {
        "psu", "cooler", "gpu", "storage", "motherboard", "ram", "case", "cpu",
    }


# =====================================================================
# 2. SOAP padding на MaterialID/MaterialGroup → strip
# =====================================================================

def test_strip_padding_from_material_id_and_warehouse_id(
    resurs_media_env, monkeypatch, db_session,
):
    """Resurs Media выравнивает строковые ID пробелами справа. Мы должны
    стрипать их до lookup в _GROUP_TO_OUR_CATEGORY и до записи в БД."""
    from portal.services.configurator.auto_price.fetchers.resurs_media import ResursMediaApiFetcher

    raw_items = [{
        "MaterialID":     "RAM-001       ",   # лишние пробелы справа
        "PartNum":        "DDR4-3200-CL16",
        "Price":          1200.0,
        "PriceUSD":       12.0,
        "AvailableCount": "5",
    }]
    md_items = [{
        "MaterialID":    "RAM-001       ",   # та же длина — должна найтись после strip
        "PartNum":       "DDR4-3200-CL16",
        "VendorPart":    "DDR4-3200-CL16",
        "MaterialText":  "Kingston DDR4 16GB 3200",
        "MaterialGroup": "Z431       ",      # ram, с padding
        "Vendor":        "Kingston",
    }]

    _patch_client_and_invoke(monkeypatch, [
        _prices_response(raw_items),
        _md_response(md_items),
    ])

    upload_id = ResursMediaApiFetcher().fetch_and_save()
    assert isinstance(upload_id, int) and upload_id > 0

    # supplier_sku в supplier_prices — без trailing-пробелов.
    row = db_session.execute(text(
        "SELECT supplier_sku FROM supplier_prices "
        "WHERE supplier_sku = 'RAM-001'"
    )).first()
    assert row is not None, "supplier_sku должен быть стрипнутый"

    # raw_category хранится в unmapped_supplier_items — для NO_MATCH/AMBIG
    # позиций. Для свежесозданного скелета status='created_new'. Тоже
    # должна быть стрипнутая «Z431».
    unm = db_session.execute(text(
        "SELECT raw_category FROM unmapped_supplier_items "
        "WHERE supplier_sku = 'RAM-001'"
    )).first()
    assert unm is not None
    assert unm.raw_category == "Z431"


# =====================================================================
# 3. Позиция с MaterialGroup, не входящей в наши 8 категорий, → пропуск
# =====================================================================

def test_skip_position_outside_our_categories(resurs_media_env, monkeypatch):
    """Z017 (Дискеты на test-стенде) не попадает в _GROUP_TO_OUR_CATEGORY.
    Все позиции из неё должны быть отфильтрованы."""
    from portal.services.configurator.auto_price.fetchers.resurs_media import ResursMediaApiFetcher
    from portal.services.configurator.auto_price.fetchers.base_imap import NoNewDataException

    raw_items = [{
        "MaterialID":     "К104",
        "PartNum":        "MF-2HD10NA",
        "Price":          188.49,
        "AvailableCount": "42",
    }]
    md_items = [{
        "MaterialID":    "К104",
        "VendorPart":    "N6050-010-0R",
        "MaterialText":  "Дискеты Nashua",
        "MaterialGroup": "Z017",  # вне наших 8
        "Vendor":        "Прочее",
    }]

    _patch_client_and_invoke(monkeypatch, [
        _prices_response(raw_items),
        _md_response(md_items),
    ])

    # Все позиции отфильтрованы → NoNewDataException (см. _save_rows).
    with pytest.raises(NoNewDataException, match="ни одной позиции"):
        ResursMediaApiFetcher().fetch_and_save()


# =====================================================================
# 4. Позиция в GetPrices, но GetMaterialData не вернул о ней данных
# =====================================================================

def test_skip_position_missing_in_material_data_response(
    resurs_media_env, monkeypatch, db_session,
):
    """В GetPrices пришли две позиции, в GetMaterialData — только одна.
    Вторая молча пропускается (счётчик skipped_no_md в логе)."""
    from portal.services.configurator.auto_price.fetchers.resurs_media import ResursMediaApiFetcher

    raw_items = [
        {"MaterialID": "CPU-OK",      "Price": 5000.0, "AvailableCount": "3"},
        {"MaterialID": "CPU-MISSING", "Price": 7000.0, "AvailableCount": "2"},
    ]
    md_items = [
        # Только одна — про "CPU-MISSING" сервер ничего не вернул.
        {
            "MaterialID":    "CPU-OK",
            "VendorPart":    "i5-12400F",
            "MaterialText":  "Intel Core i5-12400F",
            "MaterialGroup": "Z999-10110",  # cpu
            "Vendor":        "Intel",
        },
    ]

    _patch_client_and_invoke(monkeypatch, [
        _prices_response(raw_items),
        _md_response(md_items),
    ])

    upload_id = ResursMediaApiFetcher().fetch_and_save()
    assert upload_id > 0

    skus = [r.supplier_sku for r in db_session.execute(text(
        "SELECT supplier_sku FROM supplier_prices ORDER BY supplier_sku"
    )).all()]
    assert "CPU-OK" in skus
    assert "CPU-MISSING" not in skus


# =====================================================================
# 5. Result=3 → парсинг секунд, sleep, retry один раз
# =====================================================================

def test_rate_limit_retry_on_result_3(resurs_media_env, monkeypatch):
    """Первый GetPrices вернул Result=3 + интервал в ErrorMessage.
    Fetcher делает time.sleep(N+2), retry. Второй ответ — успех."""
    from portal.services.configurator.auto_price.fetchers.resurs_media import ResursMediaApiFetcher
    from portal.services.configurator.auto_price.fetchers.base_imap import NoNewDataException

    sleep_calls: list[float] = []

    import portal.services.configurator.auto_price.fetchers.resurs_media as rm
    monkeypatch.setattr(rm.time, "sleep", lambda s: sleep_calls.append(s))

    # GetPrices_call_1: Result=3.  call_2: пустой Material_Tab.
    # На этом fetcher бросит NoNewDataException — это нас не интересует;
    # цель теста — проверить что был retry с правильным sleep.
    responses = [
        {
            "Result": 3,
            "ErrorMessage": "Разрешенный интервал между запросами 60 сек.",
        },
        _prices_response([]),
    ]
    _patch_client_and_invoke(monkeypatch, responses)

    with pytest.raises(NoNewDataException):
        ResursMediaApiFetcher().fetch_and_save()

    # 60 сек из ErrorMessage + 2 сек padding = 62.
    assert sleep_calls == [62]


# =====================================================================
# 6. Result=3 на retry → RuntimeError (не loop)
# =====================================================================

def test_no_retry_on_result_3_on_second_attempt(resurs_media_env, monkeypatch):
    """Если retry тоже Result=3 — поднимаем RuntimeError, не loop'имся."""
    from portal.services.configurator.auto_price.fetchers.resurs_media import ResursMediaApiFetcher

    import portal.services.configurator.auto_price.fetchers.resurs_media as rm
    monkeypatch.setattr(rm.time, "sleep", lambda s: None)

    rate_limited = {
        "Result": 3,
        "ErrorMessage": "Разрешенный интервал между запросами 60 сек.",
    }
    _patch_client_and_invoke(monkeypatch, [rate_limited, rate_limited])

    with pytest.raises(RuntimeError, match="повторный Result=3"):
        ResursMediaApiFetcher().fetch_and_save()


# =====================================================================
# 7. Result=4 → NoNewDataException
# =====================================================================

def test_result_4_raises_no_new_data(resurs_media_env, monkeypatch):
    from portal.services.configurator.auto_price.fetchers.resurs_media import ResursMediaApiFetcher
    from portal.services.configurator.auto_price.fetchers.base_imap import NoNewDataException

    _patch_client_and_invoke(monkeypatch, {
        "Result": 4,
        "ErrorMessage": "Работа с заказами через API отключена.",
    })

    with pytest.raises(NoNewDataException, match="Result=4"):
        ResursMediaApiFetcher().fetch_and_save()


# =====================================================================
# 8. Result=1 → RuntimeError с ErrorMessage
# =====================================================================

def test_result_1_raises_runtime_error(resurs_media_env, monkeypatch):
    from portal.services.configurator.auto_price.fetchers.resurs_media import ResursMediaApiFetcher

    _patch_client_and_invoke(monkeypatch, {
        "Result": 1,
        "ErrorMessage": "Internal server error",
    })

    with pytest.raises(RuntimeError) as ei:
        ResursMediaApiFetcher().fetch_and_save()
    assert "Result=1" in str(ei.value)
    assert "Internal server error" in str(ei.value)


# =====================================================================
# 9. storage агрегирует Z383, Z897, Z373 — все в our_category="storage"
# =====================================================================

def test_storage_category_aggregates_three_groups(
    resurs_media_env, monkeypatch, db_session,
):
    from portal.services.configurator.auto_price.fetchers.resurs_media import ResursMediaApiFetcher

    raw_items = [
        {"MaterialID": "HDD-1", "Price": 5000.0, "AvailableCount": "1"},
        {"MaterialID": "SSD-1", "Price": 6000.0, "AvailableCount": "2"},
        {"MaterialID": "FLASH-1", "Price": 1500.0, "AvailableCount": "3"},
    ]
    md_items = [
        {"MaterialID": "HDD-1",   "VendorPart": "WD", "MaterialText": "WD HDD",
         "MaterialGroup": "Z383", "Vendor": "WD"},
        {"MaterialID": "SSD-1",   "VendorPart": "SAM", "MaterialText": "Samsung SSD",
         "MaterialGroup": "Z897", "Vendor": "Samsung"},
        {"MaterialID": "FLASH-1", "VendorPart": "KF",  "MaterialText": "Kingston flash",
         "MaterialGroup": "Z373", "Vendor": "Kingston"},
    ]

    _patch_client_and_invoke(monkeypatch, [
        _prices_response(raw_items),
        _md_response(md_items),
    ])

    ResursMediaApiFetcher().fetch_and_save()

    rows = db_session.execute(text(
        "SELECT supplier_sku, category FROM supplier_prices "
        "ORDER BY supplier_sku"
    )).all()
    cats = {r.supplier_sku: r.category for r in rows}
    assert cats["FLASH-1"] == "storage"
    assert cats["HDD-1"]   == "storage"
    assert cats["SSD-1"]   == "storage"


# =====================================================================
# 10. psu из Z999-919999 + Z999-9992 — обе → our_category="psu"
# =====================================================================

def test_psu_dual_group_codes(resurs_media_env, monkeypatch, db_session):
    from portal.services.configurator.auto_price.fetchers.resurs_media import ResursMediaApiFetcher

    raw_items = [
        {"MaterialID": "PSU-CASE",   "Price": 4000.0, "AvailableCount": "1"},
        {"MaterialID": "PSU-SERVER", "Price": 9000.0, "AvailableCount": "1"},
    ]
    md_items = [
        {"MaterialID": "PSU-CASE",   "VendorPart": "RM750", "MaterialText": "Corsair RM750",
         "MaterialGroup": "Z999-919999", "Vendor": "Corsair"},
        {"MaterialID": "PSU-SERVER", "VendorPart": "PWR1",  "MaterialText": "Power Server PSU",
         "MaterialGroup": "Z999-9992",   "Vendor": "DELL"},
    ]

    _patch_client_and_invoke(monkeypatch, [
        _prices_response(raw_items),
        _md_response(md_items),
    ])

    ResursMediaApiFetcher().fetch_and_save()

    cats = {
        r.supplier_sku: r.category
        for r in db_session.execute(text(
            "SELECT supplier_sku, category FROM supplier_prices "
            "WHERE supplier_sku IN ('PSU-CASE','PSU-SERVER')"
        )).all()
    }
    assert cats["PSU-CASE"]   == "psu"
    assert cats["PSU-SERVER"] == "psu"


# =====================================================================
# 11. Парсинг Price — и string, и float
# =====================================================================

def test_correct_decimal_parsing_from_price(
    resurs_media_env, monkeypatch, db_session,
):
    from portal.services.configurator.auto_price.fetchers.resurs_media import ResursMediaApiFetcher

    raw_items = [
        # SOAP может вернуть Price и как float, и как строку. Проверяем оба.
        {"MaterialID": "P-FLOAT",  "Price": 188.49,    "AvailableCount": "1"},
        {"MaterialID": "P-STRING", "Price": "200.10", "AvailableCount": "1"},
    ]
    md_items = [
        {"MaterialID": "P-FLOAT",  "VendorPart": "A", "MaterialText": "X",
         "MaterialGroup": "Z431", "Vendor": "K"},
        {"MaterialID": "P-STRING", "VendorPart": "B", "MaterialText": "Y",
         "MaterialGroup": "Z431", "Vendor": "K"},
    ]

    _patch_client_and_invoke(monkeypatch, [
        _prices_response(raw_items),
        _md_response(md_items),
    ])

    ResursMediaApiFetcher().fetch_and_save()

    rows = db_session.execute(text(
        "SELECT supplier_sku, price FROM supplier_prices "
        "WHERE supplier_sku IN ('P-FLOAT','P-STRING') ORDER BY supplier_sku"
    )).all()
    by_sku = {r.supplier_sku: Decimal(r.price) for r in rows}
    assert by_sku["P-FLOAT"]  == Decimal("188.49")
    assert by_sku["P-STRING"] == Decimal("200.10")


# =====================================================================
# 12. Item-обёртка в MaterialGroup_Tab и MaterialID_Tab
# =====================================================================

def test_call_arguments_use_item_wrapper(resurs_media_env, monkeypatch):
    """Проверяем, что и MaterialGroup_Tab (в GetPrices), и MaterialID_Tab
    (в GetMaterialData) обёрнуты в {"Item": [...]} — без обёртки сервер
    отвечает 'expected element MaterialGroup_Tab/Item ...'."""
    from portal.services.configurator.auto_price.fetchers.resurs_media import ResursMediaApiFetcher

    raw_items = [{
        "MaterialID":     "MB-1",
        "Price":          15000.0,
        "AvailableCount": "1",
    }]
    md_items = [{
        "MaterialID":    "MB-1",
        "VendorPart":    "B760",
        "MaterialText":  "ASUS B760",
        "MaterialGroup": "Z999-10006",  # motherboard
        "Vendor":        "ASUS",
    }]

    calls = _patch_client_and_invoke(monkeypatch, [
        _prices_response(raw_items),
        _md_response(md_items),
    ])

    ResursMediaApiFetcher().fetch_and_save()

    assert len(calls) == 2
    # GetPrices.MaterialGroup_Tab → {"Item": [{"MaterialGroup": ...}, ...]}
    op1, kwargs1 = calls[0]
    assert op1 == "GetPrices"
    tab1 = kwargs1["MaterialGroup_Tab"]
    assert "Item" in tab1 and isinstance(tab1["Item"], list)
    assert all("MaterialGroup" in it for it in tab1["Item"])

    # GetMaterialData.MaterialID_Tab → {"Item": [{"MaterialID": ...}, ...]}
    op2, kwargs2 = calls[1]
    assert op2 == "GetMaterialData"
    tab2 = kwargs2["MaterialID_Tab"]
    assert "Item" in tab2 and isinstance(tab2["Item"], list)
    assert all("MaterialID" in it for it in tab2["Item"])
    assert tab2["Item"] == [{"MaterialID": "MB-1"}]
    # Доп.флаги enrichment'а — выключены (нам нужны только поля для PriceRow).
    assert kwargs2["WithCharacteristics"] is False
    assert kwargs2["WithBarCodes"] is False
    assert kwargs2["WithCertificates"] is False
    assert kwargs2["WithImages"] is False


# =====================================================================
# Доп. — проверка отсутствия кредов
# =====================================================================

def test_init_raises_without_credentials(monkeypatch):
    """Без RESURS_MEDIA_WSDL_URL/USERNAME/PASSWORD — RuntimeError с
    понятным списком ожидаемых переменных."""
    from portal.services.configurator.auto_price.fetchers.resurs_media import ResursMediaApiFetcher

    monkeypatch.delenv("RESURS_MEDIA_WSDL_URL", raising=False)
    monkeypatch.delenv("RESURS_MEDIA_USERNAME", raising=False)
    monkeypatch.delenv("RESURS_MEDIA_PASSWORD", raising=False)

    with pytest.raises(RuntimeError) as ei:
        ResursMediaApiFetcher()
    msg = str(ei.value)
    assert "RESURS_MEDIA_WSDL_URL" in msg
    assert "RESURS_MEDIA_USERNAME" in msg
    assert "RESURS_MEDIA_PASSWORD" in msg


# =====================================================================
# Доп. — _parse_rate_limit_seconds на разных формах ErrorMessage
# =====================================================================

def test_parse_rate_limit_seconds_from_error_message():
    from portal.services.configurator.auto_price.fetchers.resurs_media import _parse_rate_limit_seconds

    assert _parse_rate_limit_seconds("Разрешенный интервал 60 сек.") == 60
    assert _parse_rate_limit_seconds("limit 5 сек ") == 5
    # Без числа → дефолт.
    assert _parse_rate_limit_seconds("not a rate limit") == 65
    assert _parse_rate_limit_seconds("") == 65
    assert _parse_rate_limit_seconds(None) == 65
