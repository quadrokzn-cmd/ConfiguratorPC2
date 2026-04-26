# Фикстуры для тестов веб-сервиса.
#
# Стратегия:
#   - отдельная БД (TEST_DATABASE_URL из .env, по умолчанию
#     configurator_pc_test). БД должна быть создана заранее —
#     `psql -U postgres -c "CREATE DATABASE configurator_pc_test"`.
#   - один раз на сессию pytest: DROP + применяем миграции 001-007.
#     Это чистая среда.
#   - перед каждым тестом: TRUNCATE всех таблиц этапа 5 (users, projects,
#     queries, daily_budget_log) + api_usage_log. Таблицы компонентов
#     (cpus/motherboards/…) остаются пустыми и не мешают.
#   - в каждом тесте создаётся админ и один менеджер по умолчанию, два
#     TestClient-а с залогиненными сессиями.
#
# process_query мокается на уровне app.routers.main_router, чтобы
# не дёргать OpenAI и не требовать живых данных компонентов.

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Переключение DATABASE_URL на TEST_DATABASE_URL делается в корневом
# tests/conftest.py — он гарантированно выполняется до любого из
# дочерних conftest-ов и до импорта app.database.


# ---- Подготовка схемы БД (1 раз на сессию) -----------------------------

_MIGRATIONS = [
    "001_init.sql",
    "002_add_currency_and_relax_nullability.sql",
    "003_widen_model_column.sql",
    "004_add_component_field_sources.sql",
    "005_add_source_url_to_component_field_sources.sql",
    "006_add_api_usage_log.sql",
    "007_web_service.sql",
    "008_project_specification.sql",
    "009_multi_supplier_and_gtin.sql",
    "010_unmapped_score.sql",
    "011_email_support.sql",
    "012_supplier_contact_person.sql",
    "013_components_is_hidden.sql",
    "014_specification_recalculated_at.sql",
    "015_exchange_rates_table.sql",
    "016_specification_items_parsed_query.sql",
    "017_add_user_permissions.sql",
]


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _drop_all_known_tables(engine) -> None:
    """Дропает все таблицы, которые создают миграции. Без CASCADE не
    обойтись — есть FK между projects/queries/users/specification_items."""
    tables = [
        # этап 9А.2.3
        "exchange_rates",
        # этап 8.3
        "sent_emails",
        # этап 7
        "unmapped_supplier_items",
        # этап 6.2
        "specification_items",
        # этап 5
        "queries", "projects", "daily_budget_log", "users",
        # этап 4 (api_usage_log)
        "api_usage_log",
        # этап 2.5
        "component_field_sources",
        # этап 1
        "price_uploads", "supplier_prices", "suppliers",
        "cpus", "motherboards", "rams", "gpus", "storages",
        "cases", "psus", "coolers",
    ]
    with engine.begin() as conn:
        for t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))


def _apply_migrations(engine) -> None:
    root = _project_root() / "migrations"
    for name in _MIGRATIONS:
        sql = (root / name).read_text(encoding="utf-8")
        with engine.begin() as conn:
            conn.execute(text(sql))


@pytest.fixture(scope="session")
def db_engine():
    """Создаёт движок и один раз на сессию проливает все миграции."""
    from app.config import settings
    # client_encoding=utf8 — защита от UnicodeDecodeError на русской Windows
    # (аналогичный фикс в app/database.py).
    engine = create_engine(
        settings.test_database_url,
        future=True,
        connect_args={"client_encoding": "utf8"},
    )
    try:
        _drop_all_known_tables(engine)
        _apply_migrations(engine)
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(db_engine):
    """Перед каждым тестом — пустые таблицы этапов 5 и 6.2 + api_usage_log."""
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE sent_emails, specification_items, queries, "
            "projects, daily_budget_log, users, api_usage_log, exchange_rates "
            "RESTART IDENTITY CASCADE"
        ))
    yield


@pytest.fixture()
def db_session(db_engine):
    """Разовая сессия SQLAlchemy для подготовки данных тестом."""
    Session = sessionmaker(bind=db_engine, autoflush=False, autocommit=False, future=True)
    s = Session()
    try:
        yield s
    finally:
        s.close()


# ---- Пользователи и клиенты -------------------------------------------

def _create_user(session, *, login: str, password: str, role: str, name: str) -> int:
    """Создаёт пользователя. С миграцией 017 (этап 9Б.1) у каждого
    пользователя есть users.permissions JSONB; для тестов конфигуратора
    выдаём дефолт {"configurator": True}, чтобы менеджеры могли
    открывать страницы конфигуратора (логин идёт через портал, и портал
    проверит permissions при заходе на главную; на сами защищённые
    страницы конфигуратора permissions пока не проверяются — это 9Б.2,
    но ставим как у production-пользователей)."""
    import json as _json
    from shared.auth import hash_password
    perms = {} if role == "admin" else {"configurator": True}
    row = session.execute(
        text(
            "INSERT INTO users (login, password_hash, role, name, permissions) "
            "VALUES (:l, :p, :r, :n, CAST(:perms AS JSONB)) RETURNING id"
        ),
        {
            "l": login, "p": hash_password(password), "r": role, "n": name,
            "perms": _json.dumps(perms),
        },
    ).first()
    session.commit()
    return int(row.id)


@pytest.fixture()
def admin_user(db_session):
    uid = _create_user(db_session, login="admin", password="admin-pass",
                       role="admin", name="Администратор")
    return {"id": uid, "login": "admin", "password": "admin-pass"}


@pytest.fixture()
def manager_user(db_session):
    uid = _create_user(db_session, login="manager1", password="manager-pass",
                       role="manager", name="Менеджер 1")
    return {"id": uid, "login": "manager1", "password": "manager-pass"}


