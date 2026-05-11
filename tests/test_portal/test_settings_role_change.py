# Тесты смены роли пользователя в /settings/users портала
# (бывший /admin/users; перенос на этапе UI-3 Пути B, 2026-05-11).
#
# Брифа: только admin может менять роли; нельзя оставить систему без
# админов (последний admin защищён); самопонижение требует подтверждения;
# admin↔admin и manager↔manager — no-op без записи в БД; невалидная
# роль → 422; несуществующий target → 404; manager и аноним получают
# 403 / 302 на /login соответственно.

from __future__ import annotations

from sqlalchemy import text as _t

from tests.test_portal.conftest import (
    _create_user,
    _login_via_portal,
    extract_csrf,
)


# --- helpers ------------------------------------------------------------

def _csrf(client) -> str:
    """Открывает /settings/users и достаёт CSRF из формы."""
    r = client.get("/settings/users")
    assert r.status_code == 200, r.status_code
    return extract_csrf(r.text)


def _role_in_db(db_session, user_id: int) -> str | None:
    row = db_session.execute(
        _t("SELECT role FROM users WHERE id = :id"), {"id": user_id},
    ).first()
    return row.role if row else None


# --- 1. promote manager → admin ----------------------------------------

def test_admin_can_promote_manager_to_admin(
    admin_portal_client, manager_user, db_session,
):
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{manager_user['id']}/role",
        data={"csrf_token": token, "role": "admin"},
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/settings/users"
    assert _role_in_db(db_session, manager_user["id"]) == "admin"


# --- 2. demote admin → manager (когда есть второй admin) ---------------

def test_admin_can_demote_admin_to_manager(
    admin_portal_client, admin_user, db_session,
):
    """Создаём второго админа и понижаем его — это легитимно, т.к.
    остаётся первый admin (admin_user, под которым залогинены)."""
    other_admin_id = _create_user(
        db_session, login="admin2", password="admin-pass2",
        role="admin", name="Второй админ",
    )
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{other_admin_id}/role",
        data={"csrf_token": token, "role": "manager"},
    )
    assert r.status_code == 302
    assert _role_in_db(db_session, other_admin_id) == "manager"


# --- 3. manager не может менять роль (403) -----------------------------

def test_manager_cannot_change_role(
    manager_portal_client, admin_user, db_session,
):
    """Менеджер залогинен, шлёт POST смены роли админу. Это require_admin
    → 403. CSRF не нужен (роль не должна меняться вообще)."""
    r = manager_portal_client.post(
        f"/settings/users/{admin_user['id']}/role",
        data={"role": "manager"},
    )
    assert r.status_code == 403
    # И в БД ничего не поменялось.
    assert _role_in_db(db_session, admin_user["id"]) == "admin"


# --- 4. аноним не может (302 на /login) --------------------------------

def test_anonymous_cannot_change_role(portal_client, db_session, admin_user):
    """Незалогиненный пользователь получает редирект на /login."""
    r = portal_client.post(
        f"/settings/users/{admin_user['id']}/role",
        data={"role": "manager"},
    )
    assert r.status_code == 302
    assert "/login" in r.headers["location"]
    # Роль не изменилась.
    assert _role_in_db(db_session, admin_user["id"]) == "admin"


# --- 5. нельзя понизить последнего админа (400) ------------------------

def test_cannot_demote_last_admin(
    admin_portal_client, admin_user, db_session,
):
    """В тестовой БД ровно один admin (admin_user). Попытка понизить
    его — 400 с человеческим сообщением. В БД роль не меняется."""
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{admin_user['id']}/role",
        data={
            "csrf_token": token,
            "role": "manager",
            # Даже с confirm — последний админ всё равно защищён.
            "confirm_self_demotion": "true",
        },
    )
    assert r.status_code == 400
    assert "последнего администратора" in r.text
    assert _role_in_db(db_session, admin_user["id"]) == "admin"


# --- 6. самопонижение требует confirm_self_demotion --------------------

def test_self_demotion_requires_confirm_flag(
    admin_portal_client, admin_user, db_session,
):
    """Создаём второго админа, чтобы пройти проверку «последний админ»,
    и пробуем самопонизить admin_user (под которым залогинены).

    Без флага confirm_self_demotion → 400; с флагом → 302."""
    _create_user(
        db_session, login="admin2", password="admin-pass2",
        role="admin", name="Второй админ",
    )

    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{admin_user['id']}/role",
        data={"csrf_token": token, "role": "manager"},
    )
    assert r.status_code == 400
    assert "подтверждения" in r.text
    assert _role_in_db(db_session, admin_user["id"]) == "admin"

    # С флагом — успех. Берём свежий CSRF на всякий случай.
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{admin_user['id']}/role",
        data={
            "csrf_token": token,
            "role": "manager",
            "confirm_self_demotion": "true",
        },
    )
    assert r.status_code == 302
    assert _role_in_db(db_session, admin_user["id"]) == "manager"


# --- 7. невалидная роль → 422 ------------------------------------------

def test_invalid_role_value_returns_422(
    admin_portal_client, manager_user, db_session,
):
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{manager_user['id']}/role",
        data={"csrf_token": token, "role": "superadmin"},
    )
    assert r.status_code == 422
    assert _role_in_db(db_session, manager_user["id"]) == "manager"


# --- 8. target не найден → 404 -----------------------------------------

def test_target_user_not_found_returns_404(admin_portal_client):
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        "/settings/users/999999/role",
        data={"csrf_token": token, "role": "admin"},
    )
    assert r.status_code == 404


# --- 9. no-op (manager → manager) → 302 без записи в БД ----------------

def test_no_op_role_change_returns_success(
    admin_portal_client, manager_user, db_session,
):
    """Та же роль, что уже у пользователя — сервер возвращает 302
    (редирект на /settings/users), флага «обновлено» не ставит, но и не
    падает. Состояние БД не меняется."""
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{manager_user['id']}/role",
        data={"csrf_token": token, "role": "manager"},
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/settings/users"
    assert _role_in_db(db_session, manager_user["id"]) == "manager"


# --- 10. Дополнительно: создание пользователя с role=admin -------------

def test_admin_can_create_admin_via_form(
    admin_portal_client, db_session,
):
    """Форма создания пользователя теперь имеет селект роли. Проверяем,
    что POST с role=admin создаёт админа с пустыми permissions."""
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        "/settings/users",
        data={
            "login":      "newadmin",
            "name":       "Новый админ",
            "password":   "secure-pass",
            "role":       "admin",
            "csrf_token": token,
        },
    )
    assert r.status_code == 302
    row = db_session.execute(
        _t(
            "SELECT role, permissions FROM users WHERE login = 'newadmin'"
        )
    ).first()
    assert row is not None
    assert row.role == "admin"
    # У админа дефолтные permissions пустые — admin видит всё и без них.
    import json
    perms = row.permissions if isinstance(row.permissions, dict) else json.loads(row.permissions)
    assert perms == {}


# --- 11. UI: на /settings/users отрисовывается селект роли ----------------

def test_role_select_renders_in_users_page(
    admin_portal_client, manager_user,
):
    """В строке менеджера на /settings/users должна быть форма смены роли
    с селектом и POST на /settings/users/{id}/role."""
    r = admin_portal_client.get("/settings/users")
    assert r.status_code == 200
    assert f'/settings/users/{manager_user["id"]}/role' in r.text
    assert 'name="role"' in r.text
    # И селект создания нового пользователя — оба варианта.
    assert "Менеджер" in r.text
    assert "Администратор" in r.text
