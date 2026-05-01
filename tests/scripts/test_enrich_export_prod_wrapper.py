# Тесты wrapper'а scripts/enrich_export_prod.py (этап 11.6.2.3.3).
#
# Wrapper запускает enrich_export.py --stdout удалённо через railway ssh,
# забирает stdout (JSON), раскладывает batch-файлы локально в pending/.
# Тесты мокают subprocess.run, чтобы не лезть на прод.

from __future__ import annotations

import json
import sys

import pytest

from scripts import enrich_export_prod as wrapper


def _fake_completed_process(*, stdout: bytes, stderr: bytes = b"", returncode: int = 0):
    """Минимальный заместитель subprocess.CompletedProcess."""
    class _CP:
        def __init__(self):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode
    return _CP()


@pytest.fixture
def patched_environment(tmp_path, monkeypatch):
    """Перенаправляет ENRICHMENT_ROOT во временную папку и делает
    railway-CLI 'видимым' для shutil.which."""
    monkeypatch.setattr(wrapper, "ENRICHMENT_ROOT", tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    return tmp_path


def _run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["enrich_export_prod.py", *argv])
    return wrapper.main()


def test_wrapper_creates_pending_files_with_correct_names(
    monkeypatch, capsys, patched_environment,
):
    """Хороший путь: subprocess отдал валидный JSON → файлы создаются
    в pending/<category>/ с правильными именами и payload-структурой."""
    expected_doc = {
        "category": "cooler",
        "exported_at": "2026-05-01T12:00:00+00:00",
        "target_fields": ["max_tdp_watts"],
        "case_psu_pass": False,
        "batches": [
            {
                "filename": "batch_001_cooler_20260501T120000Z.json",
                "batch_id": "batch_001",
                "generated_at": "2026-05-01T12:00:00+00:00",
                "items": [
                    {"id": 1, "to_fill": ["max_tdp_watts"]},
                    {"id": 2, "to_fill": ["max_tdp_watts"]},
                ],
            },
            {
                "filename": "batch_002_cooler_20260501T120001Z.json",
                "batch_id": "batch_002",
                "generated_at": "2026-05-01T12:00:01+00:00",
                "items": [{"id": 3, "to_fill": ["max_tdp_watts"]}],
            },
        ],
    }
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _fake_completed_process(
            stdout=json.dumps(expected_doc).encode("utf-8"),
            stderr=b"some progress log\n",
        ),
    )

    rc = _run(monkeypatch, ["--category", "cooler", "--batch-size", "30"])
    captured = capsys.readouterr()
    assert rc == 0

    pending_dir = patched_environment / "pending" / "cooler"
    assert pending_dir.exists()

    file1 = pending_dir / "batch_001_cooler_20260501T120000Z.json"
    file2 = pending_dir / "batch_002_cooler_20260501T120001Z.json"
    assert file1.exists()
    assert file2.exists()

    payload1 = json.loads(file1.read_text(encoding="utf-8"))
    assert payload1["category"] == "cooler"
    assert payload1["batch_id"] == "batch_001"
    assert payload1["target_fields"] == ["max_tdp_watts"]
    assert payload1["case_psu_pass"] is False
    assert len(payload1["items"]) == 2

    payload2 = json.loads(file2.read_text(encoding="utf-8"))
    assert payload2["batch_id"] == "batch_002"
    assert len(payload2["items"]) == 1

    # Прогресс из stderr удалённого процесса должен попасть в наш stderr.
    assert "some progress log" in captured.err
    # Финальная сводка
    assert "Exported 2 batches from PROD" in captured.err
    assert "(3 items total)" in captured.err