@pytest.fixture()
def manager2_user(db_session):
    uid = _create_user(db_session, login="manager2", password="manager-pass",
                       role="manager", name="Менеджер 2")
    return {"id": uid, "login": "manager2", "password": "manager-pass"}


import os as _os
# Эти env-переменные нужны и порталу (build_session_cookie_kwargs
# одинаковый), и для редиректа неавторизованных в конфигураторе.
# Дублируется в tests/test_portal/conftest.py — каждая папка тестов
# может прогоняться независимо.
_os.environ.setdefault("PORTAL_URL", "http://localhost:8081")
_os.environ.setdefault("CONFIGURATOR_URL", "http://localhost:8080")
_os.environ.setdefault("ALLOWED_REDIRECT_HOSTS", "localhost:8080,localhost:8081")


@pytest.fixture()
def app_client():
    """TestClient конфигуратора без залогиненного пользователя."""
    # Импортируем здесь, чтобы env-переменные уже были подняты.
    from app.main import app
    with TestClient(app, follow_redirects=False) as c:
        yield c


def _login(client: TestClient, login: str, password: str) -> None:
    """Этап 9Б.1: login переехал в портал. Логинимся отдельным
    portal-клиентом, копируем session-cookie в основной клиент.

    Cookie общая (одинаковые secret_key + cookie name), поэтому
    конфигуратор корректно её разберёт. Это и есть замысел шаринга
    сессии между сервисами."""
    from portal.main import app as portal_app
    with TestClient(portal_app, follow_redirects=False) as portal_client:
        r = portal_client.get("/login")
        assert r.status_code == 200, r.status_code
        import re
        m = re.search(r'name="csrf_token" value="([^"]+)"', r.text)
        assert m, "На странице логина не найден csrf_token"
        token = m.group(1)
        r = portal_client.post(
            "/login",
            data={"login": login, "password": password, "csrf_token": token},
        )
        assert r.status_code in (302, 303), f"Логин не прошёл: {r.status_code} {r.text[:200]}"
        # Переносим session-cookie из портала в клиент конфигуратора.
        for k, v in portal_client.cookies.items():
            client.cookies.set(k, v)


@pytest.fixture()
def admin_client(app_client, admin_user):
    _login(app_client, admin_user["login"], admin_user["password"])
    return app_client


@pytest.fixture()
def manager_client(app_client, manager_user):
    _login(app_client, manager_user["login"], manager_user["password"])
    return app_client


# ---- Утилиты для тестов -----------------------------------------------

def extract_csrf(html: str) -> str:
    import re
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "csrf_token не найден на странице"
    return m.group(1)


def parse_query_submit_redirect(location: str) -> tuple[int, int]:
    """POST /query теперь редиректит на /project/{pid}?highlight={qid}.
    Возвращает (project_id, query_id)."""
    from urllib.parse import urlsplit, parse_qs
    parts = urlsplit(location)
    pid = int(parts.path.rsplit("/", 1)[1])
    qid = int(parse_qs(parts.query).get("highlight", ["0"])[0])
    return pid, qid


def qid_from_submit_redirect(location: str) -> int:
    """Только query_id — для тестов, которым не нужен project_id."""
    return parse_query_submit_redirect(location)[1]


@pytest.fixture()
def mock_process_query(monkeypatch):
    """Мокает process_query из main_router.

    По умолчанию возвращает «успешный» FinalResponse с одним Intel-вариантом.
    Тест может подменить возвращаемое значение через mock.return_value.
    """
    from app.routers import main_router
    from app.services.configurator.schema import (
        BuildRequest, BuildResult, ComponentChoice, CPURequirements,
        GPURequirements, RAMRequirements, StorageRequirements,
        SupplierOffer, Variant,
    )
    from app.services.nlu.schema import FinalResponse, ParsedRequest

    fake_variant = Variant(
        manufacturer="Intel",
        components=[
            ComponentChoice(
                category="cpu",
                component_id=1,
                model="Intel Core i5-12400F",
                sku="BX8071512400F",
                manufacturer="Intel",
                chosen=SupplierOffer(
                    supplier="Поставщик А", price_usd=180, price_rub=16200, stock=10,
                ),
            ),
            ComponentChoice(
                category="ram",
                component_id=2,
                model="Kingston 16GB DDR4",
                sku=None,
                manufacturer="Kingston",
                chosen=SupplierOffer(
                    supplier="Поставщик Б", price_usd=40, price_rub=3600, stock=5,
                ),
            ),
        ],
        total_usd=220,
        total_rub=19800,
    )
    fake_result = BuildResult(
        status="ok",
        variants=[fake_variant],
        refusal_reason=None,
        usd_rub_rate=90.0,
        fx_source="fallback",
    )
    fake_req = BuildRequest()
    fake_parsed = ParsedRequest(is_empty=False, purpose="office", budget_usd=300)

    default_resp = FinalResponse(
        kind="ok",
        interpretation="Офисный ПК до $300.",
        formatted_text="[Фиктивный форматированный ответ]",
        build_request=fake_req,
        build_result=fake_result,
        parsed=fake_parsed,
        resolved=[],
        warnings=[],
        cost_usd=0.0,
    )

    mock = MagicMock(return_value=default_resp)
    monkeypatch.setattr(main_router, "process_query", mock)
    # На этапе 6.2 process_query ещё раз импортируется в project_router —
    # мокнем и его, чтобы тесты новой формы /project/{id}/new_query тоже
    # работали без обращения к OpenAI.
    from app.routers import project_router
    monkeypatch.setattr(project_router, "process_query", mock)
    return mock
