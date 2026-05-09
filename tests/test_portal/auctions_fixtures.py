"""Helper-фикстуры и фабрики для тестов модуля «Аукционы» в портале (этап 9a).

Здесь — общие хелперы для создания пользователей с тонкими правами
(auctions / auctions_edit_status / auctions_edit_settings) и сидинга
тестовых лотов / позиций / матчей. Чтобы не плодить дубли в test_*.py.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text


# --- Фабрики пользователей ---------------------------------------------

def make_user(
    db_session,
    *,
    login: str,
    password: str = "test-pass",
    role: str = "manager",
    name: str | None = None,
    permissions: dict | None = None,
) -> dict:
    """Тонкая обёртка над _create_user из conftest, возвращает dict
    с id/login/password — удобно сразу логиниться через _login_via_portal."""
    from tests.test_portal.conftest import _create_user
    uid = _create_user(
        db_session,
        login=login,
        password=password,
        role=role,
        name=name or login,
        permissions=permissions if permissions is not None else (
            {} if role == "admin" else {"configurator": True}
        ),
    )
    return {"id": uid, "login": login, "password": password}


# --- Фикстуры для часто используемых ролей -----------------------------

@pytest.fixture()
def auctions_viewer(db_session):
    """Менеджер с базовым правом 'auctions' (только просмотр)."""
    return make_user(
        db_session, login="auc_viewer",
        permissions={"auctions": True},
    )


@pytest.fixture()
def auctions_editor(db_session):
    """Менеджер с правами view + edit_status (без edit_settings)."""
    return make_user(
        db_session, login="auc_editor",
        permissions={"auctions": True, "auctions_edit_status": True},
    )


@pytest.fixture()
def auctions_settings_editor(db_session):
    """Менеджер с правами view + edit_status + edit_settings."""
    return make_user(
        db_session, login="auc_setter",
        permissions={
            "auctions": True,
            "auctions_edit_status": True,
            "auctions_edit_settings": True,
        },
    )


@pytest.fixture()
def auctions_no_perm(db_session):
    """Менеджер без auctions-прав (и без других модулей)."""
    return make_user(
        db_session, login="no_auc", permissions={},
    )


def login_as(client, user_dict) -> None:
    """Логинит TestClient через /login. Помогает в тестах не дублировать
    boilerplate с CSRF-токеном."""
    from tests.test_portal.conftest import _login_via_portal
    _login_via_portal(client, user_dict["login"], user_dict["password"])


# --- Фабрики для seed-данных аукционов ---------------------------------

def insert_tender(
    db_session,
    *,
    reg_number: str,
    customer: str = "Тестовый заказчик",
    customer_region: str = "Татарстан",
    nmck_total: float | None = 100000.00,
    submit_deadline_offset_hours: int | None = 48,
    status: str = "new",
    flags: dict | None = None,
) -> str:
    """Создаёт минимальный тендер + tender_status. Возвращает reg_number.

    submit_deadline_offset_hours:
      - положительное → дедлайн в будущем (например, 48 = через 2 дня).
      - отрицательное → дедлайн в прошлом (overdue).
      - None → submit_deadline=NULL.
    """
    deadline = None
    if submit_deadline_offset_hours is not None:
        deadline = datetime.now(tz=timezone.utc) + timedelta(hours=submit_deadline_offset_hours)

    db_session.execute(
        text(
            "INSERT INTO tenders (reg_number, customer, customer_region, "
            "                     nmck_total, submit_deadline, "
            "                     ktru_codes_array, url, flags_jsonb) "
            "VALUES (:rn, :c, :reg, :nmck, :sd, ARRAY[]::TEXT[], "
            "        'https://zakupki.gov.ru/test', CAST(:flags AS JSONB))"
        ),
        {
            "rn":   reg_number,
            "c":    customer,
            "reg":  customer_region,
            "nmck": nmck_total,
            "sd":   deadline,
            "flags": json.dumps(flags or {}, ensure_ascii=False),
        },
    )
    db_session.execute(
        text(
            "INSERT INTO tender_status (tender_id, status) VALUES (:rn, :s)"
        ),
        {"rn": reg_number, "s": status},
    )
    db_session.commit()
    return reg_number


def insert_tender_item(
    db_session,
    *,
    tender_id: str,
    position_num: int = 1,
    ktru_code: str | None = "26.20.18.000-00000001",
    name: str = "МФУ A4 ч/б",
    qty: int = 1,
    nmck_per_unit: float | None = 50000.00,
    required_attrs: dict | None = None,
) -> int:
    """Создаёт позицию лота. Возвращает id."""
    row = db_session.execute(
        text(
            "INSERT INTO tender_items (tender_id, position_num, ktru_code, "
            "                          name, qty, unit, nmck_per_unit, "
            "                          required_attrs_jsonb) "
            "VALUES (:tid, :pn, :ktru, :name, :qty, 'шт', :nmck, "
            "        CAST(:attrs AS JSONB)) RETURNING id"
        ),
        {
            "tid":   tender_id,
            "pn":    position_num,
            "ktru":  ktru_code,
            "name":  name,
            "qty":   qty,
            "nmck":  nmck_per_unit,
            "attrs": json.dumps(required_attrs or {}, ensure_ascii=False),
        },
    ).first()
    db_session.commit()
    return int(row.id)


def insert_printer_mfu(
    db_session,
    *,
    sku: str = "TEST-PRINTER-001",
    brand: str = "TestBrand",
    name: str = "Тестовый принтер",
    category: str = "printer",
    cost_base_rub: float | None = 25000.00,
) -> int:
    """Создаёт SKU в printers_mfu. Возвращает id."""
    row = db_session.execute(
        text(
            "INSERT INTO printers_mfu (sku, brand, name, category, cost_base_rub, "
            "                          attrs_jsonb, attrs_source) "
            "VALUES (:sku, :brand, :name, :cat, :cost, '{}'::JSONB, 'manual') "
            "RETURNING id"
        ),
        {
            "sku":   sku,
            "brand": brand,
            "name":  name,
            "cat":   category,
            "cost":  cost_base_rub,
        },
    ).first()
    db_session.commit()
    return int(row.id)


def insert_match(
    db_session,
    *,
    tender_item_id: int,
    nomenclature_id: int,
    match_type: str = "primary",
    margin_pct: float | None = 25.00,
    margin_rub: float | None = 12500.00,
    price_total_rub: float | None = 50000.00,
) -> int:
    """Создаёт строку matches. Возвращает id."""
    row = db_session.execute(
        text(
            "INSERT INTO matches (tender_item_id, nomenclature_id, match_type, "
            "                     margin_pct, margin_rub, price_total_rub) "
            "VALUES (:tii, :nid, :mt, :mp, :mr, :pt) RETURNING id"
        ),
        {
            "tii": tender_item_id,
            "nid": nomenclature_id,
            "mt":  match_type,
            "mp":  margin_pct,
            "mr":  margin_rub,
            "pt":  price_total_rub,
        },
    ).first()
    db_session.commit()
    return int(row.id)
