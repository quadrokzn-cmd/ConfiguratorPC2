# Тесты portal/scheduler.py — APScheduler-задачи портала.
#
# Покрывает в первую очередь блок 12.2 (единое утреннее расписание
# auto_price_loads — по одной задаче на каждого из 6 поставщиков):
#
#   test_six_jobs_registered_for_each_supplier
#   test_each_job_calls_run_auto_load_for_its_slug
#   test_disabled_supplier_skipped_in_scheduled_run
#
# А также страховочные тесты, что именованные задачи и их id'шники
# совпадают с тем, что ожидает прод-Railway-логиkа («grep -i scheduler»
# в логах).

from __future__ import annotations

import pytest
from sqlalchemy import text


# =====================================================================
# Полное расписание, ожидаемое 12.2.
# =====================================================================

_EXPECTED_SCHEDULE = [
    ("treolan",      7,  0),
    ("ocs",          7, 10),
    ("merlion",      7, 20),
    ("netlab",       7, 30),
    ("resurs_media", 7, 40),
    ("green_place",  7, 50),
]


# =====================================================================
# 1. Шесть задач зарегистрированы — по одной на каждый slug.
# =====================================================================

def test_six_jobs_registered_for_each_supplier(monkeypatch):
    """init_scheduler должен зарегистрировать ровно 6 cron-задач
    auto_price_loads_<slug> с правильным временем (07:00..07:50).
    Тест поднимает scheduler через RUN_BACKUP_SCHEDULER=1 и сразу же
    останавливает, не дожидаясь реальных тиков."""
    import portal.scheduler as sch

    monkeypatch.setenv("RUN_BACKUP_SCHEDULER", "1")
    monkeypatch.delenv("APP_ENV", raising=False)

    # Чистый старт — на случай, если другой тест уже поднимал глобальный.
    sch.shutdown_scheduler()

    sched = sch.init_scheduler()
    try:
        assert sched is not None
        ids = {j.id for j in sched.get_jobs()}
        # Все 6 auto_price_loads_<slug> должны быть зарегистрированы.
        for slug, _h, _m in _EXPECTED_SCHEDULE:
            assert f"auto_price_loads_{slug}" in ids, (
                f"задача auto_price_loads_{slug} не зарегистрирована; "
                f"имеющиеся: {sorted(ids)}"
            )
        # Остальные задачи (daily_backup, audit_retention) тоже на месте.
        assert "daily_backup" in ids
        assert "audit_retention" in ids

        # Время каждой задачи — час и минута соответствуют SCHEDULE.
        for slug, hour, minute in _EXPECTED_SCHEDULE:
            job = sched.get_job(f"auto_price_loads_{slug}")
            assert job is not None
            # CronTrigger: поля fields[hour] / fields[minute].
            fields = {f.name: str(f) for f in job.trigger.fields}
            assert fields["hour"] == str(hour), (
                f"{slug}: hour={fields['hour']}, ожидается {hour}"
            )
            assert fields["minute"] == str(minute), (
                f"{slug}: minute={fields['minute']}, ожидается {minute}"
            )
    finally:
        sch.shutdown_scheduler()


def test_old_combined_jobs_not_registered_anymore(monkeypatch):
    """До 12.2 были две агрегированные задачи: auto_price_loads_daily
    и auto_price_loads_email_channel. Этот тест — стопор для регрессии:
    если кто-то снова добавит их рядом со slug-задачами, оба способа
    окажутся активны и orchestrator получит двойную нагрузку."""
    import portal.scheduler as sch

    monkeypatch.setenv("RUN_BACKUP_SCHEDULER", "1")
    monkeypatch.delenv("APP_ENV", raising=False)
    sch.shutdown_scheduler()

    sched = sch.init_scheduler()
    try:
        ids = {j.id for j in sched.get_jobs()}
        assert "auto_price_loads_daily" not in ids
        assert "auto_price_loads_email_channel" not in ids
    finally:
        sch.shutdown_scheduler()


# =====================================================================
# 2. _make_auto_price_job(slug) — вызывает run_auto_load с этим slug
# =====================================================================

