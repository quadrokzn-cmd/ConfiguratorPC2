# Тесты ручной загрузки прайс-листов в портале (этап 11.2).
#
# Покрытие:
#   1) Доступы: anon → 302 на /login, manager → 403, admin → 200.
#   2) В таблице ровно 6 поставщиков (после миграций 001/019 они уже в БД).
#   3) Бейджи свежести: ≤24ч, 24–72ч, >72ч, none.
#   4) POST /run с валидным merlion-XLSX → 302 + запись в price_uploads
#      (фоновая задача в TestClient выполняется до возврата ответа).
#   5) POST /run с неизвестным supplier_slug → 400.
#   6) POST /run с файлом > 100 МБ → 413.
#   7) audit_log получает PRICE_UPLOAD_START после POST.
#   8) Журнал последних загрузок отображается в обратном хронологическом
#      порядке.
#   9) GET /<id>/details возвращает report_json как JSON.

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text as _t

from tests.test_portal.conftest import extract_csrf


# ---------------------------------------------------------------------
# Чистка таблиц, специфичных для price_uploads.
#
# tests/test_portal/conftest.py truncate'ит users/projects/audit и пр.,
# но не suppliers/price_uploads/supplier_prices/компонентов. Это нам и
# нужно: миграции 019/020 создали 6 поставщиков, и мы хотим их сохранить.
# Здесь мы только TRUNCATE TABLE price_uploads + supplier_prices между
# тестами, чтобы каждый тест начинал с чистого журнала.
# ---------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_price_uploads_tables(db_engine):
    with db_engine.begin() as conn:
        conn.execute(_t(
            "TRUNCATE TABLE "
            "  unmapped_supplier_items, supplier_prices, price_uploads, "
            "  cpus, motherboards, rams, gpus, storages, cases, psus, coolers "
            "RESTART IDENTITY CASCADE"
        ))
    yield


# ---- Хелперы ----------------------------------------------------------

def _ensure_supplier(db, name: str) -> int:
    """suppliers уже существуют после миграций 001/019/020. Достаём id."""
    row = db.execute(
        _t("INSERT INTO suppliers (name, is_active) VALUES (:n, TRUE) "
           "ON CONFLICT (name) DO UPDATE SET is_active=TRUE "
           "RETURNING id"),
        {"n": name},
    ).first()
    db.commit()
    return int(row.id)


def _insert_upload(
    db,
    supplier_id: int,
    *,
    hours_ago: float = 0,
    status: str = "success",
    filename: str = "x.xlsx",
    report: dict | None = None,
) -> int:
    """Добавляет запись price_uploads с заданным uploaded_at и report_json."""
    row = db.execute(
        _t(
            "INSERT INTO price_uploads "
            "  (supplier_id, filename, uploaded_at, rows_total, rows_matched, "
            "   rows_unmatched, status, notes, report_json) "
            "VALUES "
            "  (:sid, :fn, NOW() - (:h || ' hours')::interval, "
            "   :total, :matched, :unmatched, :st, :notes, "
            "   CAST(:report AS JSONB)) "
            "RETURNING id"
        ),
        {
            "sid": supplier_id,
            "fn": filename,
            "h": hours_ago,
            "total": (report or {}).get("processed", 100),
            "matched": (report or {}).get("updated", 100),
            "unmatched": (report or {}).get("skipped", 0),
            "st": status,
            "notes": "test",
            "report": json.dumps(report or {}, ensure_ascii=False),
        },
    ).first()
    db.commit()
    return int(row.id)


# ---- 1. Доступы --------------------------------------------------------


def test_anonymous_redirected_to_login(portal_client):
    r = portal_client.get("/admin/price-uploads")
    assert r.status_code == 302
    assert "/login" in r.headers.get("location", "")


def test_manager_gets_403(manager_portal_client):
    r = manager_portal_client.get("/admin/price-uploads")
    assert r.status_code == 403


def test_admin_sees_page(admin_portal_client, db_session):
    # Гарантируем, что 6 поставщиков есть (миграции 001 + 019).
    for name in ("OCS", "Merlion", "Treolan", "Netlab", "Ресурс Медиа", "Green Place"):
        _ensure_supplier(db_session, name)

    r = admin_portal_client.get("/admin/price-uploads")
    assert r.status_code == 200
    assert "Прайс-листы поставщиков" in r.text


# ---- 2. Шесть поставщиков в таблице -----------------------------------


def test_admin_sees_six_suppliers(admin_portal_client, db_session):
    for name in ("OCS", "Merlion", "Treolan", "Netlab", "Ресурс Медиа", "Green Place"):
        _ensure_supplier(db_session, name)

    r = admin_portal_client.get("/admin/price-uploads")
    assert r.status_code == 200

    # 6 строк по data-testid маркерам.
    for slug in ("ocs", "merlion", "treolan", "netlab", "resurs_media", "green_place"):
        assert f'data-testid="supplier-row-{slug}"' in r.text, (
            f"Не вижу строку поставщика {slug}: {r.text[:200]}..."
        )


