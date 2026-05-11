# Тесты физического удаления пользователя в /settings/users
# (бывший /admin/users; перенос на этапе UI-3 Пути B, 2026-05-11).
#
# Бриф: новая кнопка «Удалить навсегда» — POST /settings/users/{id}/delete-permanent.
# Эндпоинт за require_admin, требует CSRF, confirm_login, чтобы target был
# disabled, не сам current_user, не последний admin и без отправленных
# писем поставщикам. После DELETE: запись в audit_log с action
# user.delete_permanent; старые audit-записи этого пользователя получают
# user_id=NULL (миграция 018, ON DELETE SET NULL), а user_login сохраняется
# как денормализованная копия.

from __future__ import annotations

import json

from sqlalchemy import text as _t

from tests.test_portal.conftest import _create_user, extract_csrf


# --- helpers ------------------------------------------------------------

def _csrf(client) -> str:
    r = client.get("/settings/users")
    assert r.status_code == 200, r.status_code
    return extract_csrf(r.text)


def _disable(db_session, user_id: int) -> None:
    db_session.execute(
        _t("UPDATE users SET is_active = FALSE WHERE id = :id"),
        {"id": user_id},
    )
    db_session.commit()


def _user_exists(db_session, user_id: int) -> bool:
    row = db_session.execute(
        _t("SELECT 1 FROM users WHERE id = :id"), {"id": user_id},
    ).first()
    return row is not None


# --- 1. права: manager → 403, anonymous → 302 на /login ----------------

def test_delete_permanent_requires_admin(
    manager_portal_client, manager_user, db_session,
):
    """Менеджер пытается удалить себя — require_admin отдаёт 403.
    CSRF не нужен: до проверки CSRF дело не должно дойти."""
    r = manager_portal_client.post(
        f"/settings/users/{manager_user['id']}/delete-permanent",
        data={"confirm_login": manager_user["login"]},
    )
    assert r.status_code == 403
    assert _user_exists(db_session, manager_user["id"])


def test_delete_permanent_anonymous_redirects_to_login(
    portal_client, manager_user, db_session,
):
    r = portal_client.post(
        f"/settings/users/{manager_user['id']}/delete-permanent",
        data={"confirm_login": manager_user["login"]},
    )
    assert r.status_code == 302
    assert "/login" in r.headers["location"]
    assert _user_exists(db_session, manager_user["id"])


# --- 2. CSRF -----------------------------------------------------------

def test_delete_permanent_requires_csrf(
    admin_portal_client, manager_user, db_session,
):
    _disable(db_session, manager_user["id"])
    r = admin_portal_client.post(
        f"/settings/users/{manager_user['id']}/delete-permanent",
        data={"confirm_login": manager_user["login"]},  # csrf_token отсутствует
    )
    assert r.status_code == 400
    assert "CSRF" in r.text
    assert _user_exists(db_session, manager_user["id"])


# --- 3. 404 ------------------------------------------------------------

def test_delete_permanent_404_for_unknown_user(admin_portal_client):
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        "/settings/users/999999/delete-permanent",
        data={"csrf_token": token, "confirm_login": "anything"},
    )
    assert r.status_code == 404


# --- 4. target активен → 400 -------------------------------------------

def test_delete_permanent_400_for_active_user(
    admin_portal_client, manager_user, db_session,
):
    """manager_user is_active=True по дефолту фикстуры."""
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{manager_user['id']}/delete-permanent",
        data={
            "csrf_token":    token,
            "confirm_login": manager_user["login"],
        },
    )
    assert r.status_code == 400
    assert "отключите" in r.text.lower() or "Отключите" in r.text
    assert _user_exists(db_session, manager_user["id"])


# --- 5. self-check -----------------------------------------------------

