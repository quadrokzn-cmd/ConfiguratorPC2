# Тесты /admin/audit и интеграции write_audit (Этап 9В.4).
#
# Покрывает:
#   - права доступа (admin / manager / anonymous);
#   - фильтры (по пользователю, action, диапазону дат);
#   - пагинация (50 записей на страницу);
#   - CSV-экспорт;
#   - сам факт просмотра пишется в лог как audit.view;
#   - интеграции в auth.login (success/failed), admin_users (create,
#     role change), admin_backups (manual run).

from __future__ import annotations

import json

from sqlalchemy import text as _t

from tests.test_portal.conftest import _create_user, extract_csrf


# --- helpers -----------------------------------------------------------

def _audit_count(db_session, **filters) -> int:
    """SELECT COUNT(*) FROM audit_log с фильтрами по action/user_login."""
    where = []
    params = {}
    for k, v in filters.items():
        where.append(f"{k} = :{k}")
        params[k] = v
    sql = "SELECT COUNT(*) AS n FROM audit_log"
    if where:
        sql += " WHERE " + " AND ".join(where)
    return int(db_session.execute(_t(sql), params).scalar() or 0)


def _seed_audit(db_session, *, action: str, **fields) -> None:
    """Вспомогалка: создать запись в audit_log напрямую."""
    fields.setdefault("service", "portal")
    fields.setdefault("payload", {})
    fields["payload"] = json.dumps(fields["payload"])
    db_session.execute(
        _t(
            "INSERT INTO audit_log "
            "  (action, service, user_id, user_login, target_type, target_id, "
            "   payload, ip) "
            "VALUES "
            "  (:action, :service, :user_id, :user_login, :target_type, :target_id, "
            "   CAST(:payload AS JSONB), CAST(:ip AS INET))"
        ),
        {
            "action":      action,
            "service":     fields["service"],
            "user_id":     fields.get("user_id"),
            "user_login":  fields.get("user_login"),
            "target_type": fields.get("target_type"),
            "target_id":   fields.get("target_id"),
            "payload":     fields["payload"],
            "ip":          fields.get("ip"),
        },
    )
    db_session.commit()


# --- 1. Права доступа --------------------------------------------------

def test_audit_page_requires_admin_anon(portal_client):
    """Аноним → 302 на /login."""
    r = portal_client.get("/admin/audit")
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


def test_audit_page_forbids_manager(manager_portal_client):
    """Менеджер → 403."""
    r = manager_portal_client.get("/admin/audit")
    assert r.status_code == 403


def test_audit_page_open_for_admin(admin_portal_client):
    r = admin_portal_client.get("/admin/audit")
    assert r.status_code == 200
    assert "Журнал действий" in r.text


# --- 2. Отображение записей --------------------------------------------

def test_audit_page_shows_recent_entries(admin_portal_client, db_session):
    _seed_audit(
        db_session, action="auth.login.success",
        user_login="manager1", service="portal",
        target_type=None, target_id=None,
    )
    _seed_audit(
        db_session, action="project.create",
        user_login="manager1", service="configurator",
        target_type="project", target_id="42",
    )
    r = admin_portal_client.get("/admin/audit")
    assert r.status_code == 200
    assert "auth.login.success" in r.text
    assert "project.create" in r.text
    assert "manager1" in r.text


# --- 3. Фильтры --------------------------------------------------------

def test_audit_page_filters_by_user(
    admin_portal_client, db_session, manager_user,
):
    _seed_audit(
        db_session, action="x", user_id=manager_user["id"],
        user_login="manager1",
    )
    _seed_audit(
        db_session, action="y", user_id=None, user_login="other",
    )
    r = admin_portal_client.get(
        f"/admin/audit?user_id={manager_user['id']}"
    )
    assert r.status_code == 200
    assert "<td class=\"text-ink-primary\">\n            manager1" in r.text or "manager1" in r.text
    assert "other" not in r.text


def test_audit_page_filters_by_action(admin_portal_client, db_session):
    _seed_audit(db_session, action="user.create", user_login="admin")
    _seed_audit(db_session, action="project.delete", user_login="admin")
    r = admin_portal_client.get("/admin/audit?action=user.create")
    assert r.status_code == 200
    assert "user.create" in r.text
    # project.delete не должен светиться (в таблице — но он попадает в
    # actions_list дропдауна; смотрим только тело таблицы).
    # Достаточно, чтобы счётчик total = 1.
    assert "Найдено записей: 1" in r.text


