"""Тесты scripts/hide_fan_splitters_in_cooler.py.

Покрывают:
  - dry-run режим: is_hidden не меняется;
  - fan-разветвители (Разветвитель ID-Cooling FS-04 ARGB, fan hub, PWM hub,
    fan controller) попадают в кандидаты и хайдятся при --apply;
  - реальные CPU-кулеры (с supported_sockets / max_tdp_watts или с явными
    CPU-маркерами в имени) не попадают;
  - после --apply кулер пропадает из выдачи get_cheapest_cooler (проверка
    что engine.candidates._hidden_filter работает в связке).
"""

from __future__ import annotations

from sqlalchemy import text

from scripts.hide_fan_splitters_in_cooler import (
    find_candidates,
    hide_candidates,
)


def _seed_cooler(
    db, *, model, manufacturer="Test", sku=None,
    sockets=None, max_tdp=None, hidden=False,
) -> int:
    """Сидит запись в coolers. sockets=None → NULL."""
    if sockets is None:
        sock_sql = "NULL"
        params = {
            "m": model, "mfg": manufacturer, "tdp": max_tdp, "h": hidden,
            "sku": sku or model,
        }
    else:
        sock_sql = ":sockets"
        params = {
            "m": model, "mfg": manufacturer, "tdp": max_tdp, "h": hidden,
            "sockets": sockets, "sku": sku or model,
        }
    sql = (
        "INSERT INTO coolers (model, manufacturer, sku, supported_sockets, "
        "                     max_tdp_watts, is_hidden) "
        f"VALUES (:m, :mfg, :sku, {sock_sql}, :tdp, :h) RETURNING id"
    )
    row = db.execute(text(sql), params).first()
    db.commit()
    return int(row.id)


def test_dry_run_does_not_change_db(db_engine, db_session):
    """В dry-run is_hidden не меняется."""
    cid = _seed_cooler(
        db_session,
        model="Разветвитель питания ID-Cooling FS-04 ARGB",
        sku="FS-04-ARGB",
        sockets=None, max_tdp=None,
    )
    result = hide_candidates(db_engine, apply=False)
    assert result["found"] >= 1
    assert result["hidden"] == 0

    row = db_session.execute(
        text("SELECT is_hidden FROM coolers WHERE id = :id"),
        {"id": cid},
    ).first()
    assert row.is_hidden is False


def test_finds_id_cooling_fs04_argb(db_engine, db_session):
    """Реальный кейс из БД (FS-04 ARGB) попадает в кандидаты."""
    cid = _seed_cooler(
        db_session,
        model="Разветвитель питания ID-Cooling FS-04 ARGB",
        sku="FS-04-ARGB-2",
        sockets=None, max_tdp=None,
    )
    candidates = find_candidates(db_engine)
    ids = {c.id for c in candidates}
    assert cid in ids


def test_finds_pwm_hub_and_fan_controller(db_engine, db_session):
    """PWM hub / fan controller / фан-хаб ловятся."""
    cid_hub = _seed_cooler(
        db_session,
        model="ARCTIC Case Fan Hub to 10-x PWM Fan",
        sku="ACFAN00175A-2",
        sockets=None, max_tdp=None,
    )
    cid_ctrl = _seed_cooler(
        db_session,
        model="Corsair Commander Pro Fan Controller",
        sku="CC-9010210-2",
        sockets=None, max_tdp=None,
    )
    candidates = find_candidates(db_engine)
    ids = {c.id for c in candidates}
    assert cid_hub in ids
    assert cid_ctrl in ids


def test_skips_real_cpu_cooler_with_sockets(db_engine, db_session):
    """CPU-кулер с заполненным supported_sockets не помечается, даже если
    в имени мелькнёт «разветвитель» (защитный слой по данным)."""
    cid_real = _seed_cooler(
        db_session,
        model="DeepCool AK620 с разветвителем PWM в комплекте",
        sku="AK620-WITH-SPLITTER",
        sockets=["LGA1700", "AM5"],
        max_tdp=260,
    )
    candidates = find_candidates(db_engine)
    ids = {c.id for c in candidates}
    assert cid_real not in ids


def test_skips_cpu_cooler_by_name_marker(db_engine, db_session):
    """CPU-кулер без заполненных полей, но с «башня»/«радиатор» в имени —
    защитный слой _CPU_COOLER_HINTS блокирует пометку."""
    cid = _seed_cooler(
        db_session,
        model="Башенный кулер с PWM splitter",
        sku="TOWER-WITH-SPLITTER",
        sockets=None, max_tdp=None,
    )
    candidates = find_candidates(db_engine)
    ids = {c.id for c in candidates}
    assert cid not in ids


def test_apply_hides_and_engine_skips(db_engine, db_session):
    """После --apply кулер получает is_hidden=TRUE и пропадает из
    `get_cheapest_cooler` (через _hidden_filter в engine/candidates.py)."""
    cid = _seed_cooler(
        db_session,
        model="Разветвитель питания ID-Cooling FS-06 ARGB",
        sku="FS-06-ARGB-TEST",
        sockets=None, max_tdp=None,
    )
    result = hide_candidates(db_engine, apply=True)
    assert result["found"] >= 1
    assert result["hidden"] >= 1

    db_session.commit()
    row = db_session.execute(
        text("SELECT is_hidden FROM coolers WHERE id = :id"),
        {"id": cid},
    ).first()
    assert row.is_hidden is True

    # Косвенная проверка engine: SQL-выборка ровно так же фильтрует, как
    # `engine/candidates.py::get_cheapest_cooler` (is_hidden=FALSE).
    found_in_visible = db_session.execute(
        text(
            "SELECT 1 FROM coolers WHERE id = :id AND is_hidden = FALSE"
        ),
        {"id": cid},
    ).first()
    assert found_in_visible is None


def test_idempotent_on_second_apply(db_engine, db_session):
    """Повторный запуск --apply не находит уже скрытых кандидатов."""
    _seed_cooler(
        db_session,
        model="Разветвитель ID-Cooling FS-04 idempotent",
        sku="FS-04-IDEMPOTENT",
        sockets=None, max_tdp=None,
    )
    first = hide_candidates(db_engine, apply=True)
    assert first["found"] >= 1
    db_session.commit()

    second = hide_candidates(db_engine, apply=True)
    # Второй прогон уже не видит этих кандидатов (они is_hidden=TRUE).
    # При этом найденные other tests-кандидаты могут «затеряться» в общем
    # пуле; проверяем что наш конкретный idempotent SKU точно не найден.
    second_ids = {c.id for c in find_candidates(db_engine)}
    cid_idemp = db_session.execute(
        text("SELECT id FROM coolers WHERE sku = 'FS-04-IDEMPOTENT'")
    ).scalar()
    assert cid_idemp not in second_ids
