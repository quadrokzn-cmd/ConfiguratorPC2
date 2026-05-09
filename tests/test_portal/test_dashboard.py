# Тесты дашборда портала (этап 9Б.2).
#
# Покрытие:
#   1) GET / для admin → 200, в HTML есть data-testid маркеры всех 5
#      виджетов и плитки конфигуратора.
#   2) GET / для manager с permissions["configurator"]=true → то же.
#   3) GET / для manager без permissions → виджеты есть, плитка
#      configurator НЕ отрисована (вместо неё «доступных модулей нет»).
#   4) get_dashboard_data возвращает dict с 5 ключами и непустыми
#      сабполями для каждого.
#   5) get_dashboard_data на пустой БД не падает и отдаёт нули/None.
#
# Состояние БД готовится через прямой text-INSERT (см. helpers).
# Это медленнее, чем фабрики, но дешевле в поддержке: тесты «знают»
# схему через SQL, не через ORM-модели.

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text as _t


# ---------------------------------------------------------------------
# Очистка таблиц, не покрытых _clean_tables в test_portal/conftest.py:
# suppliers, price_uploads и таблицы компонентов. Без этой autouse-фикстуры
# данные между тестами дашборда мигрируют (test_admin_users.py их не
# наполняет и не страдает от этого).
# ---------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_dashboard_tables(db_engine):
    with db_engine.begin() as conn:
        conn.execute(_t(
            "TRUNCATE TABLE "
            "  unmapped_supplier_items, supplier_prices, price_uploads, suppliers, "
            "  cpus, motherboards, rams, gpus, storages, cases, psus, coolers "
            "RESTART IDENTITY CASCADE"
        ))
    yield


# ---------------------------------------------------------------------
# helpers — наполнение БД для интеграционных тестов
# ---------------------------------------------------------------------

def _insert_supplier(db, name: str) -> int:
    row = db.execute(
        _t("INSERT INTO suppliers (name, is_active) VALUES (:n, TRUE) "
           "ON CONFLICT (name) DO UPDATE SET is_active=TRUE "
           "RETURNING id"),
        {"n": name},
    ).first()
    db.commit()
    return int(row.id)


def _insert_price_upload(db, supplier_id: int, *, days_ago: int, status: str = "success") -> None:
    db.execute(
        _t(
            "INSERT INTO price_uploads "
            "(supplier_id, filename, uploaded_at, rows_total, rows_matched, "
            " rows_unmatched, status, notes) "
            "VALUES (:sid, 'x.xlsx', NOW() - (:d || ' days')::interval, "
            "        100, 100, 0, :st, NULL)"
        ),
        {"sid": supplier_id, "d": days_ago, "st": status},
    )
    db.commit()


def _insert_cpu(db, model: str, *, hidden: bool = False) -> None:
    db.execute(
        _t(
            "INSERT INTO cpus (model, manufacturer, socket, cores, threads, "
            "                  base_clock_ghz, turbo_clock_ghz, tdp_watts, "
            "                  has_integrated_graphics, memory_type, package_type, is_hidden) "
            "VALUES (:m, 'AMD', 'AM5', 8, 16, 4.5, 5.0, 105, FALSE, 'DDR5', 'BOX', :h)"
        ),
        {"m": model, "h": hidden},
    )
    db.commit()


def _insert_gpu(db, model: str, *, hidden: bool = False) -> None:
    db.execute(
        _t(
            "INSERT INTO gpus (model, manufacturer, vram_gb, vram_type, "
            "                  tdp_watts, needs_extra_power, video_outputs, "
            "                  core_clock_mhz, memory_clock_mhz, is_hidden) "
            "VALUES (:m, 'NVIDIA', 12, 'GDDR6X', 200, TRUE, 'HDMI', 1500, 8000, :h)"
        ),
        {"m": model, "h": hidden},
    )
    db.commit()


def _insert_project(db, user_id: int, name: str = "Проект") -> int:
    row = db.execute(
        _t("INSERT INTO projects (user_id, name) VALUES (:uid, :n) RETURNING id"),
        {"uid": user_id, "n": name},
    ).first()
    db.commit()
    return int(row.id)


