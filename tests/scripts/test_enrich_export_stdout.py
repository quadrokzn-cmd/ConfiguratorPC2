# Тесты CLI-флага --stdout у scripts/enrich_export.py (этап 11.6.2.3.3).
#
# В режиме --stdout файлы в pending/ не создаются, а на stdout уходит
# один JSON-документ со всеми batch'ами. Тесты мокают БД-уровень
# (export_category) и проверяют CLI-обёртку.

from __future__ import annotations

import importlib
import json
import sys

import pytest

from scripts import enrich_export as cli


@pytest.fixture
def fake_export_category(monkeypatch):
    """Подменяет export_category в scripts.enrich_export, чтобы тесты не
    лезли в БД. Возвращает функцию-фабрику для настройки результата."""

    def configure(*, batch_payloads, target_fields=None, case_psu_pass=False):
        def _fake(category, *, batch_size=None, case_psu_pass=False,
                  max_batches=None, limit=None, write_files=True):
            assert write_files is False, (
                "В --stdout-режиме CLI должен звать export_category(write_files=False)"
            )
            return {
                "category":      category,
                "status":        "success",
                "candidates":    len(batch_payloads),
                "skipped_known": 0,
                "filtered_not_applicable": 0,
                "exported":      sum(len(p["payload"]["items"]) for p in batch_payloads),
                "batches":       [p["filename"] for p in batch_payloads],
                "batch_payloads": batch_payloads,
                "batch_size":    batch_size or 30,
                "target_fields": target_fields or ["foo_field"],
                "case_psu_pass": case_psu_pass,
            }
        monkeypatch.setattr(cli, "export_category", _fake)

    return configure


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["enrich_export.py", *argv])
    return cli.main()


def test_stdout_emits_valid_json(monkeypatch, capsys, fake_export_category):
    """--stdout печатает на stdout валидный JSON с ожидаемой структурой."""
    fake_export_category(batch_payloads=[
        {
            "filename": "batch_001_cooler_20260501T120000Z.json",
            "payload": {
                "category": "cooler",
                "batch_id": "batch_001",
                "generated_at": "2026-05-01T12:00:00+00:00",
                "target_fields": ["max_tdp_watts"],
                "case_psu_pass": False,
                "items": [{"id": 1, "model": "X1", "to_fill": ["max_tdp_watts"]}],
            },
        },
        {
            "filename": "batch_002_cooler_20260501T120001Z.json",
            "payload": {
                "category": "cooler",
                "batch_id": "batch_002",
                "generated_at": "2026-05-01T12:00:01+00:00",
                "target_fields": ["max_tdp_watts"],
                "case_psu_pass": False,
                "items": [{"id": 2, "model": "X2", "to_fill": ["max_tdp_watts"]}],
            },
        },
    ], target_fields=["max_tdp_watts"])

    rc = _run_cli(monkeypatch, [
        "--category", "cooler", "--batch-size", "1", "--stdout",
    ])
    captured = capsys.readouterr()
    assert rc == 0

    doc = json.loads(captured.out)
    assert doc["category"] == "cooler"
    assert "exported_at" in doc
    assert doc["target_fields"] == ["max_tdp_watts"]
    assert isinstance(doc["batches"], list)
    assert len(doc["batches"]) == 2
    assert doc["batches"][0]["filename"] == "batch_001_cooler_20260501T120000Z.json"
    assert doc["batches"][0]["items"][0]["id"] == 1
    assert doc["batches"][1]["filename"] == "batch_002_cooler_20260501T120001Z.json"
    assert doc["batches"][1]["items"][0]["id"] == 2


def test_stdout_does_not_pollute_with_logs(monkeypatch, capsys, fake_export_category):
    """stdout содержит ТОЛЬКО валидный JSON; вся диагностика — в stderr."""
    fake_export_category(batch_payloads=[
        {
            "filename": "batch_001_gpu_20260501T120000Z.json",
            "payload": {
                "category": "gpu",
                "batch_id": "batch_001",
                "generated_at": "2026-05-01T12:00:00+00:00",
                "target_fields": ["tdp_watts"],
                "case_psu_pass": False,
                "items": [{"id": 7, "to_fill": ["tdp_watts"]}],
            },
        },
    ], target_fields=["tdp_watts"])

    _run_cli(monkeypatch, ["--category", "gpu", "--stdout"])
    captured = capsys.readouterr()

    # stdout — валидный JSON и ничего больше.
    json.loads(captured.out)  # не должно бросать

    # Заголовок и progress — в stderr.
    assert "Выгрузка batch-файлов для Claude Code" in captured.err
    assert "Всего экспортировано позиций" in captured.err
    # И на stdout этого быть не должно.
    assert "Выгрузка" not in captured.out
    assert "экспортировано" not in captured.out


def test_stdout_does_not_create_pending_files(
    monkeypatch, capsys, fake_export_category, tmp_path,
):
    """В --stdout-режиме CLI не должен создавать ни одного файла в pending/.
    Гарантия — через write_files=False (проверяется внутри fake_export_category).
    Дополнительно убедимся, что pending/<category>/ остаётся пустой."""
    from portal.services.configurator.enrichment.claude_code import exporter as exporter_mod

    monkeypatch.setattr(exporter_mod, "ENRICHMENT_ROOT", tmp_path)
    fake_export_category(batch_payloads=[
        {
            "filename": "batch_001_case_20260501T120000Z.json",
            "payload": {
                "category": "case",
                "batch_id": "batch_001",
                "generated_at": "2026-05-01T12:00:00+00:00",
                "target_fields": ["has_psu_included"],
                "case_psu_pass": False,
                "items": [{"id": 5, "to_fill": ["has_psu_included"]}],
            },
        },
    ], target_fields=["has_psu_included"])

    _run_cli(monkeypatch, ["--category", "case", "--stdout"])
    capsys.readouterr()

    pending_dir = tmp_path / "pending" / "case"
    assert not pending_dir.exists() or not list(pending_dir.glob("*.json"))


def test_stdout_rejects_with_all(monkeypatch, capsys, fake_export_category):
    """Контракт CLI: --stdout несовместим с --all (не сериализуется
    одной 'category' в JSON)."""
    fake_export_category(batch_payloads=[])

    with pytest.raises(SystemExit):
        _run_cli(monkeypatch, ["--all", "--stdout"])
    captured = capsys.readouterr()
    assert "--stdout требует --category" in captured.err


def test_stdout_structure_matches_spec(monkeypatch, capsys, fake_export_category):
    """Контрольная проверка спецификации формата документа."""
    fake_export_category(batch_payloads=[
        {
            "filename": "batch_001_psu_20260501T120000Z.json",
            "payload": {
                "category": "psu",
                "batch_id": "batch_001",
                "generated_at": "2026-05-01T12:00:00+00:00",
                "target_fields": ["wattage"],
                "case_psu_pass": False,
                "items": [{"id": 11, "to_fill": ["wattage"]}],
            },
        },
    ], target_fields=["wattage"])

    _run_cli(monkeypatch, ["--category", "psu", "--stdout"])
    captured = capsys.readouterr()
    doc = json.loads(captured.out)

    # Минимальный контракт: category, exported_at, batches.
    assert set(["category", "exported_at", "batches"]).issubset(doc.keys())
    for entry in doc["batches"]:
        assert "filename" in entry
        assert "items" in entry
        assert isinstance(entry["items"], list)
