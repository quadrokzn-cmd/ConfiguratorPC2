# Тесты на web_result_view.enrich_variants_with_specs.
#
# Вставляем по одному компоненту каждой категории в реальную тестовую БД
# (фикстуры из test_web/conftest.py проливают миграции и дают db_session),
# вызываем enrich и проверяем собранную строку specs_short.
#
# Транзакция откатывается в конце теста, чтобы записи компонентов не
# утекали в следующие тесты.

from __future__ import annotations

import pytest
from sqlalchemy import text as _t

from app.services.web_result_view import enrich_variants_with_specs


# --------------------------- helpers --------------------------------------

def _variant_with(components_by_cat: dict[str, dict]) -> list[dict]:
    """Строит минимальный список вариантов в формате _prepare_variants."""
    return [{
        "manufacturer": "Intel",
        "total_usd":    100,
        "total_rub":    9000,
        "components":   components_by_cat,
        "warnings":     [],
        "used_transit": False,
        "path_used":    "default",
    }]


def _comp(cat: str, cid: int, model: str = "Test") -> dict:
    """Минимальный dict компонента, как его формирует _prepare_variants."""
    return {
        "category":      cat,
        "component_id":  cid,
        "model":         model,
        "sku":           None,
        "manufacturer":  "X",
        "quantity":      1,
        "supplier":      "S",
        "supplier_sku":  None,
        "price_usd":     0,
        "price_rub":     0,
        "stock":         1,
        "in_transit":    False,
        "also_available_at": [],
    }


# --------------------------- тесты ----------------------------------------

@pytest.fixture()
def rollback_session(db_session):
    """db_session + автоматический rollback, чтобы не мусорить тестовыми
    записями в таблицах компонентов между прогонами."""
    try:
        yield db_session
    finally:
        db_session.rollback()


def test_cpu_full_fields(rollback_session):
    cid = rollback_session.execute(_t(
        "INSERT INTO cpus (model, manufacturer, socket, cores, threads, "
        "  base_clock_ghz, turbo_clock_ghz, tdp_watts, has_integrated_graphics, "
        "  memory_type, package_type) "
        "VALUES ('Core i5-12400', 'Intel', 'LGA1700', 6, 12, 2.5, 4.4, "
        "        65, TRUE, 'DDR5', 'BOX') RETURNING id"
    )).first().id
    variants = _variant_with({"cpu": _comp("cpu", cid, "Core i5-12400")})

    enrich_variants_with_specs(variants, rollback_session)

    specs = variants[0]["components"]["cpu"]["specs_short"]
    assert specs == "6C/12T · 2.5/4.4GHz · LGA1700"


def test_cpu_partial_fields(rollback_session):
    # Только cores и base_clock — нет threads и turbo, нет socket.
    cid = rollback_session.execute(_t(
        "INSERT INTO cpus (model, manufacturer, cores, base_clock_ghz) "
        "VALUES ('Ryzen Partial', 'AMD', 8, 3.0) RETURNING id"
    )).first().id
    variants = _variant_with({"cpu": _comp("cpu", cid)})

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["cpu"]["specs_short"] == "8C · 3GHz"


def test_motherboard(rollback_session):
    cid = rollback_session.execute(_t(
        "INSERT INTO motherboards (model, manufacturer, socket, chipset, "
        "  form_factor, memory_type, has_m2_slot) "
        "VALUES ('B550M', 'ASUS', 'AM4', 'B550', 'mATX', 'DDR4', TRUE) "
        "RETURNING id"
    )).first().id
    variants = _variant_with({"motherboard": _comp("motherboard", cid)})

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["motherboard"]["specs_short"] \
        == "AM4 · mATX · DDR4"


def test_ram(rollback_session):
    cid = rollback_session.execute(_t(
        "INSERT INTO rams (model, manufacturer, memory_type, form_factor, "
        "  module_size_gb, modules_count, frequency_mhz) "
        "VALUES ('Kingston Fury', 'Kingston', 'DDR4', 'DIMM', "
        "        16, 2, 3200) RETURNING id"
    )).first().id
    variants = _variant_with({"ram": _comp("ram", cid)})

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["ram"]["specs_short"] \
        == "16GB × 2 · DDR4-3200"


def test_gpu_with_tdp(rollback_session):
    cid = rollback_session.execute(_t(
        "INSERT INTO gpus (model, manufacturer, vram_gb, vram_type, "
        "  tdp_watts, needs_extra_power, video_outputs, "
        "  core_clock_mhz, memory_clock_mhz) "
        "VALUES ('RTX 4060', 'NVIDIA', 8, 'GDDR6', 115, TRUE, "
        "        'HDMI x1', 2460, 17000) RETURNING id"
    )).first().id
    variants = _variant_with({"gpu": _comp("gpu", cid)})

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["gpu"]["specs_short"] \
        == "8GB GDDR6 · 115W"


def test_gpu_without_tdp(rollback_session):
    cid = rollback_session.execute(_t(
        "INSERT INTO gpus (model, manufacturer, vram_gb, vram_type) "
        "VALUES ('RTX no-tdp', 'NVIDIA', 12, 'GDDR6X') RETURNING id"
    )).first().id
    variants = _variant_with({"gpu": _comp("gpu", cid)})

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["gpu"]["specs_short"] == "12GB GDDR6X"


