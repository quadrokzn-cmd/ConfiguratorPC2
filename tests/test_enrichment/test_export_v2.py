# Тесты экспорта/импорта Claude Code этапа 11.6.2.1.
#
# Проверяем три новых аспекта на стороне exporter:
#   1) фильтр not_applicable_* в component_field_sources;
#   2) присутствие mpn / gtin / raw_names в каждом item;
#   3) идемпотентность по pending/done/archive.
#
# И четыре аспекта на стороне importer:
#   4) отклонение значений вне диапазона;
#   5) отклонение URL не из whitelist'а;
#   6) запись в БД с source='claude_code' и source_detail='from_web_search';
#   7) dry-run не пишет в БД и не двигает файлы.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from app.services.enrichment.claude_code import exporter as exporter_mod
from app.services.enrichment.claude_code import importer as importer_mod
from app.services.enrichment.claude_code.exporter import export_category
from app.services.enrichment.claude_code.importer import (
    import_category,
    import_file,
)
from app.services.enrichment.claude_code.schema import (
    SOURCE_DETAIL_WEB_SEARCH,
    SOURCE_NAME,
)


# ---------------------------------------------------------------------------
# Хелперы
# ---------------------------------------------------------------------------


@pytest.fixture
def enrichment_tmp(tmp_path, monkeypatch):
    """Подменяет корневую папку enrichment/ на временную, чтобы тесты не
    задевали реальные файлы в репозитории."""
    monkeypatch.setattr(exporter_mod, "ENRICHMENT_ROOT", tmp_path)
    monkeypatch.setattr(importer_mod, "ENRICHMENT_ROOT", tmp_path)
    return tmp_path


def _insert_supplier(db_session, name: str = "OCS") -> int:
    return db_session.execute(
        text("INSERT INTO suppliers (name) VALUES (:n) RETURNING id"),
        {"n": name},
    ).scalar_one()


def _insert_case(db_session, **fields) -> int:
    cols = ["model", "manufacturer"]
    params = {"model": fields.pop("model", "TestCase"),
              "manufacturer": fields.pop("manufacturer", "TestVendor")}
    for k, v in fields.items():
        cols.append(k)
        params[k] = v
    sql = (
        f"INSERT INTO cases ({', '.join(cols)}) VALUES "
        f"({', '.join(':' + c for c in cols)}) RETURNING id"
    )
    return db_session.execute(text(sql), params).scalar_one()


def _insert_gpu(db_session, **fields) -> int:
    cols = ["model", "manufacturer"]
    params = {"model": fields.pop("model", "TestGPU"),
              "manufacturer": fields.pop("manufacturer", "TestVendor")}
    for k, v in fields.items():
        cols.append(k)
        params[k] = v
    sql = (
        f"INSERT INTO gpus ({', '.join(cols)}) VALUES "
        f"({', '.join(':' + c for c in cols)}) RETURNING id"
    )
    return db_session.execute(text(sql), params).scalar_one()


def _attach_price(
    db_session, *, supplier_id: int, category: str, component_id: int,
    raw_name: str, supplier_sku: str = "SKU-1",
) -> None:
    db_session.execute(text(
        "INSERT INTO supplier_prices "
        "    (supplier_id, category, component_id, supplier_sku, "
        "     price, stock_qty, transit_qty, raw_name) "
        "VALUES (:supplier_id, :category, :component_id, :supplier_sku, "
        "        100.0, 1, 0, :raw_name)"
    ), {
        "supplier_id":  supplier_id,
        "category":     category,
        "component_id": component_id,
        "supplier_sku": supplier_sku,
        "raw_name":     raw_name,
    })


def _mark_not_applicable(
    db_session, *, category: str, component_id: int, field_name: str,
    detail: str,
) -> None:
    db_session.execute(text(
        "INSERT INTO component_field_sources "
        "    (category, component_id, field_name, source, confidence, "
        "     source_url, source_detail, updated_at) "
        "VALUES (:cat, :cid, :fname, 'derived', 1.0, NULL, :detail, NOW())"
    ), {"cat": category, "cid": component_id, "fname": field_name,
        "detail": detail})


def _read_batch(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1) фильтр not_applicable
# ---------------------------------------------------------------------------


