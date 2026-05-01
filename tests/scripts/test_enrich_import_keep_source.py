# Тесты CLI-флага --keep-source у scripts/enrich_import.py (этап 11.6.2.3.3).
#
# По умолчанию importer перемещает batch-файл из done/ в archive/ после
# успешной обработки. Флаг --keep-source отключает это поведение —
# файл остаётся в done/, чтобы тот же набор можно было повторно
# импортировать на проде через railway ssh.
#
# Тесты мокают БД (SessionLocal + _process_item), чтобы не требовать
# реального Postgres.

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from app.services.enrichment.claude_code import exporter as exporter_mod
from app.services.enrichment.claude_code import importer as importer_mod
from scripts import enrich_import as cli


@pytest.fixture
def patched_db(monkeypatch):
    """Подменяет SessionLocal и _process_item, чтобы import_* не лезли
    в БД и не требовали валидных компонентов."""

    class _StubSession:
        def commit(self): pass
        def rollback(self): pass
        def close(self): pass
        def begin_nested(self):
            class _SP:
                def commit(self): pass
                def rollback(self): pass
            return _SP()

    monkeypatch.setattr(importer_mod, "SessionLocal", lambda: _StubSession())
    monkeypatch.setattr(
        importer_mod, "_process_item",
        lambda session, category, item, stats, *, dry_run: None,
    )


@pytest.fixture
def enrichment_tmp(tmp_path, monkeypatch):
    """Перенаправляет ENRICHMENT_ROOT во временную папку для тестов."""
    monkeypatch.setattr(exporter_mod, "ENRICHMENT_ROOT", tmp_path)
    monkeypatch.setattr(importer_mod, "ENRICHMENT_ROOT", tmp_path)
    return tmp_path


def _make_done_batch(enrichment_tmp: Path, category: str, name: str) -> Path:
    done_dir = enrichment_tmp / "done" / category
    done_dir.mkdir(parents=True, exist_ok=True)
    p = done_dir / name
    p.write_text(
        json.dumps({
            "category": category,
            "batch_id": "batch_001",
            "items": [{"id": 1, "fields": {}}],
        }),
        encoding="utf-8",
    )
    return p


def _run_cli(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["enrich_import.py", *argv])
    return cli.main()


def test_default_moves_files_to_archive(
    monkeypatch, enrichment_tmp, patched_db, capsys,
):
    """Без --keep-source: файл из done/ должен переехать в archive/."""
    f = _make_done_batch(enrichment_tmp, "gpu", "batch_001_gpu_test.json")
    assert f.exists()

    rc = _run_cli(monkeypatch, ["--category", "gpu"])
    capsys.readouterr()
    assert rc == 0

    assert not f.exists(), "Файл должен был уехать в archive/"
    archive = enrichment_tmp / "archive" / "gpu" / "batch_001_gpu_test.json"
    assert archive.exists(), f"Файл не найден в {archive}"


def test_keep_source_keeps_files_in_done(
    monkeypatch, enrichment_tmp, patched_db, capsys,
):
    """С --keep-source: файл остаётся в done/, archive/ не создаётся."""
    f = _make_done_batch(enrichment_tmp, "gpu", "batch_001_gpu_keep.json")
    assert f.exists()

    rc = _run_cli(monkeypatch, ["--category", "gpu", "--keep-source"])
    capsys.readouterr()
    assert rc == 0

    assert f.exists(), "Файл должен был остаться в done/"
    archive = enrichment_tmp / "archive" / "gpu"
    if archive.exists():
        assert not list(archive.glob("*.json")), (
            "В archive/gpu/ не должно быть файлов при --keep-source"
        )


def test_keep_source_via_file_flag(
    monkeypatch, enrichment_tmp, patched_db, capsys,
):
    """--keep-source работает и в режиме --file (точечный импорт)."""
    f = _make_done_batch(enrichment_tmp, "gpu", "batch_007_gpu_one.json")
    assert f.exists()

    rc = _run_cli(monkeypatch, ["--file", str(f), "--keep-source"])
    capsys.readouterr()
    assert rc == 0
    assert f.exists()


def test_dry_run_does_not_move_regardless_of_keep_source(
    monkeypatch, enrichment_tmp, patched_db, capsys,
):
    """--dry-run всегда оставляет файл в done/, без зависимости от --keep-source.
    Проверяет, что новый флаг не сломал семантику dry-run."""
    f = _make_done_batch(enrichment_tmp, "gpu", "batch_001_gpu_dry.json")
    rc = _run_cli(monkeypatch, ["--category", "gpu", "--dry-run"])
    capsys.readouterr()
    assert rc == 0
    assert f.exists()
    archive = enrichment_tmp / "archive" / "gpu"
    if archive.exists():
        assert not list(archive.glob("*.json"))