def test_storage_large_capacity_as_tb(rollback_session):
    cid = rollback_session.execute(_t(
        "INSERT INTO storages (model, manufacturer, storage_type, "
        "  form_factor, interface, capacity_gb) "
        "VALUES ('Samsung 2TB', 'Samsung', 'SSD', 'M.2', 'NVMe', 2000) "
        "RETURNING id"
    )).first().id
    variants = _variant_with({"storage": _comp("storage", cid)})

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["storage"]["specs_short"] \
        == "2TB · SSD · NVMe"


def test_storage_small_capacity_as_gb(rollback_session):
    cid = rollback_session.execute(_t(
        "INSERT INTO storages (model, manufacturer, storage_type, "
        "  form_factor, interface, capacity_gb) "
        "VALUES ('Kingston 512', 'Kingston', 'SSD', 'M.2', 'NVMe', 512) "
        "RETURNING id"
    )).first().id
    variants = _variant_with({"storage": _comp("storage", cid)})

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["storage"]["specs_short"] \
        == "512GB · SSD · NVMe"


def test_psu(rollback_session):
    cid = rollback_session.execute(_t(
        "INSERT INTO psus (model, manufacturer, power_watts) "
        "VALUES ('Corsair 650W', 'Corsair', 650) RETURNING id"
    )).first().id
    variants = _variant_with({"psu": _comp("psu", cid)})

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["psu"]["specs_short"] == "650W"


def test_case(rollback_session):
    cid = rollback_session.execute(_t(
        "INSERT INTO cases (model, manufacturer, supported_form_factors, "
        "  has_psu_included) "
        "VALUES ('Corsair 4000D', 'Corsair', "
        "        ARRAY['ATX','mATX','ITX'], FALSE) RETURNING id"
    )).first().id
    variants = _variant_with({"case": _comp("case", cid)})

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["case"]["specs_short"] \
        == "ATX/mATX/ITX"


def test_cooler(rollback_session):
    cid = rollback_session.execute(_t(
        "INSERT INTO coolers (model, manufacturer, supported_sockets, "
        "  max_tdp_watts) "
        "VALUES ('DeepCool AK400', 'DeepCool', ARRAY['AM5','LGA1700'], 220) "
        "RETURNING id"
    )).first().id
    variants = _variant_with({"cooler": _comp("cooler", cid)})

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["cooler"]["specs_short"] \
        == "TDP до 220W"


def test_all_fields_null_returns_none(rollback_session):
    """Если у компонента заполнены только обязательные model/manufacturer
    (а все структурированные поля NULL) — specs_short должен быть None,
    шаблон такую строку не покажет."""
    cid = rollback_session.execute(_t(
        "INSERT INTO cpus (model, manufacturer) "
        "VALUES ('Unknown CPU', 'Intel') RETURNING id"
    )).first().id
    variants = _variant_with({"cpu": _comp("cpu", cid)})

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["cpu"]["specs_short"] is None


def test_component_missing_in_db_gets_none(rollback_session):
    """Если component_id не найден в БД (удалён), specs_short = None,
    страница при этом не падает."""
    variants = _variant_with({"cpu": _comp("cpu", 999999)})

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["cpu"]["specs_short"] is None


def test_empty_variants_noop(rollback_session):
    """Пустой список вариантов просто возвращается без обращений к БД."""
    assert enrich_variants_with_specs([], rollback_session) == []


def test_multiple_variants_and_categories(rollback_session):
    """Смешанный кейс: два варианта (Intel и AMD), в каждом по CPU и RAM.
    Убеждаемся, что один SELECT на категорию корректно раскладывает
    specs по обоим вариантам."""
    intel_cpu_id = rollback_session.execute(_t(
        "INSERT INTO cpus (model, manufacturer, cores, threads, "
        "  base_clock_ghz, turbo_clock_ghz, socket) "
        "VALUES ('i5', 'Intel', 6, 12, 2.5, 4.4, 'LGA1700') RETURNING id"
    )).first().id
    amd_cpu_id = rollback_session.execute(_t(
        "INSERT INTO cpus (model, manufacturer, cores, threads, "
        "  base_clock_ghz, turbo_clock_ghz, socket) "
        "VALUES ('r5', 'AMD', 6, 12, 3.8, 4.5, 'AM5') RETURNING id"
    )).first().id
    ram_id = rollback_session.execute(_t(
        "INSERT INTO rams (model, manufacturer, memory_type, form_factor, "
        "  module_size_gb, modules_count, frequency_mhz) "
        "VALUES ('Common', 'Kingston', 'DDR5', 'DIMM', 16, 2, 5600) "
        "RETURNING id"
    )).first().id

    variants = [
        {
            "manufacturer": "Intel",
            "total_usd": 1, "total_rub": 1, "warnings": [],
            "used_transit": False, "path_used": "default",
            "components": {
                "cpu": _comp("cpu", intel_cpu_id),
                "ram": _comp("ram", ram_id),
            },
        },
        {
            "manufacturer": "AMD",
            "total_usd": 1, "total_rub": 1, "warnings": [],
            "used_transit": False, "path_used": "default",
            "components": {
                "cpu": _comp("cpu", amd_cpu_id),
                "ram": _comp("ram", ram_id),
            },
        },
    ]

    enrich_variants_with_specs(variants, rollback_session)

    assert variants[0]["components"]["cpu"]["specs_short"] \
        == "6C/12T · 2.5/4.4GHz · LGA1700"
    assert variants[1]["components"]["cpu"]["specs_short"] \
        == "6C/12T · 3.8/4.5GHz · AM5"
    assert variants[0]["components"]["ram"]["specs_short"] \
        == variants[1]["components"]["ram"]["specs_short"] \
        == "16GB × 2 · DDR5-5600"