def _insert_exchange_rate(db, *, days_ago: int = 0, rate: str = "75.5300") -> None:
    db.execute(
        _t(
            "INSERT INTO exchange_rates (rate_date, rate_usd_rub, source, fetched_at) "
            "VALUES ((CURRENT_DATE - :d), :r, 'cbr', NOW()) "
            "ON CONFLICT (rate_date, source) DO UPDATE "
            "  SET rate_usd_rub = EXCLUDED.rate_usd_rub, "
            "      fetched_at  = EXCLUDED.fetched_at"
        ),
        {"d": days_ago, "r": rate},
    )
    db.commit()


# ---------------------------------------------------------------------
# get_dashboard_data — юнит-уровень
# ---------------------------------------------------------------------

def test_dashboard_data_returns_expected_keys_on_empty_db(db_session):
    """На пустой БД сервис не падает и возвращает все 6 ключей
    (5 базовых + auctions_overview из этапа 9a слияния)."""
    from portal.services.dashboard import get_dashboard_data

    data = get_dashboard_data(db_session)
    assert set(data.keys()) == {
        "active_projects",
        "managers",
        "exchange_rate",
        "suppliers_freshness",
        "components_breakdown",
        "auctions_overview",
    }
    # И каждое значение — словарь либо список с минимум одним полем.
    assert isinstance(data["active_projects"], dict) and "total" in data["active_projects"]
    assert isinstance(data["managers"], dict) and "total" in data["managers"]
    assert isinstance(data["exchange_rate"], dict) and "rate" in data["exchange_rate"]
    assert isinstance(data["suppliers_freshness"], list)
    assert isinstance(data["components_breakdown"], dict) and "total" in data["components_breakdown"]
    assert isinstance(data["auctions_overview"], dict)
    assert "total_active" in data["auctions_overview"]


def test_dashboard_data_empty_db_zero_values(db_session):
    """На пустой БД счётчики = 0, курс = None, поставщики — три строки
    с last_loaded_at=None."""
    from portal.services.dashboard import get_dashboard_data, SUPPLIERS_FOR_FRESHNESS

    data = get_dashboard_data(db_session)
    assert data["active_projects"]["total"] == 0
    assert data["managers"]["total"] == 0
    assert data["exchange_rate"]["rate"] is None
    assert data["components_breakdown"]["total"] == 0

    freshness = data["suppliers_freshness"]
    assert len(freshness) == len(SUPPLIERS_FOR_FRESHNESS)
    for row in freshness:
        assert row["name"] in SUPPLIERS_FOR_FRESHNESS
        assert row["last_loaded_at"] is None


def test_dashboard_data_with_projects_and_managers(db_session, admin_user, manager_user):
    """С 2 пользователями в БД (admin+manager) виджет «менеджеры» = 1.
    После создания проекта — active_projects.total = 1."""
    from portal.services.dashboard import get_dashboard_data

    _insert_project(db_session, manager_user["id"], "Тестовый проект")
    data = get_dashboard_data(db_session)
    assert data["managers"]["total"] == 1   # admin не считается
    assert data["active_projects"]["total"] == 1


def test_dashboard_data_components_count_excludes_hidden(db_session):
    """Скрытые компоненты (is_hidden=true) не учитываются в сумме."""
    from portal.services.dashboard import get_dashboard_data

    _insert_cpu(db_session, "Ryzen 7 7700X", hidden=False)
    _insert_cpu(db_session, "Ryzen 9 9950X", hidden=False)
    _insert_cpu(db_session, "СКРЫТЫЙ CPU",   hidden=True)
    _insert_gpu(db_session, "RTX 4070",      hidden=False)

    data = get_dashboard_data(db_session)
    assert data["components_breakdown"]["total"] == 3   # 2 CPU + 1 GPU
    cats = {c["table"]: c["count"] for c in data["components_breakdown"]["categories"]}
    assert cats["cpus"] == 2
    assert cats["gpus"] == 1


def test_dashboard_data_exchange_rate_takes_latest(db_session):
    """Курс берётся самый свежий по rate_date."""
    from portal.services.dashboard import get_dashboard_data

    _insert_exchange_rate(db_session, days_ago=5, rate="80.0000")
    _insert_exchange_rate(db_session, days_ago=0, rate="75.5300")

    data = get_dashboard_data(db_session)
    assert abs(data["exchange_rate"]["rate"] - 75.53) < 0.001
    assert data["exchange_rate"]["source"] == "cbr"


