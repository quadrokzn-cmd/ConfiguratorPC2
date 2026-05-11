# Этап 12.5a: HTML-модал вместо нативного confirm() на admin-страницах.
#
# Тестируем:
# - Скрипт static/js/portal-dialog.js подключён через portal/base.html.
# - Файл portal-dialog.js содержит ключевые публичные API
#   (confirmDialog, toastDialog) и aria-атрибуты, нужные для access.
# - На /admin/auto-price-loads и /settings/backups больше нет inline
#   onsubmit="return confirm(...)".
# - На этих страницах есть форма с классом kt-confirm-form
#   и data-confirm-message — это та идиома, на которую хук JS навешивает
#   HTML-модал.
# - На /admin/price-uploads и /settings/users в <script>-блоках больше нет
#   нативных вызовов alert() и confirm() — заменены на window.toastDialog
#   и window.confirmDialog.

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import text


REPO_ROOT = Path(__file__).resolve().parents[2]
PORTAL_DIALOG_JS = REPO_ROOT / "static" / "js" / "portal-dialog.js"


def _strip_js_comments(src: str) -> str:
    """Срезает однострочные `// ...` и многострочные `/* ... */`
    JS-комментарии. Нужно, чтобы grep'аем только реальный код:
    в комментариях слова `confirm()` и `alert()` остаются как
    исторические маркеры — это допустимо."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"//[^\n]*", "", src)
    return src


# --- 1. Сам JS-файл ----------------------------------------------------

def test_portal_dialog_js_file_exists():
    assert PORTAL_DIALOG_JS.is_file(), (
        f"Ожидался файл {PORTAL_DIALOG_JS}, но его нет"
    )


def test_portal_dialog_js_exposes_public_api():
    src = PORTAL_DIALOG_JS.read_text(encoding="utf-8")
    # Глобальные хелперы.
    assert "window.confirmDialog" in src
    assert "window.toastDialog" in src
    # Auto-wiring форм.
    assert "kt-confirm-form" in src


def test_portal_dialog_js_has_aria_attributes_for_a11y():
    """Доступность: модал должен ставить aria-modal/aria-labelledby/
    aria-describedby и role=dialog. Ровно эти атрибуты строит JS."""
    src = PORTAL_DIALOG_JS.read_text(encoding="utf-8")
    assert 'aria-modal="true"' in src
    assert "aria-labelledby" in src
    assert "aria-describedby" in src
    assert 'role="dialog"' in src


def test_portal_dialog_js_handles_esc_and_backdrop():
    """Esc и клик по подложке должны давать «Отменить». Проверяем,
    что в коде есть оба обработчика — иначе computer-use агенты не
    смогут «обойти» модал по ошибке (баг типа «Esc подтверждает»)."""
    src = PORTAL_DIALOG_JS.read_text(encoding="utf-8")
    assert "'Escape'" in src or '"Escape"' in src
    # Клик по самой подложке (e.target === overlay) → close(false).
    assert "e.target === overlay" in src


# --- 2. Шаблон base.html подключает скрипт -----------------------------

def test_portal_base_template_includes_dialog_script():
    base_html = (REPO_ROOT / "portal" / "templates" / "base.html").read_text(
        encoding="utf-8"
    )
    assert "static/js/portal-dialog.js" in base_html, (
        "portal/base.html должен подключать portal-dialog.js"
    )


# --- 3. /admin/auto-price-loads больше не использует native confirm() --

def _seed_auto(db_engine):
    with db_engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE TABLE "
            "  auto_price_load_runs, auto_price_loads "
            "RESTART IDENTITY CASCADE"
        ))
        conn.execute(text(
            "INSERT INTO auto_price_loads (supplier_slug, enabled) VALUES "
            "  ('treolan', FALSE), ('ocs', FALSE), ('merlion', FALSE), "
            "  ('netlab', FALSE), ('resurs_media', FALSE), ('green_place', FALSE) "
            "ON CONFLICT (supplier_slug) DO NOTHING"
        ))


def test_auto_price_loads_uses_html_modal_not_native_confirm(
    admin_portal_client, db_engine
):
    _seed_auto(db_engine)
    r = admin_portal_client.get("/admin/auto-price-loads")
    assert r.status_code == 200
    html = r.text

    # Главное: ни одного inline onsubmit="return confirm(...)".
    assert 'onsubmit="return confirm(' not in html
    # Идиома 12.5a — формы с kt-confirm-form + data-confirm-message.
    assert "kt-confirm-form" in html
    assert "data-confirm-message=" in html

    # И сам скрипт-модал на странице тоже должен быть подключён.
    assert "static/js/portal-dialog.js" in html


def test_auto_price_loads_run_form_has_confirm_message_text(
    admin_portal_client, db_engine
):
    _seed_auto(db_engine)
    r = admin_portal_client.get("/admin/auto-price-loads")
    assert r.status_code == 200
    # Сообщение модала для «Запустить» содержит ключевые слова.
    assert re.search(
        r'data-confirm-message="Запустить автозагрузку прайса',
        r.text,
    )


# --- 4. /settings/backups — то же самое -----------------------------------

def test_backups_create_uses_html_modal(admin_portal_client):
    r = admin_portal_client.get("/settings/backups")
    assert r.status_code == 200
    html = r.text
    assert 'onsubmit="return confirm(' not in html
    assert "kt-confirm-form" in html
    assert "Создать резервную копию" in html


# --- 5. /admin/price-uploads --- больше нет alert()/confirm() в JS ---

def test_price_uploads_js_no_native_confirm_or_alert(admin_portal_client):
    r = admin_portal_client.get("/admin/price-uploads")
    assert r.status_code == 200
    html = r.text
    # Берём содержимое <script>-блоков, режем JS-комментарии — в них
    # остаются маркеры «confirm()/alert()» как исторические.
    scripts = re.findall(r"<script[^>]*>(.*?)</script>",
                         html, re.DOTALL)
    code = _strip_js_comments("\n".join(scripts))
    # Ни одного нативного вызова — только confirmDialog/toastDialog.
    assert re.search(r"\balert\s*\(", code) is None, (
        "В price_uploads.html остался нативный alert(): "
        + (code[:300] if code else "")
    )
    assert re.search(r"(?<![A-Za-z_])confirm\s*\(", code) is None, (
        "В price_uploads.html остался нативный confirm()"
    )
    # Зато есть новые helpers.
    assert "confirmDialog" in code
    assert "toastDialog" in code


# --- 6. /settings/users --- self-demotion и hard-delete тоже HTML-модал ---

def test_users_js_no_native_confirm_or_alert(admin_portal_client):
    r = admin_portal_client.get("/settings/users")
    assert r.status_code == 200
    html = r.text
    scripts = re.findall(r"<script[^>]*>(.*?)</script>",
                         html, re.DOTALL)
    code = _strip_js_comments("\n".join(scripts))
    # confirm() и alert() — больше нет (в коде; в комментариях допустимо).
    assert re.search(r"(?<![A-Za-z_])confirm\s*\(", code) is None, (
        "В users.html остался нативный confirm()"
    )
    assert re.search(r"\balert\s*\(", code) is None, (
        "В users.html остался нативный alert()"
    )
    # confirmDialog присутствует (для self-demotion и hard-delete).
    assert "confirmDialog" in code
