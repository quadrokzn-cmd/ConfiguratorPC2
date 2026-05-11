# Тесты UI-разметки модалки «Запросить цены у поставщикам» (этап 8.5).
#
# Проверяется рендер /project/{id}:
#   - присутствует <div id="emails-body-editor" contenteditable="true">
#   - нет старой textarea#emails-body-html и нет iframe#emails-preview-frame
#   - в скрипте есть helper plural(…)
#   - в скрипте есть paste-handler на bodyEditor
#
# Отдельно покрываем поведение JS-хелпера plural(…) — через Python-реплику,
# чтобы не тянуть в проект JS-рантайм. Алгоритм идентичен тому, что лежит
# в project_detail.html.

from __future__ import annotations

import pytest

from portal.services.configurator import spec_service


# --- Хелпер на Python (копия JS-логики) -----------------------------------

def _plural(n: int, forms: tuple[str, str, str]) -> str:
    """Копия JS-хелпера plural(n, forms) для юнит-покрытия алгоритма."""
    n = abs(n) % 100
    n1 = n % 10
    if 10 < n < 20:
        return forms[2]
    if 1 < n1 < 5:
        return forms[1]
    if n1 == 1:
        return forms[0]
    return forms[2]


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "позиций"),
        (1, "позиция"),
        (2, "позиции"),
        (3, "позиции"),
        (4, "позиции"),
        (5, "позиций"),
        (10, "позиций"),
        (11, "позиций"),
        (12, "позиций"),
        (14, "позиций"),
        (15, "позиций"),
        (20, "позиций"),
        (21, "позиция"),
        (22, "позиции"),
        (25, "позиций"),
        (101, "позиция"),
        (111, "позиций"),
        (122, "позиции"),
    ],
)
def test_plural_helper_matches_russian_rules(n, expected):
    assert _plural(n, ("позиция", "позиции", "позиций")) == expected


# --- Рендер страницы проекта ----------------------------------------------


def test_email_modal_has_contenteditable_and_no_textarea(
    db_session, manager_client, manager_user,
):
    """В разметке модалки должен быть contenteditable-редактор и не должно
    остаться textarea#emails-body-html / iframe#emails-preview-frame."""
    pid = spec_service.create_empty_project(
        db_session, user_id=manager_user["id"], name="UI-check",
    )

    r = manager_client.get(f"/configurator/project/{pid}")
    assert r.status_code == 200, r.text[:200]
    html = r.text

    # Новый редактор присутствует
    assert 'id="emails-body-editor"' in html
    assert 'contenteditable="true"' in html

    # Старые элементы удалены
    assert 'id="emails-body-html"' not in html, (
        "Старая textarea с id=emails-body-html должна быть удалена"
    )
    assert 'id="emails-preview-frame"' not in html, (
        "Старый iframe-предпросмотр должен быть удалён"
    )

    # Заголовок секции больше не «Тело письма (HTML)»
    assert "Тело письма (HTML)" not in html

    # Старая метка «HTML-предпросмотр» больше не рендерится
    assert "HTML-предпросмотр" not in html


def test_email_modal_script_has_plural_and_paste_handler(
    db_session, manager_client, manager_user,
):
    """В скрипте модалки должен быть helper plural(...) и paste-обработчик
    на bodyEditor (чистка вставки из буфера)."""
    pid = spec_service.create_empty_project(
        db_session, user_id=manager_user["id"], name="UI-script-check",
    )
    r = manager_client.get(f"/configurator/project/{pid}")
    assert r.status_code == 200
    html = r.text

    # Хелпер склонения
    assert "function plural(" in html
    assert "['позиция', 'позиции', 'позиций']" in html

    # Paste-handler на редакторе
    assert "bodyEditor.addEventListener('paste'" in html
    assert "insertText" in html  # execCommand('insertText', ...)


def test_email_modal_no_html_textarea_label(
    db_session, manager_client, manager_user,
):
    """Заголовок секции тела письма — «Тело письма», без «(HTML)»."""
    pid = spec_service.create_empty_project(
        db_session, user_id=manager_user["id"], name="UI-label-check",
    )
    r = manager_client.get(f"/configurator/project/{pid}")
    assert r.status_code == 200
    # Этап 9А.1: лейблы без trailing-колона (style guide новой дизайн-системы).
    assert "Тело письма" in r.text
    assert "(HTML)" not in r.text
