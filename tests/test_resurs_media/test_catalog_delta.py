# Тесты сервиса resurs_media_catalog: compute_delta + upsert_catalog
# (мини-этап 2026-05-12 «Resurs Media GetMaterialData инкрементальная
# дельта»).
#
# Pure-тесты не дёргают SOAP — для compute_delta достаточно реальной
# тестовой БД (фикстура db_engine), для upsert_catalog тоже. Integration-
# тесты сценария runner'а строятся вокруг monkeypatch'инга
# ResursMediaApiFetcher._invoke / _get_client (тот же приём, что в
# test_auto_price/test_resurs_media_fetcher.py).
#
# resurs_media_catalog зачищается перед каждым тестом локальным
# conftest'ом (TRUNCATE), миграция 0037 применяется корневым conftest'ом.

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text

from portal.services.configurator.auto_price.resurs_media_catalog import (
    compute_delta,
    upsert_catalog,
)


# ---- helpers -----------------------------------------------------------

def _md_response(items: list[dict]) -> dict:
    """Эмулируем ответ SOAP-операции GetMaterialData."""
    return {
        "Result": 0,
        "ErrorMessage": None,
        "MaterialData_Tab": {"Item": items},
    }


def _seed_row(
    db_engine,
    material_id: str,
    *,
    vendor: str = "Vendor-X",
    vendor_part: str = "VP-1",
    material_text: str = "Description",
    material_group: str = "Z100189",
    synced_days_ago: int = 0,
) -> None:
    """Прямой INSERT в resurs_media_catalog с явно заданным synced_at —
    нужен, чтобы тестировать stale-логику без time-travel'а."""
    with db_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO resurs_media_catalog "
                "    (material_id, vendor, vendor_part, material_text, "
                "     material_group, raw_jsonb, synced_at) "
                "VALUES (:mid, :v, :vp, :mt, :mg, "
                "        CAST('{}' AS JSONB), "
                "        NOW() - make_interval(days => :d))"
            ),
            {
                "mid": material_id,
                "v":   vendor,
                "vp":  vendor_part,
                "mt":  material_text,
                "mg":  material_group,
                "d":   synced_days_ago,
            },
        )


def _count_catalog(db_engine) -> int:
    with db_engine.begin() as conn:
        return int(conn.execute(
            text("SELECT COUNT(*) FROM resurs_media_catalog")
        ).scalar() or 0)


def _select_catalog_row(db_engine, material_id: str) -> dict[str, Any] | None:
    with db_engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT material_id, vendor, vendor_part, material_text, "
                "       material_group, weight, vat, raw_jsonb, "
                "       (NOW() - synced_at) AS age "
                "  FROM resurs_media_catalog WHERE material_id = :mid"
            ),
            {"mid": material_id},
        ).first()
    return dict(row._mapping) if row else None


# =====================================================================
# compute_delta — pure
# =====================================================================

def test_compute_delta_empty_table_all_to_fetch(db_engine):
    """1. Таблица пустая, 100 ID на входе → все 100 в ids_to_fetch,
    cached_data пуст."""
    ids_in = [f"MID-{i:03d}" for i in range(100)]

    ids_to_fetch, cached = compute_delta(db_engine, ids_in)

    assert sorted(ids_to_fetch) == sorted(ids_in)
    assert cached == {}


def test_compute_delta_all_fresh_nothing_to_fetch(db_engine):
    """2. 100 ID в БД (synced_at=now()), те же 100 на входе → 0 в
    ids_to_fetch, все 100 в cached_data."""
    ids_in = [f"MID-{i:03d}" for i in range(100)]
    for mid in ids_in:
        _seed_row(db_engine, mid, vendor_part=f"VP-{mid}")

    ids_to_fetch, cached = compute_delta(db_engine, ids_in)

    assert ids_to_fetch == []
    assert set(cached.keys()) == set(ids_in)
    # И поля корректно прочитаны.
    sample = cached[ids_in[0]]
    assert sample == {
        "vendor":         "Vendor-X",
        "vendor_part":    f"VP-{ids_in[0]}",
        "material_text":  "Description",
        "material_group": "Z100189",
    }