def test_export_filters_not_applicable_per_field(
    db_session, enrichment_tmp,
):
    """Корпус с has_psu_included=NULL и пометкой not_applicable_no_psu на
    included_psu_watts: должен попасть в batch для has_psu_included
    (это другое поле, не помеченное), но без included_psu_watts в to_fill.

    Поскольку included_psu_watts вообще не входит в TARGET_FIELDS
    первого прогона case (он во втором), то фактически тест проверяет
    обратную связь: пометка not_applicable на included_psu_watts НЕ
    блокирует первый прогон.
    """
    cid = _insert_case(
        db_session,
        model="TestCase-NoPSU",
        has_psu_included=None,
        supported_form_factors=None,
    )
    # пометим included_psu_watts как not_applicable (как делает rule_2)
    _mark_not_applicable(
        db_session, category="case", component_id=cid,
        field_name="included_psu_watts", detail="not_applicable_no_psu",
    )
    db_session.commit()

    result = export_category("case", batch_size=10)
    assert result["status"] == "success"
    assert result["exported"] == 1
    batches = result["batches"]
    assert len(batches) == 1

    payload = _read_batch(enrichment_tmp / "pending" / "case" / batches[0])
    items = payload["items"]
    assert len(items) == 1
    item = items[0]
    assert item["id"] == cid
    # included_psu_watts на 1-м прогоне нет в target_fields, но проверим
    # что в to_fill только has_psu_included и supported_form_factors:
    assert set(item["to_fill"]) == {"has_psu_included", "supported_form_factors"}


def test_export_filters_not_applicable_drops_item_when_only_field(
    db_session, enrichment_tmp,
):
    """Если у компонента единственное NULL-поле помечено not_applicable —
    компонент в batch не попадает совсем."""
    # Корпус с has_psu_included=TRUE и included_psu_watts=NULL —
    # кандидат для 2-го прогона. Если на included_psu_watts стоит
    # not_applicable, item должен быть отфильтрован (filtered_not_applicable).
    cid = _insert_case(
        db_session,
        model="TestCase-MarkedNA",
        has_psu_included=True,
        included_psu_watts=None,
    )
    _mark_not_applicable(
        db_session, category="case", component_id=cid,
        field_name="included_psu_watts", detail="not_applicable_no_psu",
    )
    db_session.commit()

    result = export_category("case", batch_size=10, case_psu_pass=True)
    assert result["status"] == "success"
    assert result["exported"] == 0
    assert result["filtered_not_applicable"] == 1
    assert result["batches"] == []


# ---------------------------------------------------------------------------
# 1b) Фильтр is_hidden (этап 11.6.2.5.1b)
# ---------------------------------------------------------------------------


def test_export_skips_hidden_components(db_session, enrichment_tmp):
    """Скрытые компоненты (is_hidden=TRUE) не выгружаются на AI-обогащение,
    даже если у них NULL целевые поля.

    На проде этап 11.6.2.5.0c пометил ~97 PSU как is_hidden=TRUE
    (скелеты, не-PSU, адаптеры). До 11.6.2.5.1b exporter их игнорировал
    и выгружал в pending/, что приводило к лишним 97 items в batch'ах.
    """
    visible_id = _insert_gpu(
        db_session, model="Visible-GPU", manufacturer="MSI", sku="VIS-1",
    )
    hidden_id = _insert_gpu(
        db_session, model="Hidden-GPU", manufacturer="MSI", sku="HID-1",
        is_hidden=True,
    )
    db_session.commit()

    result = export_category("gpu", batch_size=10)
    assert result["status"] == "success"
    assert result["exported"] == 1

    payload = _read_batch(enrichment_tmp / "pending" / "gpu" / result["batches"][0])
    item_ids = [it["id"] for it in payload["items"]]
    assert visible_id in item_ids
    assert hidden_id not in item_ids


# ---------------------------------------------------------------------------
# 2) raw_names + mpn + gtin
# ---------------------------------------------------------------------------


