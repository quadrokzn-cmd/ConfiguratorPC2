"""Тесты Backlog #10 — per-key merge attrs_jsonb и attrs_source в importer.

Проверяем:

1. **Pure-функции merge.py** (без БД, 8 кейсов как в DoD):
   - attrs: n/a не затирает не-n/a, не-n/a побеждает, отсутствующий ключ
     не трогается; идемпотентность.
   - source: пустой → new, single+new → union, manual+new → manual+new,
     duplicate не добавляется.

2. **DB-интеграция** importer.import_done():
   - n/a в done не затирает существующее не-n/a в БД (regex_name-данные
     сохраняются);
   - не-n/a из done побеждает n/a в БД;
   - attrs_source становится `regex_name+claude_code` (а не теряется);
   - повторный импорт того же файла → 0 SKU обновлено (idempotency).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from portal.services.auctions.catalog.enrichment import importer as importer_mod
from portal.services.auctions.catalog.enrichment.merge import (
    NA,
    merge_attrs,
    merge_source,
)


# ============================================================
# Часть 1. Pure-тесты merge_attrs / merge_source.
# ============================================================

# ---- merge_attrs ------------------------------------------------------

def test_merge_attrs_na_does_not_overwrite_existing_value():
    """Кейс #1 из DoD: n/a в done НЕ затирает не-n/a в БД."""
    existing = {"print_speed_ppm": 22, "colorness": "ч/б"}
    incoming = {"print_speed_ppm": NA, "colorness": NA}
    merged = merge_attrs(existing, incoming)
    assert merged["print_speed_ppm"] == 22
    assert merged["colorness"] == "ч/б"


def test_merge_attrs_concrete_value_overwrites_na():
    """Кейс #2 из DoD: не-n/a в done обновляет n/a в БД."""
    existing = {"print_speed_ppm": NA, "colorness": NA}
    incoming = {"print_speed_ppm": 22, "colorness": "ч/б"}
    merged = merge_attrs(existing, incoming)
    assert merged["print_speed_ppm"] == 22
    assert merged["colorness"] == "ч/б"


def test_merge_attrs_concrete_value_overwrites_existing_concrete():
    """Кейс #3 из DoD: не-n/a в done обновляет не-n/a в БД (новое
    значение из claude_code считаем авторитетнее — собственник может
    его поправить руками и зафиксировать через `manual`)."""
    existing = {"print_speed_ppm": 22}
    incoming = {"print_speed_ppm": 33}
    merged = merge_attrs(existing, incoming)
    assert merged["print_speed_ppm"] == 33


def test_merge_attrs_missing_key_in_incoming_left_untouched():
    """Кейс #4 из DoD: отсутствующий в done ключ → не трогается в БД."""
    existing = {"print_speed_ppm": 22, "colorness": "ч/б"}
    incoming = {"print_speed_ppm": 33}  # colorness отсутствует
    merged = merge_attrs(existing, incoming)
    assert merged["print_speed_ppm"] == 33
    assert merged["colorness"] == "ч/б"  # не тронут


def test_merge_attrs_na_fills_missing_key():
    """Если ключа нет в existing, n/a из incoming заполняет его как n/a
    (layout-ровность; n/a-protection касается только перезаписи
    существующих не-n/a)."""
    existing = {"print_speed_ppm": 22}
    incoming = {"colorness": NA}
    merged = merge_attrs(existing, incoming)
    assert merged["print_speed_ppm"] == 22
    assert merged["colorness"] == NA


def test_merge_attrs_idempotent_second_pass():
    """Повторный merge того же incoming → результат не меняется."""
    existing = {"print_speed_ppm": NA, "colorness": "ч/б"}
    incoming = {"print_speed_ppm": 22, "colorness": NA}
    merged1 = merge_attrs(existing, incoming)
    merged2 = merge_attrs(merged1, incoming)
    assert merged1 == merged2
    assert merged2["print_speed_ppm"] == 22
    assert merged2["colorness"] == "ч/б"


def test_merge_attrs_does_not_mutate_inputs():
    """merge_attrs должен возвращать новый dict, не модифицируя входы."""
    existing = {"print_speed_ppm": NA}
    incoming = {"print_speed_ppm": 22}
    existing_copy = dict(existing)
    incoming_copy = dict(incoming)
    merge_attrs(existing, incoming)
    assert existing == existing_copy
    assert incoming == incoming_copy


# ---- merge_source -----------------------------------------------------

def test_merge_source_empty_existing_returns_incoming():
    """Кейс #5 из DoD: пустой/None existing + new → new."""
    assert merge_source(None, "claude_code") == "claude_code"
    assert merge_source("", "claude_code") == "claude_code"