def test_compute_delta_partial_stale(db_engine):
    """3. 100 ID в БД, 30 из них stale (synced_at > 30 дней назад) →
    30 в ids_to_fetch, 70 в cached_data."""
    fresh_ids = [f"FRESH-{i:03d}" for i in range(70)]
    stale_ids = [f"STALE-{i:03d}" for i in range(30)]
    for mid in fresh_ids:
        _seed_row(db_engine, mid, synced_days_ago=5)
    for mid in stale_ids:
        _seed_row(db_engine, mid, synced_days_ago=45)

    ids_to_fetch, cached = compute_delta(
        db_engine, fresh_ids + stale_ids,
    )

    assert sorted(ids_to_fetch) == sorted(stale_ids)
    assert set(cached.keys()) == set(fresh_ids)


def test_compute_delta_mix_new_stale_fresh(db_engine):
    """4. На входе 30 новых (не в БД), 30 stale (в БД >30 дней),
    40 fresh (в БД <30 дней). Ожидаем: 60 → ids_to_fetch, 40 → cached."""
    new_ids   = [f"NEW-{i}"   for i in range(30)]
    stale_ids = [f"STALE-{i}" for i in range(30)]
    fresh_ids = [f"FRESH-{i}" for i in range(40)]
    for mid in stale_ids:
        _seed_row(db_engine, mid, synced_days_ago=60)
    for mid in fresh_ids:
        _seed_row(db_engine, mid, synced_days_ago=10)

    ids_to_fetch, cached = compute_delta(
        db_engine, new_ids + stale_ids + fresh_ids,
    )

    assert sorted(ids_to_fetch) == sorted(new_ids + stale_ids)
    assert set(cached.keys()) == set(fresh_ids)


def test_compute_delta_custom_stale_threshold(db_engine):
    """5. С stale_after=7 дней — позиция, которой 10 дней, считается stale
    (а на дефолте 30 дней — fresh)."""
    _seed_row(db_engine, "T-10D", synced_days_ago=10)
    _seed_row(db_engine, "T-1D",  synced_days_ago=1)

    # default 30d: обе fresh.
    ids_default, cached_default = compute_delta(db_engine, ["T-10D", "T-1D"])
    assert ids_default == []
    assert set(cached_default.keys()) == {"T-10D", "T-1D"}

    # custom 7d: T-10D становится stale.
    ids_custom, cached_custom = compute_delta(
        db_engine, ["T-10D", "T-1D"], stale_after=timedelta(days=7),
    )
    assert ids_custom == ["T-10D"]
    assert set(cached_custom.keys()) == {"T-1D"}


def test_compute_delta_empty_input(db_engine):
    """6.1 (bonus). Пустой вход → пустые выходы, не падаем."""
    assert compute_delta(db_engine, []) == ([], {})
    # Список из пустых строк / None — тоже корректно отфильтрован.
    assert compute_delta(db_engine, ["", "   "]) == ([], {})


# =====================================================================
# upsert_catalog — pure
# =====================================================================