def test_delete_permanent_400_for_self(
    admin_portal_client, admin_user, db_session,
):
    """Дублирующая защита: даже если бы каким-то путём админу разрешили
    удалить себя, серверная проверка self-id отказывает 400. Чтобы
    self-check сработал РАНЬШЕ last-admin, в системе должно быть два
    admin'а (count_admins() > 1)."""
    _create_user(
        db_session, login="second_admin", password="x",
        role="admin", name="Второй админ",
    )
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{admin_user['id']}/delete-permanent",
        data={
            "csrf_token":    token,
            "confirm_login": admin_user["login"],
        },
    )
    assert r.status_code == 400
    assert "собственный" in r.text or "себя" in r.text.lower()
    assert _user_exists(db_session, admin_user["id"])


# --- 6. confirm_login mismatch -----------------------------------------

def test_delete_permanent_400_when_confirm_login_mismatched(
    admin_portal_client, manager_user, db_session,
):
    _disable(db_session, manager_user["id"])
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{manager_user['id']}/delete-permanent",
        data={
            "csrf_token":    token,
            "confirm_login": "wrong_login",
        },
    )
    assert r.status_code == 400
    assert "не совпало" in r.text or "Подтверждение" in r.text
    assert _user_exists(db_session, manager_user["id"])


# --- 7. last admin (даже когда current пытается удалить себя) ---------

def test_delete_permanent_400_when_last_admin(
    admin_portal_client, admin_user, db_session,
):
    """В системе ровно один admin (admin_user, под которым залогинены).
    Запрос на удаление admin_user себя самого. Здесь last-admin-проверка
    стоит РАНЬШЕ self-check, поэтому ответ — «Нельзя удалить последнего
    администратора», а не «Нельзя удалить себя». Это и есть защита от
    «оставить систему без админов»."""
    from shared import user_repo
    assert user_repo.count_admins(db_session) == 1

    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{admin_user['id']}/delete-permanent",
        data={
            "csrf_token":    token,
            "confirm_login": admin_user["login"],
        },
    )
    assert r.status_code == 400
    assert "последнего администратора" in r.text
    assert _user_exists(db_session, admin_user["id"])


def test_delete_permanent_succeeds_for_disabled_admin_when_other_admin_exists(
    admin_portal_client, db_session,
):
    """Положительная сторона last-admin-защиты: если admin'ов 2 и target
    disabled — удаление проходит, count_admins() при этом 2 → 1."""
    target_id = _create_user(
        db_session, login="ex_admin", password="x",
        role="admin", name="Бывший админ",
    )
    db_session.execute(
        _t("UPDATE users SET is_active = FALSE WHERE id = :id"),
        {"id": target_id},
    )
    db_session.commit()

    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{target_id}/delete-permanent",
        data={"csrf_token": token, "confirm_login": "ex_admin"},
    )
    assert r.status_code == 302
    assert not _user_exists(db_session, target_id)


# --- 8. happy path: disabled manager -----------------------------------

def test_delete_permanent_succeeds_for_disabled_manager(
    admin_portal_client, manager_user, db_session,
):
    _disable(db_session, manager_user["id"])
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{manager_user['id']}/delete-permanent",
        data={
            "csrf_token":    token,
            "confirm_login": manager_user["login"],
        },
    )
    assert r.status_code == 302
    assert r.headers["location"] == "/settings/users"
    assert not _user_exists(db_session, manager_user["id"])

    # audit_log: одна запись user.delete_permanent с правильными полями
    rows = db_session.execute(
        _t(
            "SELECT user_id, user_login, target_type, target_id, payload "
            "FROM audit_log WHERE action = 'user.delete_permanent'"
        )
    ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.user_login == "admin"
    assert row.target_type == "user"
    assert row.target_id == str(manager_user["id"])


# --- 9. payload audit-записи -------------------------------------------

def test_delete_permanent_writes_audit_with_correct_payload(
    admin_portal_client, manager_user, admin_user, db_session,
):
    _disable(db_session, manager_user["id"])
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{manager_user['id']}/delete-permanent",
        data={
            "csrf_token":    token,
            "confirm_login": manager_user["login"],
        },
    )
    assert r.status_code == 302

    row = db_session.execute(
        _t(
            "SELECT user_id, user_login, target_id, payload "
            "FROM audit_log WHERE action = 'user.delete_permanent'"
        )
    ).first()
    assert row is not None
    # actor — admin_user (current)
    assert row.user_id == admin_user["id"]
    assert row.user_login == "admin"
    # target — удалённый manager
    assert row.target_id == str(manager_user["id"])
    payload = row.payload if isinstance(row.payload, dict) else json.loads(row.payload)
    assert payload["deleted_login"] == manager_user["login"]
    assert payload["deleted_role"] == "manager"
    assert payload["was_active"] is False