def test_merge_source_single_existing_appends_incoming():
    """Кейс #6 из DoD: existing='regex_name' + 'claude_code'
    → 'regex_name+claude_code' (порядок появления)."""
    assert merge_source("regex_name", "claude_code") == "regex_name+claude_code"


def test_merge_source_manual_is_preserved_and_appended():
    """Кейс #7 из DoD: manual защищён, но new добавляется к нему как
    дополнительный тег. Это отличается от семантики regex-скрипта
    (который вообще не дописывает к manual), потому что для importer'а
    важно зафиксировать факт «claude_code прошёл по этому SKU поверх
    manual»."""
    assert merge_source("manual", "claude_code") == "manual+claude_code"
    assert merge_source("manual+regex_name", "claude_code") == (
        "manual+regex_name+claude_code"
    )


def test_merge_source_does_not_duplicate():
    """Кейс #8 из DoD: existing уже содержит incoming → не дублируется."""
    assert merge_source("claude_code", "claude_code") == "claude_code"
    assert merge_source("claude_code+regex_name", "claude_code") == (
        "claude_code+regex_name"
    )
    assert merge_source("regex_name+claude_code", "claude_code") == (
        "regex_name+claude_code"
    )


# ============================================================
# Часть 2. DB-интеграция importer.import_done().
# ============================================================

# Полный набор валидных attrs (validate_attrs требует все 9 ключей).
def _full_attrs(overrides: dict | None = None) -> dict:
    base = {
        "print_speed_ppm":         NA,
        "colorness":               NA,
        "max_format":              NA,
        "duplex":                  NA,
        "resolution_dpi":          NA,
        "network_interface":       NA,
        "usb":                     NA,
        "starter_cartridge_pages": NA,
        "print_technology":        NA,
    }
    if overrides:
        base.update(overrides)
    return base


@pytest.fixture
def enrichment_tmp(tmp_path, monkeypatch):
    """Перенаправляет ENRICHMENT_ROOT в importer'е на временную папку
    (done/ + archive/), чтобы тест не трогал боевые JSON-ы репо."""
    monkeypatch.setattr(importer_mod, "ENRICHMENT_ROOT", tmp_path)
    (tmp_path / "done").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_printers_mfu(db_engine):
    """Чистим printers_mfu перед каждым тестом. matches.nomenclature_id
    смотрит на printers_mfu(id), но в этом файле никаких matches не
    создаём, поэтому CASCADE безопасен."""
    with db_engine.begin() as conn:
        conn.execute(
            text("TRUNCATE TABLE printers_mfu RESTART IDENTITY CASCADE")
        )
    yield


def _seed_sku(
    db_session,
    *,
    sku: str,
    brand: str = "Bulat",
    name: str = "Bulat P1024W",
    category: str = "printer",
    attrs: dict | None = None,
    attrs_source: str | None = None,
) -> int:
    """Создаёт SKU в printers_mfu. Возвращает id."""
    attrs_json = json.dumps(attrs or _full_attrs(), ensure_ascii=False)
    row = db_session.execute(
        text(
            "INSERT INTO printers_mfu "
            "  (sku, brand, name, category, attrs_jsonb, attrs_source, "
            "   attrs_updated_at) "
            "VALUES (:sku, :brand, :name, :category, CAST(:attrs AS JSONB), "
            "        :source, now()) "
            "RETURNING id"
        ),
        {
            "sku": sku,
            "brand": brand,
            "name": name,
            "category": category,
            "attrs": attrs_json,
            "source": attrs_source,
        },
    ).first()
    db_session.commit()
    return row.id