def test_each_job_calls_run_auto_load_for_its_slug(monkeypatch):
    """Тело cron-задачи должно вызывать run_auto_load(slug, 'scheduled').
    Проверяем для всех 6 slug'ов сразу — один цикл, чтобы регрессия типа
    «забыли про netlab» не пролезла."""
    import portal.scheduler as sch

    # _is_supplier_enabled → True, чтобы тело job-а не отбивалось на
    # тумблере (этот код покрывается отдельным тестом ниже).
    monkeypatch.setattr(sch, "_is_supplier_enabled", lambda _slug: True)

    # Мок run_auto_load — собираем вызовы по slug.
    calls: list[tuple[str, str]] = []

    def _fake_run_auto_load(slug, triggered_by):
        calls.append((slug, triggered_by))
        return {"status": "success", "supplier_slug": slug}

    # _make_auto_price_job импортирует runner лениво — патчим в его модуле.
    import app.services.auto_price.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_auto_load", _fake_run_auto_load)

    for slug, _h, _m in _EXPECTED_SCHEDULE:
        job_fn = sch._make_auto_price_job(slug)
        job_fn()

    assert calls == [(slug, "scheduled") for slug, _h, _m in _EXPECTED_SCHEDULE]


# =====================================================================
# 3. enabled=FALSE → run_auto_load не вызывается
# =====================================================================

def test_disabled_supplier_skipped_in_scheduled_run(monkeypatch, db_engine):
    """Если auto_price_loads.enabled=FALSE — job ДОЛЖЕН тихо выйти,
    НЕ вызывая run_auto_load и НЕ создавая запись в auto_price_load_runs.
    Иначе журнал засоряется ошибками «канал не подключён» для slug'ов,
    у которых пользователь сознательно держит тумблер OFF."""
    import portal.scheduler as sch

    # seed: одна строка enabled=FALSE для тестового slug.
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE auto_price_load_runs, auto_price_loads "
            "RESTART IDENTITY CASCADE"
        ))
        conn.execute(text(
            "INSERT INTO auto_price_loads (supplier_slug, enabled) "
            "VALUES ('treolan', FALSE)"
        ))

    # run_auto_load не должен быть вызван — мок с пометкой «звали».
    called = {"n": 0}

    def _fake_run_auto_load(slug, triggered_by):
        called["n"] += 1
        return {"status": "success", "supplier_slug": slug}

    import app.services.auto_price.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_auto_load", _fake_run_auto_load)

    job_fn = sch._make_auto_price_job("treolan")
    job_fn()

    assert called["n"] == 0

    # И никакой записи в auto_price_load_runs не появилось.
    with db_engine.begin() as conn:
        cnt = conn.execute(text(
            "SELECT COUNT(*) FROM auto_price_load_runs"
        )).scalar()
    assert cnt == 0


def test_enabled_supplier_actually_runs(monkeypatch, db_engine):
    """Зеркальный кейс: enabled=TRUE — run_auto_load вызвана с правильными
    аргументами."""
    import portal.scheduler as sch

    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE auto_price_load_runs, auto_price_loads "
            "RESTART IDENTITY CASCADE"
        ))
        conn.execute(text(
            "INSERT INTO auto_price_loads (supplier_slug, enabled) "
            "VALUES ('treolan', TRUE)"
        ))

    seen: list[tuple[str, str]] = []

    def _fake_run_auto_load(slug, triggered_by):
        seen.append((slug, triggered_by))
        return {"status": "success", "supplier_slug": slug}

    import app.services.auto_price.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_auto_load", _fake_run_auto_load)

    sch._make_auto_price_job("treolan")()

    assert seen == [("treolan", "scheduled")]


# =====================================================================
# 4. Job не валит scheduler-loop, если run_auto_load бросает исключение.
# =====================================================================

def test_job_swallows_runner_exception(monkeypatch):
    """run_auto_load уже сам пишет в БД и Sentry. Если он перебрасывает
    исключение наверх (например, ValueError от незарегистрированного
    fetcher'а), job в scheduler'е должен поймать его и выйти штатно —
    иначе APScheduler пометит job как failing и может повлиять на
    смежные задачи (max_instances=1)."""
    import portal.scheduler as sch

    monkeypatch.setattr(sch, "_is_supplier_enabled", lambda _slug: True)

    def _boom(slug, triggered_by):
        raise ValueError(f"fetcher для {slug} не подключён")

    import app.services.auto_price.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_auto_load", _boom)

    # Не должно бросить.
    sch._make_auto_price_job("resurs_media")()