def test_dashboard_data_suppliers_freshness(db_session):
    """OCS — свежий (3 дня назад), Merlion — устарел (>14 дней),
    Treolan — нет данных."""
    from portal.services.dashboard import get_dashboard_data

    ocs_id = _insert_supplier(db_session, "OCS")
    merlion_id = _insert_supplier(db_session, "Merlion")
    _insert_supplier(db_session, "Treolan")  # без price_uploads

    _insert_price_upload(db_session, ocs_id, days_ago=3)
    _insert_price_upload(db_session, merlion_id, days_ago=20)

    data = get_dashboard_data(db_session)
    by_name = {row["name"]: row for row in data["suppliers_freshness"]}

    assert by_name["OCS"]["is_stale"] is False
    assert by_name["OCS"]["days_ago"] == 3
    assert by_name["Merlion"]["is_stale"] is True
    assert by_name["Merlion"]["days_ago"] == 20
    assert by_name["Treolan"]["last_loaded_at"] is None
    assert by_name["Treolan"]["days_ago"] is None


def test_dashboard_data_supplier_freshness_ignores_failed_uploads(db_session):
    """Загрузка со status='failed' не учитывается как «свежий прайс»."""
    from portal.services.dashboard import get_dashboard_data

    ocs_id = _insert_supplier(db_session, "OCS")
    # Только failed-загрузки → виджет показывает «нет данных».
    _insert_price_upload(db_session, ocs_id, days_ago=1, status="failed")

    data = get_dashboard_data(db_session)
    by_name = {row["name"]: row for row in data["suppliers_freshness"]}
    assert by_name["OCS"]["last_loaded_at"] is None


def test_dashboard_data_supplier_freshness_includes_partial_uploads(db_session):
    """status='partial' (часть строк сматчилась, часть нет) — это
    нормальная штатная загрузка прайса. Виджет должен её учитывать."""
    from portal.services.dashboard import get_dashboard_data

    ocs_id = _insert_supplier(db_session, "OCS")
    _insert_price_upload(db_session, ocs_id, days_ago=2, status="partial")

    data = get_dashboard_data(db_session)
    by_name = {row["name"]: row for row in data["suppliers_freshness"]}
    assert by_name["OCS"]["last_loaded_at"] is not None
    assert by_name["OCS"]["days_ago"] == 2
    assert by_name["OCS"]["is_stale"] is False


# ---------------------------------------------------------------------
# Format helpers — юниты
# ---------------------------------------------------------------------

def test_format_days_ago():
    from portal.services.dashboard import format_days_ago

    assert format_days_ago(None) == "нет данных"
    assert format_days_ago(0) == "сегодня"
    assert format_days_ago(1) == "вчера"
    assert format_days_ago(2) == "2 дня назад"
    assert format_days_ago(5) == "5 дней назад"
    assert format_days_ago(11) == "11 дней назад"
    assert format_days_ago(21) == "21 день назад"
    assert format_days_ago(22) == "22 дня назад"
    assert format_days_ago(25) == "25 дней назад"


def test_format_ru_date():
    from datetime import date
    from portal.services.dashboard import format_ru_date

    assert format_ru_date(None) == ""
    assert format_ru_date(date(2026, 4, 27)) == "27 апреля 2026"


def test_format_ru_datetime_short():
    from portal.services.dashboard import format_ru_datetime_short

    assert format_ru_datetime_short(None) == ""
    dt = datetime(2026, 4, 27, 10, 0, 0, tzinfo=timezone.utc)
    out = format_ru_datetime_short(dt)
    # МСК = UTC+3 → 13:00. Формат «27 апреля 2026, 13:00».
    assert "27 апреля 2026" in out
    assert "13:00" in out


# ---------------------------------------------------------------------
# Интеграция: GET / для разных пользователей
# ---------------------------------------------------------------------

def test_admin_dashboard_shows_all_widgets(admin_portal_client):
    r = admin_portal_client.get("/")
    assert r.status_code == 200
    body = r.text
    # Все 5 виджетов
    for tid in (
        "widget-active-projects",
        "widget-managers",
        "widget-exchange-rate",
        "widget-suppliers-freshness",
        "widget-components",
    ):
        assert f'data-testid="{tid}"' in body, f"Виджет {tid} не найден"
    # Плитка конфигуратора — admin видит её всегда
    assert 'data-testid="tile-configurator"' in body
    assert "Конфигуратор ПК" in body


def test_admin_dashboard_greeting_contains_first_name(
    admin_portal_client, admin_user, db_session
):
    """Приветствие — «Добрый день, <первое слово имени>»."""
    db_session.execute(
        _t("UPDATE users SET name = 'Иван Иванов' WHERE id = :id"),
        {"id": admin_user["id"]},
    )
    db_session.commit()
    r = admin_portal_client.get("/")
    assert r.status_code == 200
    assert "Добрый день, Иван" in r.text