# ---- 3. Бейджи свежести ----------------------------------------------


def test_freshness_badges_render_correctly(admin_portal_client, db_session):
    """Загружаем 4 поставщика с разной свежестью и проверяем CSS-классы.

    OCS — 1 час назад → badge-success «Свежий»
    Merlion — 50 часов → badge-warning «Устаревает»
    Treolan — 100 часов → badge-danger «Старый»
    Netlab — без загрузок → badge-neutral «Не загружался»
    """
    ocs_id = _ensure_supplier(db_session, "OCS")
    merlion_id = _ensure_supplier(db_session, "Merlion")
    treolan_id = _ensure_supplier(db_session, "Treolan")
    _ensure_supplier(db_session, "Netlab")
    _ensure_supplier(db_session, "Ресурс Медиа")
    _ensure_supplier(db_session, "Green Place")

    _insert_upload(db_session, ocs_id, hours_ago=1, status="success")
    _insert_upload(db_session, merlion_id, hours_ago=50, status="success")
    _insert_upload(db_session, treolan_id, hours_ago=100, status="success")

    r = admin_portal_client.get("/admin/price-uploads")
    assert r.status_code == 200

    def _badge_class(html: str, slug: str) -> str:
        """Возвращает CSS-класс бейджа конкретного поставщика."""
        import re
        m = re.search(
            r'<span class="badge ([a-z\-]+)"\s+data-testid="badge-' + slug + r'"',
            html,
        )
        assert m, f"Не нашёл бейдж для {slug}"
        return m.group(1)

    assert _badge_class(r.text, "ocs") == "badge-success"
    assert _badge_class(r.text, "merlion") == "badge-warning"
    assert _badge_class(r.text, "treolan") == "badge-danger"
    assert _badge_class(r.text, "netlab") == "badge-neutral"


# ---- 4. POST /run с merlion-XLSX -------------------------------------


def test_upload_form_accepts_merlion_xlsx(
    admin_portal_client, db_session, make_merlion_xlsx,
):
    """Смешанный merlion-прайс (1 матч + 1 новый + 1 skipped) → 302
    редирект, в price_uploads появилась запись со статусом success/partial.
    """
    # Заранее существующая мать — чтобы один товар сматчился.
    db_session.execute(_t(
        "INSERT INTO motherboards "
        "  (model, manufacturer, sku, socket, chipset, form_factor, "
        "   memory_type, has_m2_slot) "
        "VALUES ('PRIME H610M-E D4', 'ASUS', 'PRIME-H610M-E', 'LGA1700', "
        "        'H610', 'mATX', 'DDR4', TRUE)"
    ))
    db_session.commit()

    path = make_merlion_xlsx([
        {
            "g1": "Комплектующие для компьютеров", "g2": "Материнские Платы",
            "g3": "Socket-1700",
            "brand": "ASUS", "number": "M-001", "mpn": "PRIME-H610M-E",
            "name": "ASUS PRIME H610M-E D4", "price_rub": 8500, "stock": 3,
        },
        {
            "g1": "Техника", "g2": "Телевизоры", "g3": "OLED",
            "brand": "LG", "number": "M-002", "mpn": "OLED77C3",
            "name": "LG OLED77C3", "price_rub": 200000, "stock": 1,
        },
    ])

    # Берём CSRF со страницы.
    r0 = admin_portal_client.get("/admin/price-uploads")
    assert r0.status_code == 200
    token = extract_csrf(r0.text)

    with open(path, "rb") as f:
        r = admin_portal_client.post(
            "/admin/price-uploads/run",
            data={
                "csrf_token":    token,
                "supplier_slug": "merlion",
            },
            files={
                "uploaded_file": (
                    "Прайслист_Мерлион.xlsm",
                    f.read(),
                    "application/vnd.ms-excel.sheet.macroEnabled.12",
                ),
            },
        )
    # 302 редирект на /admin/price-uploads.
    assert r.status_code == 302, r.text[:300]
    assert r.headers.get("location") == "/admin/price-uploads"

    # В TestClient FastAPI выполняет BackgroundTasks ДО завершения вызова —
    # это проверено в тестах backups (см. там логику с background_tasks).
    # Запись в price_uploads должна быть.
    rows = db_session.execute(_t(
        "SELECT pu.id, pu.status, pu.report_json, s.name "
        "FROM price_uploads pu JOIN suppliers s ON s.id=pu.supplier_id "
        "WHERE s.name = 'Merlion'"
    )).all()
    assert len(rows) == 1, f"Ожидалась 1 запись, а вернулось {len(rows)}"
    rec = rows[0]
    assert rec.status in ("success", "partial"), rec.status
    # report_json — JSONB; драйвер вернёт dict.
    report = rec.report_json
    if isinstance(report, str):
        report = json.loads(report)
    assert report.get("supplier") == "Merlion"
    assert "added" in report
    assert "updated" in report


# ---- 5. POST /run с невалидным slug ----------------------------------


