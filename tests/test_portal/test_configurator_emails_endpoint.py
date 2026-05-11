# Тесты email-эндпоинтов /project/{id}/emails/preview и /emails/send (этап 8.3).
#
# Используют фикстуры tests/test_web/conftest.py. exchange_rate и
# email_sender.send_email мокаются, чтобы не ходить в сеть.

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import text as _t


# --- helpers (локальные, не дергают test_export/conftest) -----------------

def _insert_supplier(db_session, *, name: str, email: str | None) -> int:
    row = db_session.execute(
        _t("SELECT id FROM suppliers WHERE name = :n"),
        {"n": name},
    ).first()
    if row is None:
        row = db_session.execute(
            _t(
                "INSERT INTO suppliers (name, is_active, email) "
                "VALUES (:n, TRUE, :e) RETURNING id"
            ),
            {"n": name, "e": email},
        ).first()
    else:
        db_session.execute(
            _t("UPDATE suppliers SET email = :e WHERE id = :id"),
            {"e": email, "id": int(row.id)},
        )
    db_session.commit()
    return int(row.id)


def _insert_cpu(db_session, *, sku: str) -> int:
    row = db_session.execute(
        _t(
            "INSERT INTO cpus (sku, manufacturer, model, socket, cores, threads, "
            "                  base_clock_ghz, turbo_clock_ghz, tdp_watts) "
            "VALUES (:sku, 'Intel Corporation', 'Intel Core i5-12400F', 'LGA1700', "
            "         6, 12, 2.5, 4.4, 65) RETURNING id"
        ),
        {"sku": sku},
    ).first()
    db_session.commit()
    return int(row.id)


def _insert_supplier_price(
    db_session, *, supplier_id: int, component_id: int,
    price: float, supplier_sku: str | None = None, currency: str = "RUB",
    category: str = "cpu",
) -> None:
    db_session.execute(
        _t(
            "INSERT INTO supplier_prices "
            "  (supplier_id, category, component_id, supplier_sku, "
            "   price, currency, stock_qty) "
            "VALUES (:sid, :cat, :cid, :sku, :p, :c, 10)"
        ),
        {
            "sid": supplier_id, "cat": category, "cid": component_id,
            "sku": supplier_sku, "p": price, "c": currency,
        },
    )
    db_session.commit()


def _make_query_with_cpu(
    db_session, *, project_id: int, user_id: int, cpu_id: int,
) -> int:
    build_result = {
        "status": "ok",
        "variants": [{
            "manufacturer": "Intel",
            "path_used":    "default",
            "used_transit": False,
            "total_usd":    100.0,
            "total_rub":    9000.0,
            "components": [{
                "category": "cpu", "component_id": cpu_id,
                "model": "Intel Core i5-12400F", "sku": "CPU-MPN",
                "manufacturer": "Intel", "quantity": 1,
                "price_usd": 100.0, "price_rub": 9000.0,
                "supplier": "x", "supplier_sku": "x",
            }],
            "warnings": [],
        }],
        "refusal_reason": None,
        "usd_rub_rate":   90.0,
        "fx_source":      "fallback",
    }
    row = db_session.execute(
        _t(
            "INSERT INTO queries "
            "  (project_id, user_id, raw_text, build_result_json, status, "
            "   cost_usd, cost_rub) "
            "VALUES (:pid, :uid, :rt, CAST(:br AS JSONB), 'ok', 0, 0) "
            "RETURNING id"
        ),
        {
            "pid": project_id, "uid": user_id, "rt": "endpoint-test",
            "br":  json.dumps(build_result, ensure_ascii=False),
        },
    ).first()
    db_session.commit()
    return int(row.id)


def _mock_rate(rate_str: str = "90"):
    return patch(
        "portal.services.configurator.export.email_composer.exchange_rate.get_usd_rate",
        return_value=(Decimal(rate_str), date(2026, 4, 24), "cache"),
    )


def _csrf_from(page_html: str) -> str:
    import re
    m = re.search(r'name="csrf-token" content="([^"]+)"', page_html)
    if not m:
        # Попробуем форму логина.
        m = re.search(r'name="csrf_token" value="([^"]+)"', page_html)
    assert m, "csrf-token не найден"
    return m.group(1)


