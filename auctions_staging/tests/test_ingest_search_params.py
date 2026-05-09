from __future__ import annotations

from app.modules.auctions.ingest.search import build_search_params


def test_build_search_params_includes_ktru_code_name_list():
    params = build_search_params(
        "26.20.18.000-00000001",
        "Многофункциональное устройство (МФУ)",
        page_number=1,
    )
    assert params["ktruCodeNameList"] == (
        "26.20.18.000-00000001&&&Многофункциональное устройство (МФУ)"
    )
    assert params["ktruSelectedPageNum"] == "1"


def test_build_search_params_drops_legacy_filters():
    """После миграции 0009 ингест больше не использует searchString и publishDate*-параметры
    (структурированный KTRU-фильтр работает без них)."""
    params = build_search_params("26.20.16.120-00000001", "Принтер", page_number=1)
    assert "searchString" not in params
    assert "publishDateFrom" not in params
    assert "publishDateTo" not in params
    assert "applSubmissionCloseDateFrom" not in params
    assert "applSubmissionCloseDateTo" not in params
    assert "pc" not in params  # pc=on убран — собственник проверял, мешает


def test_build_search_params_keeps_required_filters():
    params = build_search_params("26.20.16.120-00000001", "Принтер", page_number=2)
    assert params["fz44"] == "on"
    assert params["af"] == "on"
    assert params["morphology"] == "on"
    assert params["sortBy"] == "UPDATE_DATE"
    assert params["sortDirection"] == "false"
    assert params["currencyIdGeneral"] == "-1"
    assert params["showLotsInfoHidden"] == "false"
    assert params["pageNumber"] == 2
    assert params["recordsPerPage"] == "_50"


def test_build_search_params_records_per_page_overridable():
    params = build_search_params(
        "26.20.16.120-00000001", "Принтер", page_number=1, records_per_page=10
    )
    assert params["recordsPerPage"] == "_10"