def test_upsert_inserts_new_row(db_engine):
    """6. INSERT новой строки — все поля корректно записаны, synced_at
    близок к NOW()."""
    response = _md_response([{
        "MaterialID":         "RAM-001",
        "PartNum":            "PN-1",
        "MaterialText":       "Kingston DDR4 16GB",
        "MaterialGroup":      "Z100189",
        "Vendor":             "Kingston",
        "VendorPart":         "DDR4-16",
        "UnitOfMeasurement":  "PC",
        "Multiplicity":       "1",
        "Weight":             "0.05",
        "VAT":                "20.00",
    }])

    counters = upsert_catalog(db_engine, response)

    assert counters == {"inserted": 1, "updated": 0, "errors": 0}
    row = _select_catalog_row(db_engine, "RAM-001")
    assert row is not None
    assert row["vendor"]        == "Kingston"
    assert row["vendor_part"]   == "DDR4-16"
    assert row["material_text"] == "Kingston DDR4 16GB"
    assert row["material_group"] == "Z100189"
    assert row["weight"]        == Decimal("0.05")
    assert row["vat"]           == Decimal("20.00")
    # raw_jsonb — JSONB, в SQLAlchemy 2 приходит как dict.
    assert row["raw_jsonb"]["MaterialID"] == "RAM-001"
    assert row["raw_jsonb"]["Vendor"]     == "Kingston"
    # synced_at в пределах разумного окна от теперь (1 секунда).
    assert row["age"].total_seconds() < 5


def test_upsert_updates_existing_row(db_engine):
    """7. UPDATE существующего material_id — поля перезаписаны,
    synced_at обновлён, счётчик updated=1."""
    # Сначала seed'им устаревшую версию (с старым synced_at).
    _seed_row(
        db_engine, "RAM-002",
        vendor="OLD-VENDOR", vendor_part="OLD-VP",
        material_text="OLD TEXT", material_group="Z000",
        synced_days_ago=40,
    )

    response = _md_response([{
        "MaterialID":    "RAM-002",
        "MaterialText":  "NEW TEXT",
        "MaterialGroup": "Z100189",
        "Vendor":        "NEW-VENDOR",
        "VendorPart":    "NEW-VP",
    }])

    counters = upsert_catalog(db_engine, response)

    assert counters == {"inserted": 0, "updated": 1, "errors": 0}
    row = _select_catalog_row(db_engine, "RAM-002")
    assert row is not None
    assert row["vendor"]        == "NEW-VENDOR"
    assert row["vendor_part"]   == "NEW-VP"
    assert row["material_text"] == "NEW TEXT"
    assert row["material_group"] == "Z100189"
    # synced_at стал свежим — меньше 5 сек назад.
    assert row["age"].total_seconds() < 5


def test_upsert_saves_full_raw_jsonb(db_engine):
    """8. raw_jsonb содержит ПОЛНЫЙ ответ (включая nested BarCodes,
    MaterialCharacteristics, Images) — UPSERT не дискриминирует поля."""
    full_item = {
        "MaterialID":   "RAM-003",
        "MaterialText": "with nested",
        "MaterialGroup": "Z100189",
        "BarCodes": {
            "Item": [{"BarCode": "1234567890"}, {"BarCode": "987654321"}],
        },
        "MaterialCharacteristics": {
            "Item": [
                {"Characteristic": "Color", "Value": "Black"},
                {"Characteristic": "Capacity", "Value": "16 GB"},
            ],
        },
        "Images": {
            "Item": [{"ImageName": "front.jpg"}],
        },
    }
    response = _md_response([full_item])

    counters = upsert_catalog(db_engine, response)

    assert counters == {"inserted": 1, "updated": 0, "errors": 0}
    row = _select_catalog_row(db_engine, "RAM-003")
    assert row is not None
    raw = row["raw_jsonb"]
    assert raw["BarCodes"]["Item"][0]["BarCode"] == "1234567890"
    assert raw["BarCodes"]["Item"][1]["BarCode"] == "987654321"
    assert raw["MaterialCharacteristics"]["Item"][0]["Characteristic"] == "Color"
    assert raw["MaterialCharacteristics"]["Item"][1]["Value"]          == "16 GB"
    assert raw["Images"]["Item"][0]["ImageName"] == "front.jpg"