def _write_done(
    enrichment_tmp: Path, *, file_name: str, results: list[dict]
) -> Path:
    """Кладёт JSON-файл в done/. Возвращает путь."""
    path = enrichment_tmp / "done" / file_name
    path.write_text(
        json.dumps({"results": results}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _fetch_state(db_session, sku: str) -> tuple[dict, str | None]:
    row = db_session.execute(
        text(
            "SELECT attrs_jsonb, attrs_source FROM printers_mfu WHERE sku = :sku"
        ),
        {"sku": sku},
    ).first()
    return (row.attrs_jsonb, row.attrs_source)


def test_db_na_does_not_overwrite_existing_concrete(
    db_session, enrichment_tmp,
):
    """Сценарий из задачи: regex_name заполнил print_speed_ppm=22,
    done-файл пришёл с n/a по этому ключу → 22 должно остаться в БД."""
    sku = "bulat:p1024w"
    _seed_sku(
        db_session,
        sku=sku,
        attrs=_full_attrs({"print_speed_ppm": 22}),
        attrs_source="regex_name",
    )

    _write_done(
        enrichment_tmp,
        file_name="bulat_001.json",
        results=[
            {"sku": sku, "attrs": _full_attrs({"print_speed_ppm": NA})}
        ],
    )

    report = importer_mod.import_done()
    assert report["files_imported"] == 1
    assert report["files_rejected"] == 0

    db_session.expire_all()
    attrs, source = _fetch_state(db_session, sku)
    # n/a из done НЕ затёрло реальное 22 от regex_name.
    assert attrs["print_speed_ppm"] == 22
    # attrs_source: regex_name присоединил claude_code (но не потерял
    # regex_name) — даже если фактических значений из done не добавилось,
    # факт прохода importer'а по SKU фиксируется в source.
    assert source == "regex_name+claude_code"


def test_db_concrete_overwrites_na(db_session, enrichment_tmp):
    """Кейс симметричный: в БД n/a, в done — реальное значение → done
    выигрывает."""
    sku = "bulat:p1024wf"
    _seed_sku(db_session, sku=sku, attrs=_full_attrs(), attrs_source=None)

    _write_done(
        enrichment_tmp,
        file_name="bulat_002.json",
        results=[
            {
                "sku": sku,
                "attrs": _full_attrs(
                    {
                        "print_speed_ppm": 24,
                        "colorness": "ч/б",
                        "max_format": "A4",
                        "print_technology": "лазерная",
                    }
                ),
            }
        ],
    )

    report = importer_mod.import_done()
    assert report["files_imported"] == 1

    db_session.expire_all()
    attrs, source = _fetch_state(db_session, sku)
    assert attrs["print_speed_ppm"] == 24
    assert attrs["colorness"] == "ч/б"
    assert attrs["max_format"] == "A4"
    assert attrs["print_technology"] == "лазерная"
    # У SKU не было source — он становится "claude_code".
    assert source == "claude_code"


def test_db_source_merges_keeping_manual(db_session, enrichment_tmp):
    """Кейс #7 на DB-уровне: existing=manual, claude_code-импорт →
    `manual+claude_code`, manual не теряется."""
    sku = "bulat:p1024iw"
    _seed_sku(
        db_session,
        sku=sku,
        attrs=_full_attrs({"print_speed_ppm": 20}),
        attrs_source="manual",
    )

    _write_done(
        enrichment_tmp,
        file_name="bulat_003.json",
        results=[
            {
                "sku": sku,
                "attrs": _full_attrs({"colorness": "ч/б"}),
            }
        ],
    )

    report = importer_mod.import_done()
    assert report["files_imported"] == 1

    db_session.expire_all()
    attrs, source = _fetch_state(db_session, sku)
    # manual-значение не тронуто (incoming print_speed_ppm=n/a),
    # colorness обновлён, source — union с manual в начале.
    assert attrs["print_speed_ppm"] == 20
    assert attrs["colorness"] == "ч/б"
    assert source == "manual+claude_code"


def test_db_idempotent_second_import(db_session, enrichment_tmp):
    """Повторный импорт того же содержимого → 0 SKU обновлено
    (skus_unchanged=1). attrs_updated_at не дёргается."""
    sku = "bulat:p1024nw"
    _seed_sku(db_session, sku=sku, attrs=_full_attrs(), attrs_source=None)

    incoming = _full_attrs({"print_speed_ppm": 22, "colorness": "ч/б"})

    # Первый импорт — должен записать.
    _write_done(
        enrichment_tmp, file_name="bulat_004.json",
        results=[{"sku": sku, "attrs": incoming}],
    )
    report1 = importer_mod.import_done()
    assert report1["skus_updated"] == 1
    assert report1["skus_unchanged"] == 0

    # Запомним state после первого импорта.
    db_session.expire_all()
    attrs_after_first, source_after_first = _fetch_state(db_session, sku)
    updated_at_after_first = db_session.execute(
        text("SELECT attrs_updated_at FROM printers_mfu WHERE sku = :sku"),
        {"sku": sku},
    ).first().attrs_updated_at

    # Второй импорт того же содержимого. Файл первого уже уехал в archive/,
    # поэтому пересоздаём done/<file>.json с тем же содержимым.
    _write_done(
        enrichment_tmp, file_name="bulat_004_repeat.json",
        results=[{"sku": sku, "attrs": incoming}],
    )
    report2 = importer_mod.import_done()

    # Файл обработан, но SKU не менялся (идемпотентность).
    assert report2["files_imported"] == 1
    assert report2["skus_updated"] == 0
    assert report2["skus_unchanged"] == 1

    db_session.expire_all()
    attrs_after_second, source_after_second = _fetch_state(db_session, sku)
    assert attrs_after_second == attrs_after_first
    assert source_after_second == source_after_first

    # attrs_updated_at не должно было сдвинуться — мы не звали UPDATE.
    updated_at_after_second = db_session.execute(
        text("SELECT attrs_updated_at FROM printers_mfu WHERE sku = :sku"),
        {"sku": sku},
    ).first().attrs_updated_at
    assert updated_at_after_second == updated_at_after_first