def _get_csrf_token(manager_client) -> str:
    """Возвращает csrf_token текущей сессии через /login-форму (там он
    рендерится всегда, даже для залогиненного пользователя — страница
    редиректит на корень, но сам токен в сессии уже живёт).

    Использует auth.get_csrf_token напрямую через сессию — но raw
    доступа к сессии у нас тут нет; проще забрать из страницы проекта.
    """
    # Форма /login не подойдёт — залогиненного редиректит. Идём на /projects.
    r = manager_client.get("/configurator/projects")
    assert r.status_code == 200, r.text[:200]
    return _csrf_from(r.text)


# --- Тесты ---------------------------------------------------------------


def test_preview_returns_json_with_drafts(
    db_session, manager_client, manager_user,
):
    """GET /preview → 200, application/json, в items — ожидаемые поля."""
    from portal.services.configurator import spec_service

    sid = _insert_supplier(db_session, name="OCS-test", email="ocs@sup.ru")
    pid = spec_service.create_empty_project(
        db_session, user_id=manager_user["id"], name="Endpoint preview",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-prev")
    _insert_supplier_price(db_session, supplier_id=sid,
                           component_id=cpu_id, price=10000,
                           supplier_sku="ocs-cpu")
    qid = _make_query_with_cpu(
        db_session, project_id=pid, user_id=manager_user["id"], cpu_id=cpu_id,
    )
    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=1)

    with _mock_rate():
        r = manager_client.get(f"/configurator/project/{pid}/emails/preview")

    assert r.status_code == 200, r.text[:200]
    assert "application/json" in r.headers["content-type"]
    data = r.json()
    assert data["ok"] is True
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["supplier_id"] == sid
    assert item["supplier_name"] == "OCS-test"
    assert item["to_email"] == "ocs@sup.ru"
    assert item["subject"] == "Endpoint preview"
    assert item["can_send"] is True
    assert "ocs-cpu" in item["body_html"]


def test_preview_forbidden_for_other_user(
    db_session, app_client, manager_user, manager2_user,
):
    """Чужой проект — 403."""
    from portal.services.configurator import spec_service
    from tests.test_portal.conftest import _login

    pid = spec_service.create_empty_project(
        db_session, user_id=manager_user["id"], name="Not yours",
    )
    _login(app_client, manager2_user["login"], manager2_user["password"])
    with _mock_rate():
        r = app_client.get(f"/configurator/project/{pid}/emails/preview")
    assert r.status_code == 403


def test_send_all_sent_when_sender_mocked(
    db_session, manager_client, manager_user,
):
    """POST /send с валидным payload и замоканным sender → все sent."""
    from portal.services.configurator import spec_service

    sid = _insert_supplier(db_session, name="Sender-A", email="a@sup.ru")
    pid = spec_service.create_empty_project(
        db_session, user_id=manager_user["id"], name="Send-OK",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-send")
    _insert_supplier_price(db_session, supplier_id=sid,
                           component_id=cpu_id, price=10000,
                           supplier_sku="sa-cpu")
    qid = _make_query_with_cpu(
        db_session, project_id=pid, user_id=manager_user["id"], cpu_id=cpu_id,
    )
    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=1)

    csrf = _get_csrf_token(manager_client)

    with _mock_rate(), patch(
        "portal.routers.configurator.export.email_sender.send_email",
        return_value=None,
    ) as send_mock:
        r = manager_client.post(
            f"/configurator/project/{pid}/emails/send",
            headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
            json={"items": [{
                "supplier_id": sid,
                "to_email": "a@sup.ru",
                "subject":  "Тема",
                "body_html": "<p>Привет!</p>",
            }]},
        )

    assert r.status_code == 200, r.text[:400]
    payload = r.json()
    assert payload["ok"] is True
    assert payload["results"] == [{"supplier_id": sid, "status": "sent"}]
    send_mock.assert_called_once()

    # В sent_emails появилась ровно одна запись.
    cnt = db_session.execute(
        _t("SELECT COUNT(*) AS c FROM sent_emails WHERE project_id = :p"),
        {"p": pid},
    ).scalar()
    assert int(cnt) == 1