def test_upload_rejects_invalid_supplier_slug(admin_portal_client):
    r0 = admin_portal_client.get("/admin/price-uploads")
    token = extract_csrf(r0.text)

    fake = io.BytesIO(b"not a real xlsx")
    r = admin_portal_client.post(
        "/admin/price-uploads/run",
        data={"csrf_token": token, "supplier_slug": "hacker"},
        files={"uploaded_file": ("evil.xlsx", fake, "application/octet-stream")},
    )
    assert r.status_code == 400


# ---- 6. POST /run с большим файлом ------------------------------------


def test_upload_rejects_oversize_file(admin_portal_client):
    r0 = admin_portal_client.get("/admin/price-uploads")
    token = extract_csrf(r0.text)

    # 101 МБ — на байт больше лимита. Это не реальный xlsx, но валидация
    # размера срабатывает раньше парсера.
    big = b"\x00" * (101 * 1024 * 1024)
    r = admin_portal_client.post(
        "/admin/price-uploads/run",
        data={"csrf_token": token, "supplier_slug": "merlion"},
        files={"uploaded_file": ("big.xlsx", big, "application/octet-stream")},
    )
    assert r.status_code == 413


# ---- 7. audit_log получает PRICE_UPLOAD_START -------------------------


def test_audit_log_receives_price_upload_start(
    admin_portal_client, db_session, make_merlion_xlsx,
):
    path = make_merlion_xlsx([
        {
            "g1": "Техника", "g2": "Телевизоры", "g3": "OLED",
            "brand": "LG", "number": "M-001", "mpn": "OLED77C3",
            "name": "LG OLED77C3", "price_rub": 200000, "stock": 1,
        },
    ])

    r0 = admin_portal_client.get("/admin/price-uploads")
    token = extract_csrf(r0.text)

    with open(path, "rb") as f:
        admin_portal_client.post(
            "/admin/price-uploads/run",
            data={"csrf_token": token, "supplier_slug": "merlion"},
            files={"uploaded_file": ("Мерлион.xlsm", f.read(), "application/octet-stream")},
        )

    # Audit-лог пишется в отдельной транзакции — нужно явно перечитать.
    db_session.expire_all()
    rows = db_session.execute(_t(
        "SELECT action, payload FROM audit_log "
        "WHERE action = 'price_upload.start' OR action = 'price_upload.complete'"
        "ORDER BY id"
    )).all()
    assert any(r.action == "price_upload.start" for r in rows)


# ---- 8. Журнал в обратном хронологическом порядке --------------------


def test_journal_shows_uploads_in_reverse_chronological_order(
    admin_portal_client, db_session,
):
    ocs_id = _ensure_supplier(db_session, "OCS")
    merlion_id = _ensure_supplier(db_session, "Merlion")
    treolan_id = _ensure_supplier(db_session, "Treolan")

    _insert_upload(db_session, ocs_id, hours_ago=72, filename="ocs_old.xlsx")
    _insert_upload(db_session, merlion_id, hours_ago=24, filename="merlion_mid.xlsm")
    _insert_upload(db_session, treolan_id, hours_ago=1, filename="treolan_new.xlsx")

    r = admin_portal_client.get("/admin/price-uploads")
    assert r.status_code == 200

    # treolan_new раньше merlion_mid, тот раньше ocs_old.
    pos_new = r.text.find("treolan_new.xlsx")
    pos_mid = r.text.find("merlion_mid.xlsm")
    pos_old = r.text.find("ocs_old.xlsx")
    assert 0 <= pos_new < pos_mid < pos_old, (
        f"Неверный порядок: new={pos_new}, mid={pos_mid}, old={pos_old}"
    )


# ---- 9. /<id>/details возвращает JSON --------------------------------


def test_upload_details_returns_report_json(admin_portal_client, db_session):
    ocs_id = _ensure_supplier(db_session, "OCS")
    upl_id = _insert_upload(
        db_session, ocs_id,
        hours_ago=2, status="success", filename="ocs.xlsx",
        report={"supplier": "OCS", "added": 5, "updated": 100, "errors": 0},
    )

    r = admin_portal_client.get(f"/admin/price-uploads/{upl_id}/details")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == upl_id
    assert data["supplier"] == "OCS"
    assert data["report"]["added"] == 5
    assert data["report"]["updated"] == 100


def test_upload_details_404_for_unknown_id(admin_portal_client):
    r = admin_portal_client.get("/admin/price-uploads/999999/details")
    assert r.status_code == 404


def test_upload_details_blocks_anonymous(portal_client, db_session):
    ocs_id = _ensure_supplier(db_session, "OCS")
    upl_id = _insert_upload(db_session, ocs_id, hours_ago=1)

    r = portal_client.get(f"/admin/price-uploads/{upl_id}/details")
    assert r.status_code == 302  # require_admin → require_login → 302


def test_upload_details_blocks_manager(manager_portal_client, db_session):
    ocs_id = _ensure_supplier(db_session, "OCS")
    upl_id = _insert_upload(db_session, ocs_id, hours_ago=1)

    r = manager_portal_client.get(f"/admin/price-uploads/{upl_id}/details")
    assert r.status_code == 403
