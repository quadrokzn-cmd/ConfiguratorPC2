# UI-2 (Путь B, 2026-05-11): тесты страницы /databases/suppliers
# на портале. Переехала с /admin/suppliers конфигуратора (этап 9А.2).
#
# Здесь — основные сценарии: список рендерится, manager без admin-прав
# получает 403, создание/редактирование/деактивация поставщика работают,
# is_active=False прячет поставщика из подбора цен.

from __future__ import annotations

import re

import pytest
from sqlalchemy import text


# ----- Утилиты ------------------------------------------------------------


def _seed_supplier(db, *, name, email="x@example.com",
                   is_active=True, contact_person=None):
    """Создаёт поставщика. ON CONFLICT UPDATE — чтобы не падать
    на seed-записях (OCS/Merlion/Treolan)."""
    row = db.execute(
        text(
            "INSERT INTO suppliers (name, email, contact_person, is_active) "
            "VALUES (:n, :e, :cp, :a) "
            "ON CONFLICT (name) DO UPDATE SET email = EXCLUDED.email, "
            "    contact_person = EXCLUDED.contact_person, "
            "    is_active = EXCLUDED.is_active "
            "RETURNING id"
        ),
        {"n": name, "e": email, "cp": contact_person, "a": is_active},
    ).first()
    db.commit()
    return int(row.id)


def _extract_csrf(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "csrf_token не найден на странице"
    return m.group(1)


# ----- Список и доступ ----------------------------------------------------


def test_suppliers_list_admin_renders(admin_portal_client):
    r = admin_portal_client.get("/databases/suppliers")
    assert r.status_code == 200
    assert "Поставщики" in r.text


def test_suppliers_list_manager_forbidden(manager_portal_client):
    r = manager_portal_client.get("/databases/suppliers")
    assert r.status_code == 403


def test_admin_suppliers_uses_kt_table(admin_portal_client, db_session):
    _seed_supplier(db_session, name="StubSuppUI2")
    r = admin_portal_client.get("/databases/suppliers")
    assert r.status_code == 200
    # Компонент дизайн-системы .kt-table.
    assert 'class="kt-table"' in r.text


def test_suppliers_list_active_subsection_is_suppliers(admin_portal_client):
    """Sidebar подсвечивает «Базы данных» → «Поставщики» (UI-2)."""
    r = admin_portal_client.get("/databases/suppliers")
    assert r.status_code == 200
    assert 'data-active-section="databases"' in r.text
    assert 'data-subsection="suppliers"' in r.text


# ----- Создание ----------------------------------------------------------


def test_supplier_create_works(admin_portal_client, db_session):
    r = admin_portal_client.get("/databases/suppliers/new")
    assert r.status_code == 200
    token = _extract_csrf(r.text)

    r = admin_portal_client.post(
        "/databases/suppliers/new",
        data={
            "csrf_token":     token,
            "name":           "NewSupplierUI2",
            "email":          "n@example.com",
            "contact_person": "Иван",
            "contact_phone":  "+7 999",
            "is_active":      "on",
        },
    )
    assert r.status_code in (302, 303)
    # Редирект — на список.
    assert r.headers["location"].endswith("/databases/suppliers")
    # Запись действительно появилась.
    row = db_session.execute(
        text("SELECT id, email, contact_person FROM suppliers WHERE name='NewSupplierUI2'")
    ).first()
    assert row is not None
    assert row.email == "n@example.com"
    assert row.contact_person == "Иван"


# ----- Редактирование ----------------------------------------------------


def test_supplier_edit_email(admin_portal_client, db_session):
    sid = _seed_supplier(db_session, name="EditSuppUI2", email="old@x.ru")

    r = admin_portal_client.get(f"/databases/suppliers/{sid}/edit")
    assert r.status_code == 200
    token = _extract_csrf(r.text)

    r = admin_portal_client.post(
        f"/databases/suppliers/{sid}/edit",
        data={
            "csrf_token": token,
            "name":       "EditSuppUI2",
            "email":      "new@x.ru",
            "is_active":  "on",
        },
    )
    assert r.status_code in (302, 303)

    row = db_session.execute(
        text("SELECT email FROM suppliers WHERE id=:id"), {"id": sid}
    ).first()
    assert row.email == "new@x.ru"


def test_supplier_edit_missing_returns_404(admin_portal_client):
    r = admin_portal_client.get("/databases/suppliers/999999/edit")
    assert r.status_code == 404


# ----- Toggle / Delete ---------------------------------------------------


def test_supplier_toggle_active(admin_portal_client, db_session):
    sid = _seed_supplier(db_session, name="ToggleSuppUI2", is_active=True)
    # Чтобы получить CSRF — открываем список (он рендерит токены в формах).
    r = admin_portal_client.get("/databases/suppliers")
    token = _extract_csrf(r.text)

    r = admin_portal_client.post(
        f"/databases/suppliers/{sid}/toggle",
        data={"csrf_token": token},
    )
    assert r.status_code in (302, 303)
    row = db_session.execute(
        text("SELECT is_active FROM suppliers WHERE id=:id"), {"id": sid}
    ).first()
    assert row.is_active is False


def test_supplier_delete_without_links_works(admin_portal_client, db_session):
    sid = _seed_supplier(db_session, name="DeleteSuppUI2")
    r = admin_portal_client.get(f"/databases/suppliers/{sid}/edit")
    token = _extract_csrf(r.text)

    r = admin_portal_client.post(
        f"/databases/suppliers/{sid}/delete",
        data={"csrf_token": token},
    )
    assert r.status_code in (302, 303)
    row = db_session.execute(
        text("SELECT id FROM suppliers WHERE id=:id"), {"id": sid}
    ).first()
    assert row is None
