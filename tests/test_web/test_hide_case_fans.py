"""Тесты скрипта scripts/hide_case_fans.py (этап 9А.2.1).

Покрывают:
  - dry-run режим: ничего не пишется в БД, только формируется отчёт;
  - корпусные вентиляторы (без socket, c характерным именем) попадают в
    кандидаты;
  - реальные CPU-кулеры (с socket и max_tdp) НЕ помечаются.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from scripts.hide_case_fans import find_case_fan_candidates, hide_case_fans


def _seed_cooler(
    db, *, model, manufacturer="Test",
    sockets=None, max_tdp=None, hidden=False,
) -> int:
    """coolers по модели/сокетам/TDP. sockets=None → NULL."""
    if sockets is None:
        sock_sql = "NULL"
        params = {"m": model, "mfg": manufacturer, "tdp": max_tdp, "h": hidden}
    else:
        sock_sql = ":sockets"
        params = {
            "m": model, "mfg": manufacturer, "tdp": max_tdp,
            "h": hidden, "sockets": sockets,
        }
    sql = (
        "INSERT INTO coolers (model, manufacturer, sku, supported_sockets, "
        "                     max_tdp_watts, is_hidden) "
        f"VALUES (:m, :mfg, :m, {sock_sql}, :tdp, :h) RETURNING id"
    )
    row = db.execute(text(sql), params).first()
    db.commit()
    return int(row.id)


def test_hide_case_fans_dry_run(db_engine, db_session, tmp_path):
    """В dry-run режиме (apply=False) is_hidden у coolers не меняется."""
    cid = _seed_cooler(
        db_session, model="PCCooler AF120 Black",
        sockets=None, max_tdp=None,
    )
    db_session.commit()

    result = hide_case_fans(db_engine, apply=False, reports_dir=tmp_path)
    assert result["found"] >= 1
    assert result["hidden"] == 0

    # is_hidden остался FALSE.
    row = db_session.execute(
        text("SELECT is_hidden FROM coolers WHERE id = :id"),
        {"id": cid},
    ).first()
    assert row.is_hidden is False

    # Отчёт создан.
    report = result["report_path"]
    assert report.exists()
    text_report = report.read_text(encoding="utf-8")
    assert "DRY-RUN" in text_report
    assert "PCCooler AF120 Black" in text_report


def test_hide_case_fans_finds_obvious(db_engine, db_session, tmp_path):
    """Очевидные корпусные вентиляторы (без socket) попадают в кандидаты."""
    cid_af = _seed_cooler(
        db_session, model="DeepCool AF120 Black", sockets=None, max_tdp=None,
    )
    cid_pwm = _seed_cooler(
        db_session, model="Корпусной вентилятор 140mm PWM",
        sockets=None, max_tdp=None,
    )
    db_session.commit()

    candidates = find_case_fan_candidates(db_engine)
    ids = {c.id for c in candidates}
    assert cid_af in ids
    assert cid_pwm in ids


def test_hide_case_fans_skips_real_coolers(db_engine, db_session, tmp_path):
    """Реальные CPU-кулеры (с socket и TDP) не помечаются."""
    cid_real = _seed_cooler(
        db_session,
        model="Noctua NH-U12S redux",
        sockets=["LGA1700", "AM5"],
        max_tdp=180,
    )
    db_session.commit()

    candidates = find_case_fan_candidates(db_engine)
    ids = {c.id for c in candidates}
    assert cid_real not in ids


def test_hide_case_fans_apply_marks_records(db_engine, db_session, tmp_path):
    """С --apply кандидаты получают is_hidden=TRUE и появляется бэкап."""
    cid = _seed_cooler(
        db_session, model="PCCooler SP120 RGB",
        sockets=None, max_tdp=None,
    )
    db_session.commit()

    result = hide_case_fans(db_engine, apply=True, reports_dir=tmp_path)
    assert result["found"] >= 1
    assert result["hidden"] >= 1
    assert result["backup_path"] is not None
    assert result["backup_path"].exists()

    row = db_session.execute(
        text("SELECT is_hidden FROM coolers WHERE id = :id"),
        {"id": cid},
    ).first()
    assert row.is_hidden is True