def test_audit_page_filters_by_service(admin_portal_client, db_session):
    _seed_audit(db_session, action="a", service="portal", user_login="x")
    _seed_audit(db_session, action="b", service="configurator", user_login="y")
    r = admin_portal_client.get("/admin/audit?service=configurator")
    assert "Найдено записей: 1" in r.text


def test_audit_page_filters_by_date_range(admin_portal_client, db_session):
    """Записи вне диапазона не попадают.

    Используем кастомный action 'date.test' чтобы исключить служебные
    записи audit.view, которые порождает сам GET /admin/audit."""
    db_session.execute(
        _t(
            "INSERT INTO audit_log (action, service, created_at) "
            "VALUES ('date.test', 'portal', '2020-01-01 12:00:00+00')"
        )
    )
    db_session.execute(
        _t(
            "INSERT INTO audit_log (action, service, created_at) "
            "VALUES ('date.test', 'portal', '2024-06-15 12:00:00+00')"
        )
    )
    db_session.commit()

    # date_from/to ограничивают окном, в которое попадает только запись
    # 2024-06-15. action=date.test добавляем чтобы не зацепить audit.view.
    r = admin_portal_client.get(
        "/admin/audit?action=date.test"
        "&date_from=2024-06-01&date_to=2024-06-30"
    )
    assert r.status_code == 200
    assert "Найдено записей: 1" in r.text


# --- 4. Пагинация ------------------------------------------------------

def test_audit_page_paginates(admin_portal_client, db_session):
    """60 записей с одинаковым префиксом action → 2 страницы. Фильтруем
    по 'act.*' чтобы не зацепить audit.view от самого открытия страницы."""
    for i in range(60):
        _seed_audit(
            db_session, action=f"act.{i}",
            user_login="seed", service="portal",
        )
    r = admin_portal_client.get("/admin/audit?action=act.*")
    assert r.status_code == 200
    assert "Найдено записей: 60" in r.text
    assert "page=2" in r.text


# --- 5. CSV-экспорт ----------------------------------------------------

def test_audit_csv_export_returns_csv(admin_portal_client, db_session):
    _seed_audit(
        db_session, action="export.kp_word",
        user_login="admin", service="configurator",
        target_type="project", target_id="7",
    )
    r = admin_portal_client.get("/admin/audit/export")
    assert r.status_code == 200
    # FastAPI/StreamingResponse media_type
    ct = r.headers["content-type"]
    assert ct.startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    # Шапка CSV + строка данных.
    body = r.text
    assert "created_at_msk" in body
    assert "export.kp_word" in body
    assert "admin" in body


# --- 6. Самонаблюдение -------------------------------------------------

def test_audit_view_writes_self_audit_record(admin_portal_client, db_session):
    """Открываем /admin/audit и проверяем, что в логе появляется
    запись audit.view с user_login админа."""
    r = admin_portal_client.get("/admin/audit?action=user.create")
    assert r.status_code == 200
    n = _audit_count(db_session, action="audit.view", user_login="admin")
    assert n >= 1


# --- 6.1. UX: пустые / невалидные значения фильтров (Этап 9В.4.1) -------

def test_audit_page_handles_empty_user_id_filter(admin_portal_client):
    """HTML-форма GET'ом отправляет user_id= и action= при пустых полях.
    Pydantic int-парсер на "" возвращает 422 — чтобы не падать, эндпоинт
    принимает str|None и приводит "" → None вручную."""
    r = admin_portal_client.get("/admin/audit?user_id=&action=")
    assert r.status_code == 200


def test_audit_page_handles_empty_date_filters(admin_portal_client):
    r = admin_portal_client.get("/admin/audit?date_from=&date_to=")
    assert r.status_code == 200


def test_audit_page_handles_empty_page(admin_portal_client):
    """Пустой page= → дефолт 1, без 422."""
    r = admin_portal_client.get("/admin/audit?page=")
    assert r.status_code == 200


def test_audit_page_handles_invalid_user_id_gracefully(admin_portal_client):
    """user_id=abc → фильтр игнорируется, страница открывается."""
    r = admin_portal_client.get("/admin/audit?user_id=abc")
    assert r.status_code == 200


def test_audit_view_payload_excludes_empty_filters(
    admin_portal_client, db_session,
):
    """audit.view-запись не должна содержать в payload.filters пустых
    ключей вроде action="" — это шум в логе и confusion при разборе."""
    r = admin_portal_client.get("/admin/audit?action=&user_id=&date_from=")
    assert r.status_code == 200
    row = db_session.execute(
        _t(
            "SELECT payload FROM audit_log "
            "WHERE action = 'audit.view' ORDER BY id DESC LIMIT 1"
        )
    ).first()
    assert row is not None
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    # Когда все фильтры пустые, payload вовсе отсутствует
    # (write_audit вызывается с payload=None) или filters пуст —
    # в обоих случаях ключей "action" / "user_id" быть не должно.
    filters = (payload or {}).get("filters", {}) if payload else {}
    assert "action" not in filters
    assert "user_id" not in filters
    assert "date_from" not in filters


