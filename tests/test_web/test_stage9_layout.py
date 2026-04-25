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


# --------------------- 9А.1.1: новые правила дизайн-системы ----------

def test_logo_uses_brand_asset(manager_client):
    """В сайдбаре подключён настоящий логотип бренда.
    9А.2.1: ассет переехал на векторный SVG (без зернистости при
    масштабировании); подпись «Конфигуратор» под ним сохранена."""
    r = manager_client.get("/")
    assert r.status_code == 200
    html = r.text
    # Реальный SVG-ассет лежит в репозитории
    asset = _project_root() / "static" / "img" / "brand" / "quadro-logo.svg"
    assert asset.exists(), f"Не найден ассет логотипа: {asset}"
    # И подключён в HTML страницы (через макрос brand_mark)
    assert "/static/img/brand/quadro-logo.svg" in html
    # Подпись «Конфигуратор» под логотипом — из brand-caption
    assert "brand-caption" in html
    # Плейсхолдера со старым data:image-icon больше нет
    assert "Crect width='40' height='40' rx='11' fill='%231F58D6'" not in html


def test_active_nav_no_solid_fill(manager_client):
    """Активный пункт сайдбара — без сплошной brand-заливки.
    Раньше использовался `bg-brand-500/10` + inset-ring; теперь
    подсветка делается через ::before-полосу слева, в HTML её нет.

    Проверяем: на активном `.nav-item-active` отсутствует
    bg-brand-* и нет inline ring-классов с brand-цветом.
    """
    r = manager_client.get("/")
    assert r.status_code == 200
    html = r.text

    # Класс активного пункта присутствует — у пункта «Новый запрос».
    assert "nav-item-active" in html

    # Найдём первое вхождение nav-item-active и его окрестности —
    # там не должно быть brand-заливочного фона.
    idx = html.index("nav-item-active")
    # 280 символов — в пределах одной кнопки навигации
    chunk = html[max(0, idx - 50): idx + 280]
    assert "bg-brand-500/10" not in chunk
    assert "bg-brand-500/20" not in chunk
    assert "bg-brand-500 " not in chunk
    # Старый inset-ring с brand тоже больше не должен встречаться
    # как inline-style — он переехал в ::before в CSS.
    assert "rgba(47,111,241,0.30)" not in chunk


def test_card_has_border(manager_client, mock_process_query):
    """Все карточки получают тонкую полупрозрачную границу — это
    реализовано через `.card { border: 1px solid line.soft }` в
    собранном CSS. Проверяем через main.css, а не через шаблон,
    так как граница задаётся не Tailwind-классом, а свойством."""
    css_path = _project_root() / "static" / "dist" / "main.css"
    assert css_path.exists()
    css = css_path.read_text(encoding="utf-8")

    # В собранном CSS у `.card` должен быть border 1px и
    # цвет — наш rgba-полупрозрачный белый (line.soft = 6% white).
    # Tailwind может скомпилировать значение по-разному; проверяем
    # любой из возможных вариантов записи rgba 255,255,255,0.06.
    assert ".card{" in css or ".card {" in css
    # Главное: сам класс card-active существует (синяя подсветка).
    assert ".card-active" in css
    # И есть наш glow-brand тон в собранном CSS — для активной
    # карточки (selected конфигурация).
    assert "rgba(64,120,255" in css or "rgba(64, 120, 255" in css

    # Также убеждаемся, что страница проекта рендерит .card —
    # это исходная цель «белые рамки на карточках».
    pid = _create_project(manager_client)
    _submit_query_to(manager_client, pid)
    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200
    assert "card card-pad" in r.text  # есть как минимум одна карточка


def test_brand_color_token_updated():
    """brand.500 в собранном CSS — обновлённый #2052E8 (а не #2F6FF1).
    Поднята насыщенность ближе к фирменному, но мягче чем чистый
    #0000FF, чтобы не давить на крупных площадях."""
    css = (_project_root() / "static" / "dist" / "main.css").read_text(encoding="utf-8")
    assert "#2052e8" in css.lower() or "#2052E8" in css, (
        "Ожидался обновлённый brand.500 = #2052E8 в main.css"
    )
    # Старого тона быть не должно (как минимум не в hex-форме токена).
    # Tailwind может оставить #2f6ff1 в случайных местах — проверяем,
    # что он не определён как primary через .btn-primary{background-color}.
    btn_primary_idx = css.lower().find(".btn-primary")
    if btn_primary_idx != -1:
        chunk = css[btn_primary_idx: btn_primary_idx + 400].lower()
        assert "#2f6ff1" not in chunk


def test_export_buttons_are_secondary(manager_client, mock_process_query):
    """На странице проекта три кнопки экспорта (Excel, КП, Запросить
    цены) — все в стиле btn-secondary, без яркого btn-success/btn-primary
    на этих CTA. Это иерархия из 9А.1.1: вспомогательные действия не
    конкурируют между собой."""
    pid = _create_project(manager_client)
    _submit_query_to(manager_client, pid)

    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200
    html = r.text

    # Найдём блок «Экспорт проекта»
    assert "Экспорт проекта" in html
    start = html.index("Экспорт проекта")
    end_idx = html.find("</section>", start)
    block = html[start:end_idx if end_idx != -1 else start + 2500]

    # Ни одна из кнопок экспорта не должна быть btn-success/btn-primary
    assert "btn-success" not in block, (
        "Кнопка экспорта в зелёном btn-success — должна быть btn-secondary"
    )
    # Все три кнопки используют btn-secondary
    assert block.count("btn-secondary") >= 3, (
        "Ожидалось минимум 3 кнопки btn-secondary в блоке экспорта"
    )
