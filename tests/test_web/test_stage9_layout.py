# Тесты Этапа 9А.1: дизайн-система, сайдбар, новая раскладка главной
# и страницы проекта.
#
# Подход: рендерим реальные страницы через TestClient (фикстуры
# manager_client / admin_client из conftest), затем проверяем наличие
# ключевых маркеров новой раскладки в HTML.
#
# Тесты намеренно не привязаны к точному порядку или вёрстке: ловим
# текстовые подписи, ID, базовые токены — ровно те, на которые
# завязаны интеграции (JS, скриншоты, переходы по сайдбару).

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.test_web.conftest import (
    extract_csrf,
    parse_query_submit_redirect,
)


# --------------------- helpers ---------------------------------------------

def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _create_project(client: TestClient) -> int:
    r = client.get("/projects")
    token = extract_csrf(r.text)
    r = client.post("/projects", data={"csrf_token": token})
    assert r.status_code == 302
    return int(r.headers["location"].rsplit("/", 1)[1])


def _submit_query_to(client: TestClient, project_id: int) -> int:
    r = client.get(f"/project/{project_id}/new_query")
    token = extract_csrf(r.text)
    r = client.post(
        f"/project/{project_id}/new_query",
        data={"raw_text": "офисный ПК до 50 тысяч", "csrf_token": token},
    )
    assert r.status_code == 302
    _, qid = parse_query_submit_redirect(r.headers["location"])
    return qid


# --------------------- 1. Сайдбар --------------------------------------

def test_base_template_has_sidebar(manager_client):
    """Любая авторизованная страница рендерит сайдбар с тремя
    основными пунктами навигации и карточкой пользователя."""
    r = manager_client.get("/")
    assert r.status_code == 200
    html = r.text

    # Семантика: тег <aside> для сайдбара.
    assert "<aside" in html
    # Бренд + название
    assert "КВАДРО-ТЕХ" in html
    # Три ключевых пункта главной навигации
    assert "Новый запрос" in html
    assert "Проекты" in html
    assert "История запросов" in html
    # Карточка пользователя внизу — имя + признак роли
    assert "менеджер" in html.lower()


# --------------------- 2. Хлебные крошки на странице проекта -----------

def test_breadcrumbs_block_on_project(manager_client, mock_process_query):
    """В шапке страницы проекта в крошках есть имя проекта."""
    pid = _create_project(manager_client)
    _submit_query_to(manager_client, pid)

    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200
    html = r.text

    # Сама хлебная навигация присутствует
    assert 'aria-label="Хлебные крошки"' in html
    # Проекты — первая крошка-ссылка
    assert 'href="/projects"' in html
    # Имя проекта — текущая (последняя) крошка
    assert 'class="crumb-current"' in html
    # И конкретно в crumb-current появляется имя проекта.
    # Проект только что создан без явного имени — web_service.format_project_name
    # подставляет «Запрос от <дата>».
    after_current = html.split('crumb-current')[1]
    assert "Запрос от" in after_current[:300]


# --------------------- 3. Главная — новая раскладка --------------------

def test_index_page_renders_with_new_layout(manager_client):
    """Главная содержит ключевые элементы новой раскладки:
    заголовок, форму нового запроса, блок последних запросов."""
    r = manager_client.get("/")
    assert r.status_code == 200
    html = r.text

    # Заголовок страницы
    assert "Новый запрос" in html
    # Форма с textarea и кнопкой primary
    assert 'id="query-form"' in html
    assert "<textarea" in html
    assert 'name="raw_text"' in html
    assert "Подобрать конфигурацию" in html
    # Раздел «Последние ваши запросы» (есть всегда: пустое состояние или список)
    assert "Последние ваши запросы" in html
    # Локальный CSS подключён (а не CDN)
    assert "/static/dist/main.css" in html
    assert "cdn.tailwindcss.com" not in html


# --------------------- 4. Страница проекта — новая раскладка -----------

def test_project_detail_renders_with_new_layout(
    manager_client, mock_process_query
):
    """На странице проекта: заголовок-display, бейдж количества
    конфигураций, спецификация в правой колонке, экспорт."""
    pid = _create_project(manager_client)
    _submit_query_to(manager_client, pid)

    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200
    html = r.text

    # Бейдж с числом конфигураций
    assert "конфигурац" in html  # «1 конфигурация» / «N конфигураций»
    # Панель спецификации с её ID (на неё завязан JS)
    assert 'id="kt-spec-panel"' in html
    assert "Спецификация" in html
    # Блок экспорта
    assert "Экспорт проекта" in html
    assert "Скачать Excel" in html
    assert "Сформировать КП" in html
    # Кнопка добавления — в шапке через topbar_extra
    assert "Добавить конфигурацию" in html


# --------------------- 5. Видимость админ-секции в сайдбаре ------------

def test_admin_sidebar_visibility_for_admin(admin_client):
    r = admin_client.get("/")
    assert r.status_code == 200
    html = r.text
    # Заголовок секции
    assert "Админ" in html
    # Ключевые пункты админки
    assert "Очередь маппинга" in html
    assert "Бюджет OpenAI" in html
    assert "Пользователи" in html


def test_admin_sidebar_hidden_for_manager(manager_client):
    r = manager_client.get("/")
    assert r.status_code == 200
    html = r.text
    # Менеджер не должен видеть админ-блок
    assert "Очередь маппинга" not in html
    assert "Бюджет OpenAI" not in html
    # Заголовка секции «Админ» в сайдбаре нет
    assert "nav-section-label" not in html or "Админ" not in html


# --------------------- 6. Бюджет CSS-сборки в репозитории --------------

def test_compiled_main_css_exists():
    """static/dist/main.css должен лежать в репозитории — иначе при
    деплое на Railway без Node.js страницы будут без стилей."""
    css = _project_root() / "static" / "dist" / "main.css"
    assert css.exists(), (
        f"Не найден собранный CSS: {css}. "
        "Запустите `npm run build:css` перед коммитом."
    )
    # Маркеры успешной сборки: подключение шрифта Inter и наш токен фон.
    body = css.read_text(encoding="utf-8")
    assert "Inter" in body
    assert "@font-face" in body
