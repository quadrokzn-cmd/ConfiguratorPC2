"""Тесты scripts/enrich_printers_mfu_from_names.py.

Проверяем pure-функции merge без БД:
- _merge_attrs: заполняет только n/a-ключи; не перезаписывает не-n/a;
  идемпотентность.
- _merge_source: правильно строит '<src>+regex_name'; manual не трогаем.

Идемпотентность скрипта в БД проверяется отдельно через smoke-прогон
на dev-БД (см. рефлексию `.business/история/2026-05-10-этап-9a-enrich.md`):
повторный `--apply` после первого даёт «0 SKU будут обновлены».

DB-интеграционный тест в pytest-xdist подвисает на TRUNCATE printers_mfu
при пересечении с другими test-файлами, использующими ту же таблицу.
Логика merge полностью покрыта pure-тестами здесь.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


# Загружаем скрипт как модуль (он живёт в scripts/, не в пакете).
_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent / "scripts"
    / "enrich_printers_mfu_from_names.py"
)
_spec = importlib.util.spec_from_file_location(
    "enrich_printers_mfu_from_names", _SCRIPT_PATH
)
enrich_module = importlib.util.module_from_spec(_spec)
sys.modules["enrich_printers_mfu_from_names"] = enrich_module
_spec.loader.exec_module(enrich_module)


_merge_attrs = enrich_module._merge_attrs
_merge_source = enrich_module._merge_source


# ---- _merge_attrs --------------------------------------------------------

def test_merge_attrs_overwrites_only_na():
    current = {
        "print_speed_ppm": "n/a",
        "colorness": "ч/б",  # не перезаписывается, потому что не n/a
        "max_format": "n/a",
    }
    parsed = {
        "print_speed_ppm": 22,
        "colorness": "цветной",  # конфликт — оставляем как было
        "max_format": "A4",
    }
    new_attrs, changed = _merge_attrs(current, parsed)
    assert new_attrs["print_speed_ppm"] == 22
    assert new_attrs["colorness"] == "ч/б"  # не тронуто
    assert new_attrs["max_format"] == "A4"
    assert sorted(changed) == ["max_format", "print_speed_ppm"]


def test_merge_attrs_idempotent_on_second_pass():
    current = {
        "print_speed_ppm": "n/a",
        "colorness": "ч/б",
    }
    parsed = {"print_speed_ppm": 22}
    new_attrs, changed = _merge_attrs(current, parsed)
    assert changed == ["print_speed_ppm"]
    new_attrs2, changed2 = _merge_attrs(new_attrs, parsed)
    assert changed2 == []
    assert new_attrs2["print_speed_ppm"] == 22


def test_merge_attrs_fills_missing_schema_keys_with_na():
    current = {"print_speed_ppm": 22}
    parsed = {}
    new_attrs, changed = _merge_attrs(current, parsed)
    assert changed == []
    assert new_attrs["print_speed_ppm"] == 22
    assert new_attrs["colorness"] == "n/a"
    assert new_attrs["max_format"] == "n/a"


def test_merge_attrs_does_not_overwrite_with_parsed_when_already_set():
    current = {"print_speed_ppm": 30}
    parsed = {"print_speed_ppm": 22}
    new_attrs, changed = _merge_attrs(current, parsed)
    assert changed == []
    assert new_attrs["print_speed_ppm"] == 30


def test_merge_attrs_empty_parsed_returns_no_changes():
    current = {"print_speed_ppm": "n/a"}
    new_attrs, changed = _merge_attrs(current, {})
    assert changed == []


# ---- _merge_source -------------------------------------------------------

def test_merge_source_no_change_when_no_regex_added():
    assert _merge_source("claude_code", regex_added=False) == "claude_code"
    assert _merge_source(None, regex_added=False) is None


def test_merge_source_none_becomes_regex_name():
    assert _merge_source(None, regex_added=True) == "regex_name"
    assert _merge_source("", regex_added=True) == "regex_name"


def test_merge_source_claude_code_appends_regex_name():
    assert _merge_source("claude_code", regex_added=True) == "claude_code+regex_name"


def test_merge_source_does_not_duplicate_regex_name():
    assert _merge_source("claude_code+regex_name", regex_added=True) == (
        "claude_code+regex_name"
    )


def test_merge_source_manual_is_protected():
    """Ручную правку не дополняем — собственник может править руками
    конкретные ключи, нам незачем добавлять regex поверх."""
    assert _merge_source("manual", regex_added=True) == "manual"


# ---- Smoke ---------------------------------------------------------------

def test_module_loads():
    """Smoke: модуль импортируется и экспортирует функцию run."""
    assert callable(enrich_module.run)
    assert callable(enrich_module.main)