def test_manager_with_configurator_sees_tile(manager_portal_client):
    r = manager_portal_client.get("/")
    assert r.status_code == 200
    body = r.text
    # Виджеты доступны менеджеру
    assert 'data-testid="widget-active-projects"' in body
    assert 'data-testid="widget-components"' in body
    # И плитка модуля — у него есть permission configurator (дефолт фикстуры)
    assert 'data-testid="tile-configurator"' in body


def test_manager_without_configurator_no_tile(portal_client, manager_user_no_perms):
    """Менеджер без permissions: виджеты есть, плитки модуля нет."""
    from tests.test_portal.conftest import extract_csrf

    # Логинимся вручную, потому что фикстуры manager_portal_client нет
    # для no-perms варианта.
    r = portal_client.get("/login")
    token = extract_csrf(r.text)
    r = portal_client.post(
        "/login",
        data={
            "login":      manager_user_no_perms["login"],
            "password":   manager_user_no_perms["password"],
            "csrf_token": token,
        },
    )
    assert r.status_code == 302

    r = portal_client.get("/")
    assert r.status_code == 200
    body = r.text
    # Виджеты — есть
    assert 'data-testid="widget-active-projects"' in body
    assert 'data-testid="widget-components"' in body
    # А плитки модуля — нет, вместо неё пустое состояние
    assert 'data-testid="tile-configurator"' not in body
    assert 'data-testid="tile-no-modules"' in body


# ---------------------------------------------------------------------
# 9Б.2.1 — единый kt-app-shell + сайдбар вместо топбара
# ---------------------------------------------------------------------

def test_portal_uses_kt_app_shell(admin_portal_client):
    """Портал собран на тех же классах каркаса, что и конфигуратор:
    kt-app-shell + kt-sidebar + kt-main."""
    r = admin_portal_client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "kt-app-shell" in body
    assert "kt-sidebar" in body
    assert "kt-main" in body


def test_portal_sidebar_contains_home_link(admin_portal_client):
    """В сайдбаре есть ссылка «Главная» на /."""
    r = admin_portal_client.get("/")
    assert r.status_code == 200
    body = r.text
    assert 'href="/"' in body
    assert "Главная" in body


def test_admin_sidebar_contains_users_link(admin_portal_client):
    """У админа в сайдбаре есть ссылка «Пользователи»."""
    r = admin_portal_client.get("/")
    assert r.status_code == 200
    assert 'href="/admin/users"' in r.text


def test_admin_users_page_marks_users_active(admin_portal_client):
    """На /admin/users пункт «Пользователи» помечен nav-item-active."""
    r = admin_portal_client.get("/admin/users")
    assert r.status_code == 200
    body = r.text
    # nav-item-active — класс активного пункта сайдбара (как в конфигураторе).
    assert "nav-item-active" in body


def test_manager_sidebar_has_no_users_link(manager_portal_client):
    """У менеджера НЕТ ссылки на /admin/users в сайдбаре."""
    r = manager_portal_client.get("/")
    assert r.status_code == 200
    assert 'href="/admin/users"' not in r.text


def test_admin_sidebar_has_link_to_configurator(admin_portal_client):
    """В подвале сайдбара портала — ссылка «← Конфигуратор» на
    CONFIGURATOR_URL (CONFIGURATOR_URL в тестах = http://localhost:8080)."""
    r = admin_portal_client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "http://localhost:8080/" in body
    assert "kt-portal-back" in body


def test_manager_sidebar_has_link_to_configurator(manager_portal_client):
    """То же для менеджера — ссылка на конфигуратор видна всем
    залогиненным."""
    r = manager_portal_client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "http://localhost:8080/" in body
    assert "kt-portal-back" in body


def test_portal_sidebar_renders_fx_widget_partial(admin_portal_client, db_session):
    """В сайдбаре портала курс ЦБ отрисовывается из общего партиала
    shared/templates/_partials/fx_widget.html — тот же класс
    .kt-fx-widget, что в конфигураторе."""
    # Кладём свежий курс, чтобы партиал отрисовался.
    _insert_exchange_rate(db_session, days_ago=0, rate="80.0000")

    r = admin_portal_client.get("/")
    assert r.status_code == 200
    body = r.text
    assert "kt-fx-widget" in body
    assert "$ = 80.00" in body