def test_send_mixed_success_and_smtp_failure(
    db_session, manager_client, manager_user,
):
    """Один получатель проходит, другой падает → смешанные статусы,
    HTTP по-прежнему 200, каждый залоган отдельно."""
    from portal.services.configurator import spec_service
    from portal.services.configurator.export import email_sender

    s_ok   = _insert_supplier(db_session, name="OK-Sup",   email="ok@sup.ru")
    s_fail = _insert_supplier(db_session, name="Fail-Sup", email="fail@sup.ru")
    pid = spec_service.create_empty_project(
        db_session, user_id=manager_user["id"], name="Send-Mixed",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-mix-a")
    # Создадим ещё один компонент, чтобы у s_fail была своя позиция
    cpu_id2 = _insert_cpu(db_session, sku="cpu-mix-b")
    _insert_supplier_price(db_session, supplier_id=s_ok,
                           component_id=cpu_id, price=10000,
                           supplier_sku="ok-cpu")
    _insert_supplier_price(db_session, supplier_id=s_fail,
                           component_id=cpu_id2, price=10000,
                           supplier_sku="fail-cpu")

    # Сформируем один variant с двумя CPU-компонентами. Это хак (конфигуратор
    # такого не породит), но email_composer нужно лишь чтоб оба id были
    # в build_result_json.
    build_result = {
        "status": "ok",
        "variants": [{
            "manufacturer": "Intel",
            "path_used": "default", "used_transit": False,
            "total_usd": 200.0, "total_rub": 18000.0,
            "components": [
                {"category": "cpu", "component_id": cpu_id,
                 "model": "A", "sku": "A-MPN", "manufacturer": "Intel", "quantity": 1},
                {"category": "cpu", "component_id": cpu_id2,
                 "model": "B", "sku": "B-MPN", "manufacturer": "Intel", "quantity": 1},
            ],
            "warnings": [],
        }],
        "refusal_reason": None,
        "usd_rub_rate": 90.0, "fx_source": "fallback",
    }
    row = db_session.execute(_t(
        "INSERT INTO queries "
        "  (project_id, user_id, raw_text, build_result_json, status, "
        "   cost_usd, cost_rub) "
        "VALUES (:pid, :uid, 'mix', CAST(:br AS JSONB), 'ok', 0, 0) "
        "RETURNING id"
    ), {
        "pid": pid, "uid": manager_user["id"],
        "br": json.dumps(build_result, ensure_ascii=False),
    }).first()
    qid = int(row.id)
    db_session.commit()
    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=1)

    # Фабрика побочных эффектов для send_email: для s_fail — ошибка.
    def _fake_send(*, to_email, subject, body_html, bcc=None):
        if to_email == "fail@sup.ru":
            raise email_sender.EmailSendError("SMTP timeout во время теста")
        return None

    csrf = _get_csrf_token(manager_client)

    with _mock_rate(), patch(
        "portal.routers.configurator.export.email_sender.send_email",
        side_effect=_fake_send,
    ):
        r = manager_client.post(
            f"/configurator/project/{pid}/emails/send",
            headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
            json={"items": [
                {"supplier_id": s_ok,   "to_email": "ok@sup.ru",
                 "subject": "X", "body_html": "<p>X</p>"},
                {"supplier_id": s_fail, "to_email": "fail@sup.ru",
                 "subject": "Y", "body_html": "<p>Y</p>"},
            ]},
        )

    assert r.status_code == 200, r.text[:400]
    results = {x["supplier_id"]: x for x in r.json()["results"]}
    assert results[s_ok]["status"] == "sent"
    assert results[s_fail]["status"] == "failed"
    assert "SMTP" in results[s_fail]["error_message"]

    # В sent_emails ровно 2 записи (sent + failed).
    rows = db_session.execute(
        _t("SELECT status FROM sent_emails WHERE project_id = :p ORDER BY id"),
        {"p": pid},
    ).all()
    statuses = sorted(r.status for r in rows)
    assert statuses == ["failed", "sent"]


def test_send_without_csrf_is_rejected(
    db_session, manager_client, manager_user,
):
    """Без X-CSRF-Token POST на /send отклоняется 400."""
    from portal.services.configurator import spec_service
    pid = spec_service.create_empty_project(
        db_session, user_id=manager_user["id"], name="CSRF",
    )
    r = manager_client.post(
        f"/configurator/project/{pid}/emails/send",
        headers={"Content-Type": "application/json"},
        json={"items": []},
    )
    assert r.status_code == 400


# =============================================================================
# Этап 8.6 — поле «Кому» редактируемое: payload.to_email используется как
# фактический адрес отправки, suppliers.email в БД не меняется, невалидный
# email возвращает 400.
# =============================================================================