def test_upsert_skips_items_without_material_id(db_engine):
    """8.1 (bonus). Item без MaterialID → errors += 1, остальные пишутся."""
    response = _md_response([
        {"MaterialID": "OK-1", "MaterialGroup": "Z100189", "Vendor": "K"},
        {"MaterialID": "",     "MaterialGroup": "Z100189", "Vendor": "K"},  # пустой
        {"MaterialID": "OK-2", "MaterialGroup": "Z100189", "Vendor": "K"},
    ])

    counters = upsert_catalog(db_engine, response)

    assert counters == {"inserted": 2, "updated": 0, "errors": 1}
    assert _count_catalog(db_engine) == 2


def test_upsert_empty_response(db_engine):
    """8.2 (bonus). Пустой Tab или None → counters=0."""
    assert upsert_catalog(db_engine, {"MaterialData_Tab": []}) == {
        "inserted": 0, "updated": 0, "errors": 0,
    }
    assert upsert_catalog(db_engine, {"MaterialData_Tab": None}) == {
        "inserted": 0, "updated": 0, "errors": 0,
    }
    assert _count_catalog(db_engine) == 0


def test_upsert_batches_large_input_into_chunks(db_engine):
    """8.3 (bonus). Большой вход (2500 item'ов) → batched UPSERT
    проходит за несколько chunk'ов (chunk_size=1000) и все 2500
    позиций оказываются в БД. Проверяем счётчики, число строк
    в catalog и корректность данных на репрезентативных позициях.

    Этот тест защищает chunked-flow: per-item commit без батчинга
    на удалённой Railway-БД даёт ~80 row/min из-за сетевой latency.
    Если кто-то откатит chunked-логику, тест продолжит проходить
    (он не измеряет время), поэтому он именно за корректность —
    защита от регрессии «batch SQL некорректно собирает параметры»
    или «RETURNING на multi-row UPSERT возвращает не то».
    """
    items = [
        {
            "MaterialID":    f"BATCH-{i:05d}",
            "PartNum":       f"PN-{i}",
            "MaterialText":  f"Item #{i}",
            "MaterialGroup": "Z100189",
            "Vendor":        "Kingston",
            "VendorPart":    f"VP-{i}",
            "Weight":        "0.05",
            "VAT":           "20.00",
        }
        for i in range(2500)
    ]
    response = _md_response(items)

    # chunk_size=1000 → ровно 2 полных chunk'а + остаток 500.
    counters = upsert_catalog(db_engine, response, chunk_size=1000)

    assert counters == {"inserted": 2500, "updated": 0, "errors": 0}
    assert _count_catalog(db_engine) == 2500

    # Точечная сверка: первый, средний и последний item должны быть
    # записаны корректно (а не только число строк).
    for idx in (0, 1249, 2499):
        row = _select_catalog_row(db_engine, f"BATCH-{idx:05d}")
        assert row is not None, f"BATCH-{idx:05d} не найдена"
        assert row["vendor"]        == "Kingston"
        assert row["vendor_part"]   == f"VP-{idx}"
        assert row["material_text"] == f"Item #{idx}"
        assert row["weight"]        == Decimal("0.05")

    # Повторный UPSERT тем же набором → все 2500 должны стать updated,
    # ни одной новой строки в БД не добавится.
    counters_again = upsert_catalog(db_engine, response, chunk_size=1000)
    assert counters_again == {"inserted": 0, "updated": 2500, "errors": 0}
    assert _count_catalog(db_engine) == 2500


# =====================================================================
# Integration: fetcher.fetch_and_save() с delta
# =====================================================================

class _FakeClient:
    pass


def _patch_client_and_invoke(monkeypatch, response_or_handler):
    """Тот же helper, что в tests/test_auto_price/test_resurs_media_fetcher.py.
    Дублирован тут, чтобы тесты модуля resurs_media были самодостаточны."""
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


@pytest.fixture()
def _resurs_media_env(monkeypatch):
    """Локальная копия фикстуры из tests/test_auto_price/conftest.py —
    чтобы не зависеть от fixture-видимости между подкаталогами тестов."""
    monkeypatch.setenv(
        "RESURS_MEDIA_WSDL_URL", "https://test.example/ws/WSAPI?wsdl",
    )
    monkeypatch.setenv("RESURS_MEDIA_USERNAME", "test_user")
    monkeypatch.setenv("RESURS_MEDIA_PASSWORD", "test_password")


