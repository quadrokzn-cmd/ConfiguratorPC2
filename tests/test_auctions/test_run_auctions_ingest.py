"""Тесты для CLI scripts/run_auctions_ingest.py (этап 9e.2).

CLI — тонкая обёртка над ``run_ingest_once(engine)``, поэтому проверяем
три ветки: успешный путь (с monkeypatch на ``run_ingest_once`` и
``create_engine``), отсутствие env-файла (exit 2), пустая DSN-переменная
(exit 2). Реальной БД и сети не задействуем.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "run_auctions_ingest.py"


@pytest.fixture
def script_module():
    """Загружает scripts/run_auctions_ingest.py как модуль (он не в пакете)."""
    spec = importlib.util.spec_from_file_location("run_auctions_ingest", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_auctions_ingest"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop("run_auctions_ingest", None)


def test_run_ingest_missing_env_file(script_module, tmp_path, capsys):
    code = script_module.run_ingest(
        env_file=str(tmp_path / "nope.env"),
        db_url_env="ANY_VAR",
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "не найден" in err


def test_run_ingest_missing_dsn(script_module, tmp_path, monkeypatch, capsys):
    # Пустой env-файл — load_dotenv не упадёт, но ANY_VAR в env не появится.
    env = tmp_path / "empty.env"
    env.write_text("", encoding="utf-8")
    monkeypatch.delenv("ANY_VAR_FOR_TEST_9E2", raising=False)
    code = script_module.run_ingest(
        env_file=str(env),
        db_url_env="ANY_VAR_FOR_TEST_9E2",
    )
    assert code == 2
    err = capsys.readouterr().err
    assert "ANY_VAR_FOR_TEST_9E2" in err
    assert "не задана" in err


def test_run_ingest_success(script_module, tmp_path, monkeypatch):
    # Готовим env-файл с фиктивным DSN — реальной БД не будет.
    env = tmp_path / ".env.fake"
    env.write_text(
        "FAKE_DSN=postgresql+psycopg2://u:p@127.0.0.1:9999/x\n",
        encoding="utf-8",
    )

    disposed = {"called": False}

    class _FakeEngine:
        def dispose(self) -> None:
            disposed["called"] = True

    fake_engine = _FakeEngine()

    def _fake_create_engine(dsn, **kwargs):
        assert dsn == "postgresql+psycopg2://u:p@127.0.0.1:9999/x"
        assert kwargs.get("pool_pre_ping") is True
        return fake_engine

    called_with = {}

    def _fake_run_ingest_once(engine):
        called_with["engine"] = engine
        return SimpleNamespace(as_dict=lambda: {"inserted": 0, "updated": 0})

    monkeypatch.setattr("sqlalchemy.create_engine", _fake_create_engine)
    monkeypatch.setattr(
        "app.services.auctions.ingest.orchestrator.run_ingest_once",
        _fake_run_ingest_once,
    )

    code = script_module.run_ingest(
        env_file=str(env),
        db_url_env="FAKE_DSN",
    )
    assert code == 0
    assert called_with["engine"] is fake_engine
    assert disposed["called"] is True


def test_run_ingest_unhandled_exception(script_module, tmp_path, monkeypatch):
    env = tmp_path / ".env.fake2"
    env.write_text(
        "FAKE_DSN2=postgresql+psycopg2://u:p@127.0.0.1:9999/x\n",
        encoding="utf-8",
    )

    class _FakeEngine:
        def __init__(self):
            self.disposed = False

        def dispose(self):
            self.disposed = True

    eng = _FakeEngine()
    monkeypatch.setattr("sqlalchemy.create_engine", lambda *a, **kw: eng)

    def _boom(_engine):
        raise RuntimeError("db is down")

    monkeypatch.setattr(
        "app.services.auctions.ingest.orchestrator.run_ingest_once",
        _boom,
    )

    code = script_module.run_ingest(env_file=str(env), db_url_env="FAKE_DSN2")
    assert code == 1
    assert eng.disposed is True