# --- 10. user_id в audit_log переходит в NULL после DELETE -------------

def test_audit_log_user_id_becomes_null_after_user_delete(
    admin_portal_client, manager_user, db_session,
):
    """Старые audit-записи удалённого пользователя сохраняются с
    user_id=NULL (ON DELETE SET NULL миграции 018), а user_login
    остаётся как денормализованный snapshot."""
    # 1. под manager пишем какое-нибудь предсуществующее аудит-событие
    db_session.execute(
        _t(
            "INSERT INTO audit_log "
            "  (action, service, user_id, user_login) "
            "VALUES ('auth.login.success', 'portal', :uid, :login)"
        ),
        {"uid": manager_user["id"], "login": manager_user["login"]},
    )
    db_session.commit()

    # 2. отключаем + удаляем
    _disable(db_session, manager_user["id"])
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{manager_user['id']}/delete-permanent",
        data={
            "csrf_token":    token,
            "confirm_login": manager_user["login"],
        },
    )
    assert r.status_code == 302

    # 3. строка в audit_log про login — user_id NULL, user_login сохранён
    row = db_session.execute(
        _t(
            "SELECT user_id, user_login FROM audit_log "
            "WHERE action = 'auth.login.success' "
            "AND user_login = :login"
        ),
        {"login": manager_user["login"]},
    ).first()
    assert row is not None
    assert row.user_id is None
    assert row.user_login == manager_user["login"]


# --- 11. блокировка удаления при наличии sent_emails -------------------

def test_delete_permanent_400_when_user_has_sent_emails(
    admin_portal_client, manager_user, db_session,
):
    """Если у target есть отправленные письма поставщикам, физическое
    удаление сломало бы FK sent_by_user_id (NOT NULL без ON DELETE,
    миграция 011). Сервер отдаёт 400 с понятным сообщением, в БД
    пользователь и письма остаются."""
    # Создаём проект и поставщика, чтобы можно было вставить sent_emails
    db_session.execute(
        _t(
            "INSERT INTO projects (id, user_id, name) "
            "VALUES (1, :uid, 'тест')"
        ),
        {"uid": manager_user["id"]},
    )
    db_session.execute(
        _t(
            "INSERT INTO suppliers (id, name) VALUES (1, 'OCS') "
            "ON CONFLICT (id) DO NOTHING"
        )
    )
    db_session.execute(
        _t(
            "INSERT INTO sent_emails "
            "  (project_id, supplier_id, sent_by_user_id, to_email, "
            "   subject, body_html, status) "
            "VALUES (1, 1, :uid, 'a@b', 's', 'b', 'sent')"
        ),
        {"uid": manager_user["id"]},
    )
    db_session.commit()

    _disable(db_session, manager_user["id"])
    token = _csrf(admin_portal_client)
    r = admin_portal_client.post(
        f"/settings/users/{manager_user['id']}/delete-permanent",
        data={
            "csrf_token":    token,
            "confirm_login": manager_user["login"],
        },
    )
    assert r.status_code == 400
    assert "поставщикам" in r.text or "переписки" in r.text
    assert _user_exists(db_session, manager_user["id"])