def test_export_includes_all_raw_names(db_session, enrichment_tmp):
    """В JSON должен попасть массив всех уникальных raw_name от поставщиков."""
    sup1 = _insert_supplier(db_session, name="OCS")
    sup2 = _insert_supplier(db_session, name="Treolan")
    sup3 = _insert_supplier(db_session, name="Merlion")

    cid = _insert_gpu(
        db_session,
        model="TestGPU-RTX-X",
        manufacturer="ASUS",
        sku="ROG-STRIX-RTX-X",
        gtin="4711081000001",
        # все целевые поля NULL — попадает в выгрузку
    )
    raw_names_in = [
        "ASUS ROG STRIX GeForce RTX X 16GB OC",
        "ASUS ROG-STRIX-RTX-X-O16G GAMING",
        "ASUS GeForce RTX X 16Gb GDDR7 ROG STRIX OC",
    ]
    for i, (sup, name) in enumerate(zip(
        [sup1, sup2, sup3], raw_names_in,
    )):
        _attach_price(
            db_session, supplier_id=sup, category="gpu",
            component_id=cid, raw_name=name, supplier_sku=f"X-{i}",
        )
    db_session.commit()

    result = export_category("gpu", batch_size=10)
    assert result["exported"] == 1
    payload = _read_batch(enrichment_tmp / "pending" / "gpu" / result["batches"][0])
    item = payload["items"][0]
    assert item["mpn"] == "ROG-STRIX-RTX-X"
    assert item["gtin"] == "4711081000001"
    assert sorted(item["raw_names"]) == sorted(raw_names_in)


def test_export_dedups_raw_names(db_session, enrichment_tmp):
    """Если разные поставщики прислали одинаковые raw_name — в JSON один раз."""
    sup1 = _insert_supplier(db_session, name="A")
    sup2 = _insert_supplier(db_session, name="B")
    cid = _insert_gpu(db_session, model="DupGPU", manufacturer="ASUS",
                      sku="DUP-1")
    name = "ASUS GeForce RTX 5060 Ti 16G"
    _attach_price(db_session, supplier_id=sup1, category="gpu",
                  component_id=cid, raw_name=name, supplier_sku="A")
    _attach_price(db_session, supplier_id=sup2, category="gpu",
                  component_id=cid, raw_name=name, supplier_sku="B")
    db_session.commit()

    result = export_category("gpu", batch_size=10)
    payload = _read_batch(enrichment_tmp / "pending" / "gpu" / result["batches"][0])
    item = payload["items"][0]
    assert item["raw_names"] == [name]


# ---------------------------------------------------------------------------
# 3) Идемпотентность
# ---------------------------------------------------------------------------


def test_export_idempotent_via_pending_done_archive(
    db_session, enrichment_tmp,
):
    """Первый прогон выгружает компонент в pending; второй прогон —
    skipped_known, файлы не плодятся."""
    _insert_gpu(db_session, model="Idem-GPU-1", manufacturer="MSI",
                sku="IDEM-1")
    db_session.commit()

    r1 = export_category("gpu", batch_size=10)
    assert r1["exported"] == 1
    assert r1["skipped_known"] == 0
    assert len(r1["batches"]) == 1

    r2 = export_category("gpu", batch_size=10)
    assert r2["exported"] == 0
    assert r2["skipped_known"] == 1
    assert r2["batches"] == []


def test_export_idempotent_across_done_and_archive(
    db_session, enrichment_tmp,
):
    """Если компонент уже лежит в done/ или archive/ — повторно не
    выгружаем."""
    cid = _insert_gpu(db_session, model="Already-done", manufacturer="MSI",
                      sku="DONE-1")
    db_session.commit()

    # Имитируем: компонент уже обогащён, файл лежит в archive/
    archive_dir = enrichment_tmp / "archive" / "gpu"
    archive_dir.mkdir(parents=True)
    (archive_dir / "batch_001.json").write_text(
        json.dumps({
            "category": "gpu",
            "batch_id": "batch_001",
            "items": [{"id": cid, "to_fill": ["tdp_watts"]}],
        }), encoding="utf-8",
    )

    r = export_category("gpu", batch_size=10)
    assert r["exported"] == 0
    assert r["skipped_known"] == 1


def test_batch_filename_has_timestamp_and_category(
    db_session, enrichment_tmp,
):
    """Имя нового batch-файла должно соответствовать формату 11.6.2.1."""
    _insert_gpu(db_session, model="TS-GPU", manufacturer="MSI", sku="TS-1")
    db_session.commit()

    r = export_category("gpu", batch_size=10)
    assert len(r["batches"]) == 1
    name = r["batches"][0]
    # batch_NNN_<category>_<UTC-timestamp>.json
    assert name.startswith("batch_")
    assert "_gpu_" in name
    assert name.endswith(".json")