# --- 7. Интеграция: login success/failed --------------------------------

def test_login_success_writes_audit(portal_client, db_session, manager_user):
    r = portal_client.get("/login")
    token = extract_csrf(r.text)
    r = portal_client.post(
        "/login",
        data={
            "login":      "manager1",
            "password":   "manager-pass",
            "csrf_token": token,
        },
    )
    assert r.status_code == 302
    n = _audit_count(
        db_session, action="auth.login.success", user_login="manager1",
    )
    assert n == 1


def test_login_failed_writes_audit_with_attempted_login(
    portal_client, db_session, manager_user,
):
    """Неверный пароль для существующего юзера → audit.login.failed
    с user_id и attempted_login в payload."""
    r = portal_client.get("/login")
    token = extract_csrf(r.text)
    r = portal_client.post(
        "/login",
        data={"login": "manager1", "password": "WRONG", "csrf_token": token},
    )
    assert r.status_code == 401
    rows = db_session.execute(
        _t(
            "SELECT user_id, payload FROM audit_log "
            "WHERE action = 'auth.login.failed' ORDER BY id DESC LIMIT 1"
        )
    ).all()
    assert len(rows) == 1
    payload = rows[0].payload
    if isinstance(payload, str):
        payload = json.loads(payload)
    assert payload.get("attempted_login") == "manager1"
    assert rows[0].user_id == manager_user["id"]


def test_login_failed_unknown_user_writes_audit_no_user_id(
    portal_client, db_session,
):
    """Неизвестный логин → user_id остаётся NULL, attempted_login сохраняется."""
    r = portal_client.get("/login")
    token = extract_csrf(r.text)
    r = portal_client.post(
        "/login",
        data={"login": "ghost", "password": "any", "csrf_token": token},
    )
    assert r.status_code == 401
    row = db_session.execute(
        _t(
            "SELECT user_id, payload FROM audit_log "
            "WHERE action = 'auth.login.failed' ORDER BY id DESC LIMIT 1"
        )
    ).first()
    assert row is not None
    assert row.user_id is None
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    assert payload.get("attempted_login") == "ghost"


# --- 8. Интеграция: создание пользователя -------------------------------

def test_user_create_writes_audit(admin_portal_client, db_session):
    r = admin_portal_client.get("/admin/users")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        "/admin/users",
        data={
            "login":      "newone",
            "name":       "Новый",
            "password":   "test-pass",
            "role":       "manager",
            "csrf_token": token,
        },
    )
    assert r.status_code == 302
    n = _audit_count(db_session, action="user.create")
    assert n >= 1


# --- 9. Интеграция: смена роли ----------------------------------------

def test_role_change_writes_audit_with_diff(
    admin_portal_client, db_session, manager_user,
):
    r = admin_portal_client.get("/admin/users")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        f"/admin/users/{manager_user['id']}/role",
        data={"csrf_token": token, "role": "admin"},
    )
    assert r.status_code == 302

    row = db_session.execute(
        _t(
            "SELECT payload FROM audit_log "
            "WHERE action = 'user.role_change' ORDER BY id DESC LIMIT 1"
        )
    ).first()
    assert row is not None
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    assert payload.get("from") == "manager"
    assert payload.get("to") == "admin"


# --- 10. Интеграция: backup ручной запуск ------------------------------

def test_backup_manual_run_writes_audit(admin_portal_client, db_session, monkeypatch):
    """Кнопка «Создать бекап сейчас» → запись backup.manual_run.

    Подменяем backup_service на лету, чтобы реальный pg_dump не запускался —
    нам нужна только проверка, что аудит-запись пишется при успешном POST."""
    from portal.routers import admin_backups
    monkeypatch.setattr(
        admin_backups, "_run_backup_safely", lambda: None,
    )

    # Открываем главную, чтобы достать csrf_token (на /admin/backups
    # тоже есть, но он зависит от того, что list_backups сработал).
    r = admin_portal_client.get("/")
    token = extract_csrf(r.text)
    r = admin_portal_client.post(
        "/admin/backups/create", data={"csrf_token": token},
    )
    assert r.status_code == 302

    n = _audit_count(
        db_session, action="backup.manual_run", user_login="admin",
    )
    assert n == 1
