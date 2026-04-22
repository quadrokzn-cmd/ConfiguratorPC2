# Smoke-тест CLI scripts/build_config.py.
#
# Проверяем, что:
#   - --print-example возвращает корректный JSON;
#   - --example отрабатывает на реальной БД (если она доступна).

import json
import subprocess
import sys
from pathlib import Path

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_CLI = _PROJECT_ROOT / "scripts" / "build_config.py"


def _run(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = None
    if env_extra is not None:
        import os
        env = {**os.environ, **env_extra}
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(_CLI), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(_PROJECT_ROOT),
    )


def test_print_example_returns_valid_json():
    res = _run("--print-example")
    assert res.returncode == 0, res.stderr
    parsed = json.loads(res.stdout)
    # Содержит ожидаемые ключи
    assert "budget_usd" in parsed
    assert "cpu" in parsed
    assert "min_cores" in parsed["cpu"]


@pytest.mark.skipif(
    not (_PROJECT_ROOT / ".env").exists(),
    reason="нужен .env с DATABASE_URL для smoke-теста с реальной БД",
)
def test_example_request_runs_against_real_db():
    """Проверяем, что CLI на реальной БД либо подбирает, либо корректно отказывает."""
    res = _run("--example")
    # 0 (ok/partial) или 1 (failed) — оба допустимы; падение трактуем как ошибку
    assert res.returncode in (0, 1), f"stderr: {res.stderr}"
    assert "Статус:" in res.stdout
    assert "Курс USD/RUB" in res.stdout


@pytest.mark.skipif(
    not (_PROJECT_ROOT / ".env").exists(),
    reason="нужен .env с DATABASE_URL",
)
def test_example_request_json_output():
    res = _run("--example", "--json")
    assert res.returncode in (0, 1)
    parsed = json.loads(res.stdout)
    assert parsed["status"] in ("ok", "partial", "failed")
    assert "usd_rub_rate" in parsed
    assert "fx_source" in parsed