@pytest.fixture(autouse=True)
def _clean_price_load_tables(db_engine):
    """Для integration-тестов fetcher.fetch_and_save нам нужно, чтобы
    supplier_prices / unmapped_supplier_items / price_uploads тоже были
    пусты. resurs_media_catalog truncate'ится в _truncate_resurs_media_tables
    (corresponding autouse в conftest)."""
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE "
            "  unmapped_supplier_items, supplier_prices, price_uploads, "
            "  suppliers, "
            "  cpus, motherboards, rams, gpus, storages, cases, psus, coolers "
            "RESTART IDENTITY CASCADE"
        ))
    yield


def test_full_flow_empty_table_populates_catalog(
    _resurs_media_env, monkeypatch, db_engine,
):
    """9. Пустая resurs_media_catalog → fetcher вызывает GetMaterialData
    по ВСЕМ 5 ID. После run'а 5 строк в БД с synced_at ≈ now()."""
    from portal.services.configurator.auto_price.fetchers.resurs_media import (
        ResursMediaApiFetcher,
    )

    prices = [
        {"MaterialID": f"MID-{i}", "Price": 1000.0 + i, "AvailableCount": "1"}
        for i in range(5)
    ]
    md_items = [
        {
            "MaterialID":    f"MID-{i}",
            "VendorPart":    f"VP-{i}",
            "MaterialText":  f"Item {i}",
            "MaterialGroup": "Z100189",  # ram
            "Vendor":        "Kingston",
        }
        for i in range(5)
    ]

    calls = _patch_client_and_invoke(monkeypatch, [
        {"Result": 0, "Material_Tab": prices},
        _md_response(md_items),
    ])

    upload_id = ResursMediaApiFetcher().fetch_and_save()
    assert upload_id > 0

    # Проверяем, что в catalog все 5 позиций.
    assert _count_catalog(db_engine) == 5
    # И что GetMaterialData позвали РОВНО по 5 ID (полному списку,
    # потому что catalog был пуст).
    md_call = next(c for c in calls if c[0] == "GetMaterialData")
    sent_ids = [it["MaterialID"] for it in md_call[1]["MaterialID_Tab"]["Item"]]
    assert sorted(sent_ids) == [f"MID-{i}" for i in range(5)]


def test_full_flow_fresh_cache_skips_get_material_data(
    _resurs_media_env, monkeypatch, db_engine,
):
    """10. Все 5 ID в catalog уже fresh → fetcher НЕ вызывает GetMaterialData
    (только GetPrices). assert_not_called эквивалент: только 1 call с
    operation='GetPrices'."""
    from portal.services.configurator.auto_price.fetchers.resurs_media import (
        ResursMediaApiFetcher,
    )

    # Pre-seed: все 5 ID fresh, материал-группа Z100189 (ram), vendor — Kingston.
    for i in range(5):
        _seed_row(
            db_engine, f"MID-{i}",
            vendor="Kingston", vendor_part=f"VP-{i}",
            material_text=f"Item {i}", material_group="Z100189",
            synced_days_ago=1,
        )

    prices = [
        {"MaterialID": f"MID-{i}", "Price": 1000.0 + i, "AvailableCount": "1"}
        for i in range(5)
    ]

    calls = _patch_client_and_invoke(monkeypatch, [
        {"Result": 0, "Material_Tab": prices},
        # Второй ответ не нужен — GetMaterialData вообще не должен зваться.
    ])

    upload_id = ResursMediaApiFetcher().fetch_and_save()
    assert upload_id > 0

    # SOAP позвали ровно один раз — GetPrices.
    assert [c[0] for c in calls] == ["GetPrices"]
    # И записи в catalog остались (никто их не дублировал).
    assert _count_catalog(db_engine) == 5