def test_max_batches_limits_export(db_session, enrichment_tmp):
    """--max-batches должен ограничить число файлов; кандидаты-не-в-batch
    остаются доступными для следующего прогона."""
    for i in range(5):
        _insert_gpu(db_session, model=f"Multi-{i}", manufacturer="MSI",
                    sku=f"M-{i}")
    db_session.commit()

    r1 = export_category("gpu", batch_size=2, max_batches=1)
    assert len(r1["batches"]) == 1
    assert r1["exported"] == 2

    r2 = export_category("gpu", batch_size=2, max_batches=1)
    assert len(r2["batches"]) == 1
    assert r2["exported"] == 2

    r3 = export_category("gpu", batch_size=2, max_batches=1)
    # последний компонент
    assert len(r3["batches"]) == 1
    assert r3["exported"] == 1

    r4 = export_category("gpu", batch_size=2, max_batches=1)
    assert len(r4["batches"]) == 0
    assert r4["exported"] == 0


# ---------------------------------------------------------------------------
# 4-7) Импортёр
# ---------------------------------------------------------------------------


def _write_done_file(enrichment_tmp: Path, category: str, payload: dict,
                    name: str = "batch_001_done.json") -> Path:
    done_dir = enrichment_tmp / "done" / category
    done_dir.mkdir(parents=True, exist_ok=True)
    p = done_dir / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_import_validates_ranges_rejects_out_of_range(
    db_session, enrichment_tmp,
):
    """tdp_watts=1500 (вне диапазона 10-600) — REJECT."""
    cid = _insert_gpu(db_session, model="OOR-GPU", manufacturer="MSI",
                      sku="OOR-1")
    db_session.commit()

    _write_done_file(enrichment_tmp, "gpu", {
        "category": "gpu",
        "batch_id": "batch_001",
        "items": [{
            "id": cid,
            "fields": {
                "tdp_watts": {
                    "value": 1500,
                    "source_url": "https://www.msi.com/Graphics-Card/foo",
                },
            },
        }],
    })

    stats = import_category("gpu")
    assert stats["fields_accepted"] == 0
    assert stats["fields_rejected"] == 1
    assert any("out_of_range" in r for r in stats["reject_reasons"])

    db_session.commit()
    new_row = db_session.execute(
        text("SELECT tdp_watts FROM gpus WHERE id=:id"), {"id": cid},
    ).scalar()
    assert new_row is None


def test_import_rejects_non_official_url(db_session, enrichment_tmp):
    """source_url с маркетплейса — REJECT."""
    cid = _insert_gpu(db_session, model="Bad-URL-GPU", manufacturer="MSI",
                      sku="BAD-1")
    db_session.commit()

    _write_done_file(enrichment_tmp, "gpu", {
        "category": "gpu",
        "batch_id": "batch_002",
        "items": [{
            "id": cid,
            "fields": {
                "tdp_watts": {
                    "value": 250,
                    "source_url": "https://www.dns-shop.ru/product/foo",
                },
            },
        }],
    })

    stats = import_category("gpu")
    assert stats["fields_accepted"] == 0
    assert stats["fields_rejected"] == 1
    assert any("bad_domain" in r for r in stats["reject_reasons"])


def test_import_writes_to_cfs_with_from_web_search(
    db_session, enrichment_tmp,
):
    """После успешного импорта поле обновлено + в CFS source='claude_code',
    source_detail='from_web_search'."""
    cid = _insert_gpu(
        db_session, model="OK-GPU", manufacturer="MSI", sku="OK-1",
    )
    db_session.commit()

    _write_done_file(enrichment_tmp, "gpu", {
        "category": "gpu",
        "batch_id": "batch_003",
        "items": [{
            "id": cid,
            "fields": {
                "tdp_watts": {
                    "value": 285,
                    "source_url": "https://www.msi.com/Graphics-Card/foo/Specification",
                },
            },
        }],
    })

    stats = import_category("gpu")
    assert stats["fields_accepted"] == 1
    assert stats["fields_rejected"] == 0

    db_session.expire_all()
    new_tdp = db_session.execute(
        text("SELECT tdp_watts FROM gpus WHERE id=:id"), {"id": cid},
    ).scalar()
    assert new_tdp == 285

    cfs_row = db_session.execute(text(
        "SELECT source, source_detail, source_url "
        "  FROM component_field_sources "
        " WHERE category='gpu' AND component_id=:id AND field_name='tdp_watts'"
    ), {"id": cid}).first()
    assert cfs_row is not None
    assert cfs_row[0] == SOURCE_NAME == "claude_code"
    assert cfs_row[1] == SOURCE_DETAIL_WEB_SEARCH == "from_web_search"
    assert "msi.com" in cfs_row[2]


