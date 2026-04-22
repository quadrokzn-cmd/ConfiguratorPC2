# Тесты модуля ручного редактирования (этап 2.5В).
#
# Проверяем:
#   - сериализацию/десериализацию ячеек CSV (bool, массивы, Decimal, CLEAR_TOKEN);
#   - валидаторы (успех и отказ в граничных случаях);
#   - round-trip: экспорт одной категории → импорт (с mock-сессией)
#     не должен падать и должен не менять значения (idempotency).
#
# Тесты НЕ требуют БД — работают чисто с логикой.

import csv
import io
from decimal import Decimal
from pathlib import Path

import pytest

from app.services.manual_edit.csv_io import parse_cell, serialize_cell
from app.services.manual_edit.schema import (
    CSV_DELIMITER,
    all_fields,
    csv_header,
    is_array_field,
)
from app.services.manual_edit.validators_extra import (
    ValidationError,
    validate_field,
)


# -----------------------------------------------------------------------------
# csv_io: сериализация/десериализация
# -----------------------------------------------------------------------------

class TestCsvIo:
    def test_serialize_none(self):
        assert serialize_cell(None, is_array=False) == ""
        assert serialize_cell(None, is_array=True)  == ""

    def test_serialize_bool(self):
        assert serialize_cell(True,  is_array=False) == "true"
        assert serialize_cell(False, is_array=False) == "false"

    def test_serialize_int(self):
        assert serialize_cell(220, is_array=False) == "220"

    def test_serialize_decimal(self):
        assert serialize_cell(Decimal("4.50"), is_array=False) == "4.5"
        assert serialize_cell(Decimal("3"),    is_array=False) == "3"

    def test_serialize_str(self):
        assert serialize_cell("ATX", is_array=False) == "ATX"

    def test_serialize_array(self):
        assert serialize_cell(["ATX", "MATX", "ITX"], is_array=True) == "ATX|MATX|ITX"
        assert serialize_cell([],                   is_array=True) == ""

    def test_parse_empty(self):
        assert parse_cell("",    is_array=False) == (None, False)
        assert parse_cell("   ", is_array=False) == (None, False)

    def test_parse_clear(self):
        assert parse_cell("__CLEAR__", is_array=False) == (None, True)

    def test_parse_scalar(self):
        assert parse_cell("220", is_array=False) == ("220", False)
        assert parse_cell("  GDDR6  ", is_array=False) == ("GDDR6", False)

    def test_parse_array(self):
        val, is_clear = parse_cell("ATX|MATX|ITX", is_array=True)
        assert is_clear is False
        assert val == ["ATX", "MATX", "ITX"]

    def test_parse_array_with_blanks(self):
        val, _ = parse_cell(" ATX || ITX ", is_array=True)
        assert val == ["ATX", "ITX"]


# -----------------------------------------------------------------------------
# validators_extra: отдельные поля
# -----------------------------------------------------------------------------

class TestValidators:
    def test_cooler_max_tdp_ok(self):
        assert validate_field("cooler", "max_tdp_watts", 150) == 150
        assert validate_field("cooler", "max_tdp_watts", "200") == 200

    def test_cooler_max_tdp_out_of_range(self):
        with pytest.raises(ValidationError):
            validate_field("cooler", "max_tdp_watts", 9999)

    def test_gpu_vram_type_normalize(self):
        assert validate_field("gpu", "vram_type", "gddr6x") == "GDDR6X"

    def test_gpu_vram_type_bad(self):
        with pytest.raises(ValidationError):
            validate_field("gpu", "vram_type", "DDR0")

    def test_case_supported_form_factors_dedup_and_normalize(self):
        got = validate_field(
            "case", "supported_form_factors",
            ["micro-atx", "ATX", "ATX", "mini-itx"],
        )
        assert got == ["MATX", "ATX", "ITX"]

    def test_case_supported_form_factors_empty_fails(self):
        with pytest.raises(ValidationError):
            validate_field("case", "supported_form_factors", [])

    def test_cpu_bool(self):
        assert validate_field("cpu", "has_integrated_graphics", "да") is True
        assert validate_field("cpu", "has_integrated_graphics", "no") is False

    def test_cpu_decimal(self):
        got = validate_field("cpu", "base_clock_ghz", "3,5")
        assert got == Decimal("3.5")

    def test_unknown_field(self):
        with pytest.raises(ValidationError):
            validate_field("cpu", "not_a_real_field", "x")


# -----------------------------------------------------------------------------
# schema: консистентность заголовка CSV и полей
# -----------------------------------------------------------------------------

class TestSchema:
    def test_csv_header_starts_with_system_cols(self):
        for cat in ["cpu", "gpu", "case", "cooler"]:
            h = csv_header(cat)
            assert h[:5] == ["id", "category", "model", "manufacturer", "sku"]
            assert h[5:] == all_fields(cat)

    def test_array_fields_marking(self):
        assert is_array_field("case",   "supported_form_factors")
        assert is_array_field("cooler", "supported_sockets")
        assert not is_array_field("gpu", "tdp_watts")


# -----------------------------------------------------------------------------
# Round-trip на искусственной строке (без БД)
# -----------------------------------------------------------------------------

class TestRoundTrip:
    def test_gpu_row_serialize_parse_validate(self):
        fields = all_fields("gpu")
        header = csv_header("gpu")

        # один fake-row из БД (dict)
        db_row = {
            "id":              42,
            "category":        "gpu",
            "model":           "RTX 5080 16GB",
            "manufacturer":    "ASUS",
            "sku":             "90YV0K60-M0NA00",
            "vram_gb":         16,
            "vram_type":       "GDDR7",
            "tdp_watts":       360,
            "needs_extra_power": True,
            "video_outputs":   "HDMI 2.1 x1, DisplayPort 2.1 x3",
            "core_clock_mhz":  2295,
            "memory_clock_mhz": 30000,
            "gpu_chip":        "GB203",
            "recommended_psu_watts": 850,
            "length_mm":       330,
            "height_mm":       140,
            "power_connectors": "12VHPWR",
            "fans_count":      3,
        }
        # сериализуем всю строку
        row_out = [str(db_row["id"]), "gpu", db_row["model"], db_row["manufacturer"], db_row["sku"]]
        for f in fields:
            row_out.append(
                serialize_cell(db_row.get(f), is_array=is_array_field("gpu", f))
            )

        # пишем-читаем через csv
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=CSV_DELIMITER)
        w.writerow(header)
        w.writerow(row_out)
        buf.seek(0)

        r = csv.reader(buf, delimiter=CSV_DELIMITER)
        header_back = next(r)
        row_back = next(r)
        assert header_back == header

        parsed: dict = {}
        idx = {name: i for i, name in enumerate(header_back)}
        for f in fields:
            value, is_clear = parse_cell(
                row_back[idx[f]], is_array=is_array_field("gpu", f)
            )
            assert is_clear is False
            if value is None:
                continue
            parsed[f] = validate_field("gpu", f, value)

        # сверка: после нормализации значения должны остаться эквивалентными
        assert parsed["vram_gb"] == 16
        assert parsed["vram_type"] == "GDDR7"
        assert parsed["tdp_watts"] == 360
        assert parsed["needs_extra_power"] is True
        assert parsed["core_clock_mhz"] == 2295
        assert parsed["power_connectors"] == "12VHPWR"
        assert parsed["fans_count"] == 3