def test_emails_send_uses_payload_to_email_not_suppliers_email(
    db_session, manager_client, manager_user,
):
    """to_email из payload — это адрес отправки. suppliers.email не трогаем.

    Сценарий: у поставщика в БД email = a@sup.ru, но менеджер
    разово изменил «Кому» на manager-override@sup.ru. SMTP должен
    получить именно введённый адрес, а БД остаться неизменной.
    """
    from portal.services.configurator import spec_service

    sid = _insert_supplier(db_session, name="Override-Sup", email="a@sup.ru")
    pid = spec_service.create_empty_project(
        db_session, user_id=manager_user["id"], name="To-Override",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-override")
    _insert_supplier_price(
        db_session, supplier_id=sid, component_id=cpu_id, price=10000,
        supplier_sku="ov-cpu",
    )
    qid = _make_query_with_cpu(
        db_session, project_id=pid, user_id=manager_user["id"], cpu_id=cpu_id,
    )
    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=1)

    csrf = _get_csrf_token(manager_client)

    with _mock_rate(), patch(
        "portal.routers.configurator.export.email_sender.send_email",
        return_value=None,
    ) as send_mock:
        r = manager_client.post(
            f"/configurator/project/{pid}/emails/send",
            headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
            json={"items": [{
                "supplier_id": sid,
                "to_email": "manager-override@sup.ru",
                "subject":  "Override",
                "body_html": "<p>Override</p>",
            }]},
        )

    assert r.status_code == 200, r.text[:400]
    # SMTP вызван c override-адресом, не с suppliers.email.
    send_mock.assert_called_once()
    kwargs = send_mock.call_args.kwargs
    assert kwargs["to_email"] == "manager-override@sup.ru"

    # suppliers.email в БД остался прежним.
    db_session.expire_all()
    sup_row = db_session.execute(
        _t("SELECT email FROM suppliers WHERE id = :id"), {"id": sid},
    ).first()
    assert sup_row.email == "a@sup.ru"

    # В sent_emails записан фактический адрес отправки.
    rec = db_session.execute(
        _t("SELECT to_email FROM sent_emails WHERE project_id = :p"),
        {"p": pid},
    ).first()
    assert rec.to_email == "manager-override@sup.ru"


def test_emails_send_validates_to_email_returns_400(
    db_session, manager_client, manager_user,
):
    """Невалидный to_email → 400, SMTP не вызывается, suppliers.email цел."""
    from portal.services.configurator import spec_service

    sid = _insert_supplier(db_session, name="Valid-Sup", email="real@sup.ru")
    pid = spec_service.create_empty_project(
        db_session, user_id=manager_user["id"], name="Bad-Email",
    )
    cpu_id = _insert_cpu(db_session, sku="cpu-bad-email")
    _insert_supplier_price(
        db_session, supplier_id=sid, component_id=cpu_id, price=10000,
        supplier_sku="be-cpu",
    )
    qid = _make_query_with_cpu(
        db_session, project_id=pid, user_id=manager_user["id"], cpu_id=cpu_id,
    )
    spec_service.select_variant(db_session, project_id=pid, query_id=qid,
                                manufacturer="Intel", quantity=1)

    csrf = _get_csrf_token(manager_client)

    with _mock_rate(), patch(
        "portal.routers.configurator.export.email_sender.send_email",
        return_value=None,
    ) as send_mock:
        r = manager_client.post(
            f"/configurator/project/{pid}/emails/send",
            headers={"X-CSRF-Token": csrf, "Content-Type": "application/json"},
            json={"items": [{
                "supplier_id": sid,
                "to_email": "не email",
                "subject":  "Тема",
                "body_html": "<p>Текст</p>",
            }]},
        )

    assert r.status_code == 400, r.text[:400]
    # SMTP не вызывался.
    send_mock.assert_not_called()
    # suppliers.email в БД остался прежним.
    db_session.expire_all()
    sup_row = db_session.execute(
        _t("SELECT email FROM suppliers WHERE id = :id"), {"id": sid},
    ).first()
    assert sup_row.email == "real@sup.ru"


def test_emails_modal_to_input_not_readonly():
    """Шаблон project_detail.html: input id=emails-to не имеет readonly."""
    from pathlib import Path
    # UI-4 (Путь B): шаблон переехал в portal/templates/configurator/.
    tpl = Path(__file__).resolve().parents[2] / "portal" / "templates" / "configurator" / "project_detail.html"
    text = tpl.read_text(encoding="utf-8")
    # Найдём фрагмент c id="emails-to" и убедимся, что в нём нет readonly.
    import re
    m = re.search(r'<input[^>]*id="emails-to"[^>]*>', text)
    assert m, "Не найден input id=emails-to в шаблоне"
    tag = m.group(0)
    assert "readonly" not in tag.lower(), (
        f"input id=emails-to не должен быть readonly: {tag}"
    )
    # И проверим, что type="email".
    assert 'type="email"' in tag, f"input id=emails-to должен быть type=email: {tag}"