def test_wrapper_handles_non_zero_exit(monkeypatch, capsys, patched_environment):
    """SSH вернул non-zero → wrapper возвращает 1 и пробрасывает stderr."""
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _fake_completed_process(
            stdout=b"", stderr=b"connection refused\n", returncode=2,
        ),
    )

    rc = _run(monkeypatch, ["--category", "cooler"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "connection refused" in captured.err
    assert "railway ssh завершился с кодом 2" in captured.err
    pending_dir = patched_environment / "pending" / "cooler"
    assert not pending_dir.exists() or not list(pending_dir.glob("*.json"))


def test_wrapper_handles_broken_json(monkeypatch, capsys, patched_environment):
    """Удалённый процесс отдал не-JSON → exit 1 с фрагментом данных."""
    bad = b"Welcome to Railway\nnot a json at all\n"
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _fake_completed_process(stdout=bad, returncode=0),
    )

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, ["--category", "cooler"])
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "не является валидным JSON" in captured.err
    assert "not a json at all" in captured.err


def test_wrapper_warns_on_non_empty_pending_without_force(
    monkeypatch, capsys, patched_environment,
):
    """Если pending/<category>/ уже не пуст → предупреждение и exit 1
    без --force; subprocess в этом случае не должен запускаться."""
    pending = patched_environment / "pending" / "cooler"
    pending.mkdir(parents=True)
    (pending / "batch_999_cooler_old.json").write_text("{}", encoding="utf-8")

    called = {"yes": False}
    def _should_not_run(*a, **kw):
        called["yes"] = True
        return _fake_completed_process(stdout=b"{}")
    monkeypatch.setattr("subprocess.run", _should_not_run)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, ["--category", "cooler"])
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "уже содержит" in captured.err
    assert "--force" in captured.err
    assert called["yes"] is False


def test_wrapper_with_force_overrides_warning(
    monkeypatch, capsys, patched_environment,
):
    """С --force продолжаем работу даже при непустом pending/."""
    pending = patched_environment / "pending" / "cooler"
    pending.mkdir(parents=True)
    (pending / "batch_999_cooler_old.json").write_text("{}", encoding="utf-8")

    doc = {
        "category": "cooler",
        "exported_at": "2026-05-01T12:00:00+00:00",
        "target_fields": ["max_tdp_watts"],
        "case_psu_pass": False,
        "batches": [
            {
                "filename": "batch_010_cooler_new.json",
                "batch_id": "batch_010",
                "generated_at": "2026-05-01T12:00:00+00:00",
                "items": [{"id": 1, "to_fill": ["max_tdp_watts"]}],
            },
        ],
    }
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: _fake_completed_process(
            stdout=json.dumps(doc).encode("utf-8"),
        ),
    )

    rc = _run(monkeypatch, ["--category", "cooler", "--force"])
    capsys.readouterr()
    assert rc == 0
    assert (pending / "batch_010_cooler_new.json").exists()
    # Старый файл не тронут.
    assert (pending / "batch_999_cooler_old.json").exists()


def test_wrapper_passes_limit_through(
    monkeypatch, capsys, patched_environment,
):
    """--limit пробрасывается в удалённую команду enrich_export."""
    captured_cmd = {}
    def _capture_run(cmd, *a, **kw):
        captured_cmd["cmd"] = cmd
        doc = {
            "category": "cooler", "exported_at": "x",
            "target_fields": [], "case_psu_pass": False, "batches": [],
        }
        return _fake_completed_process(stdout=json.dumps(doc).encode("utf-8"))
    monkeypatch.setattr("subprocess.run", _capture_run)

    rc = _run(monkeypatch, [
        "--category", "cooler", "--batch-size", "5", "--limit", "5",
    ])
    capsys.readouterr()
    assert rc == 0
    cmd = captured_cmd["cmd"]
    # Команда должна содержать --limit 5 и --stdout
    assert "--limit" in cmd
    assert "5" in cmd
    assert "--stdout" in cmd
    assert "--category" in cmd
    assert "cooler" in cmd


def test_wrapper_errors_when_railway_missing(monkeypatch, capsys, tmp_path):
    """Если railway CLI не найден в PATH — понятное сообщение, exit 1."""
    monkeypatch.setattr(wrapper, "ENRICHMENT_ROOT", tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)

    with pytest.raises(SystemExit) as excinfo:
        _run(monkeypatch, ["--category", "cooler"])
    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "railway CLI не найден" in captured.err