def test_import_dry_run_does_not_write_or_move_file(
    db_session, enrichment_tmp,
):
    """--dry-run: БД не меняется, файл остаётся в done/."""
    cid = _insert_gpu(
        db_session, model="DRY-GPU", manufacturer="MSI", sku="DRY-1",
    )
    db_session.commit()

    f = _write_done_file(enrichment_tmp, "gpu", {
        "category": "gpu",
        "batch_id": "batch_004",
        "items": [{
            "id": cid,
            "fields": {
                "tdp_watts": {
                    "value": 200,
                    "source_url": "https://www.msi.com/Graphics-Card/foo",
                },
            },
        }],
    })
    assert f.exists()

    stats = import_category("gpu", dry_run=True)
    assert stats["fields_accepted"] == 1

    # БД не тронута
    db_session.expire_all()
    new_tdp = db_session.execute(
        text("SELECT tdp_watts FROM gpus WHERE id=:id"), {"id": cid},
    ).scalar()
    assert new_tdp is None

    # CFS не тронут
    cfs = db_session.execute(text(
        "SELECT COUNT(*) FROM component_field_sources WHERE component_id=:id"
    ), {"id": cid}).scalar()
    assert cfs == 0

    # Файл по-прежнему в done/
    assert f.exists()
    archive = enrichment_tmp / "archive" / "gpu"
    if archive.exists():
        assert not list(archive.glob("*.json"))


def test_import_file_one_batch(db_session, enrichment_tmp):
    """import_file работает на одном файле и переносит его в archive/."""
    cid = _insert_gpu(
        db_session, model="One-File-GPU", manufacturer="ASUS", sku="OF-1",
    )
    db_session.commit()

    # Файл лежит вне done/
    custom_dir = enrichment_tmp / "custom"
    custom_dir.mkdir()
    f = custom_dir / "batch_010.json"
    f.write_text(json.dumps({
        "category": "gpu",
        "batch_id": "batch_010",
        "items": [{
            "id": cid,
            "fields": {
                "vram_gb": {
                    "value": 16,
                    "source_url": "https://www.asus.com/Graphics-Cards/foo",
                },
            },
        }],
    }), encoding="utf-8")

    stats = import_file(f)
    assert stats["fields_accepted"] == 1
    assert stats["files_done"] == 1
    db_session.expire_all()
    new = db_session.execute(
        text("SELECT vram_gb FROM gpus WHERE id=:id"), {"id": cid},
    ).scalar()
    assert new == 16
    # переместился в archive/gpu/
    archive = enrichment_tmp / "archive" / "gpu"
    assert (archive / "batch_010.json").exists()
    assert not f.exists()


# ---------------------------------------------------------------------------
# Новые валидаторы (cooler.supported_sockets, motherboard.socket/chipset)
# ---------------------------------------------------------------------------


def test_validate_cooler_supported_sockets():
    from app.services.enrichment.claude_code.validators import (
        ValidationError, validate_field,
    )

    vf = validate_field("cooler", "supported_sockets", {
        "value": ["AM5", "AM4", "LGA1700", "lga1851"],
        "source_url": "https://www.thermalright.com/product/foo",
    })
    # uppercase, без дублей, в порядке появления
    assert vf.value == ["AM5", "AM4", "LGA1700", "LGA1851"]

    with pytest.raises(ValidationError):
        validate_field("cooler", "supported_sockets", {
            "value": [],  # пустой список
            "source_url": "https://www.thermalright.com/product/foo",
        })


def test_validate_motherboard_socket_chipset():
    from app.services.enrichment.claude_code.validators import (
        ValidationError, validate_field,
    )

    vf = validate_field("motherboard", "socket", {
        "value": "lga1700",
        "source_url": "https://www.asus.com/motherboards/foo",
    })
    assert vf.value == "LGA1700"

    vf = validate_field("motherboard", "chipset", {
        "value": "Z790",
        "source_url": "https://www.asus.com/motherboards/foo",
    })
    assert vf.value == "Z790"

    # пустая строка
    with pytest.raises(ValidationError):
        validate_field("motherboard", "socket", {
            "value": "",
            "source_url": "https://www.asus.com/motherboards/foo",
        })
