# Тесты Этапа 9А.1.3: точечные правки UI по обратной связи заказчика.
#
# 1. У всех input[type=number] скрыты нативные браузерные стрелки —
#    заменены на кастомный stepper в стиле дизайн-системы.
# 2. Логотип в сайдбаре увеличен (max-width >= 180px), чтобы по ширине
#    совпадал с надписью «КОНФИГУРАТОР» под ним.
#
# Подход: для CSS-маркеров читаем собранный static/dist/main.css; для
# шаблонных маркеров рендерим страницы через TestClient (manager_client).

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_web.conftest import (
    extract_csrf,
    parse_query_submit_redirect,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_css() -> str:
    css = _project_root() / "static" / "dist" / "main.css"
    assert css.exists(), (
        f"Не найден собранный CSS: {css}. "
        "Запустите `npm run build:css` перед коммитом."
    )
    return css.read_text(encoding="utf-8")


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


# --------------------- 1. Скрытые нативные стрелки number-input ----------

def test_number_input_native_arrows_hidden():
    """В собранном CSS присутствуют правила для скрытия нативных
    spinner-arrows у input[type=number] — иначе они отображаются
    белыми поверх тёмной темы и выглядят чужеродно.

    Webkit (Chrome/Edge/Safari): ::-webkit-inner-spin-button и
    ::-webkit-outer-spin-button с -webkit-appearance: none.
    Firefox: -moz-appearance: textfield.
    """
    css = _read_css()
    compact = css.replace(" ", "").replace("\n", "")

    # Webkit-псевдоэлементы упоминаются (хотя бы один из двух)
    assert (
        "::-webkit-inner-spin-button" in css
        or ":-webkit-inner-spin-button" in css
    ), "Нет правила для ::-webkit-inner-spin-button"
    # И у этого правила есть -webkit-appearance:none
    assert "-webkit-appearance:none" in compact, (
        "У спин-баттонов должен быть -webkit-appearance: none"
    )
    # Firefox-вариант
    assert "-moz-appearance:textfield" in compact, (
        "У input[type=number] должен быть -moz-appearance: textfield"
    )


# --------------------- 2. Кастомный stepper в шаблонах -------------------

def test_number_input_custom_stepper_present(
    manager_client, mock_process_query
):
    """В шаблоне страницы проекта присутствует кастомный stepper —
    обёртка .kt-num-stepper с двумя кнопками .kt-num-stepper-up и
    .kt-num-stepper-down. На странице проекта с конфигурациями stepper
    встречается минимум для двух полей: qty (количество в карточке
    варианта) и markup-input (наценка в блоке экспорта)."""
    pid = _create_project(manager_client)
    _submit_query_to(manager_client, pid)

    r = manager_client.get(f"/project/{pid}")
    assert r.status_code == 200
    html = r.text

    # Класс-обёртка
    assert "kt-num-stepper" in html, (
        "На странице проекта должна быть обёртка .kt-num-stepper "
        "вокруг input[type=number]"
    )
    # Обе кнопки
    assert "kt-num-stepper-up" in html
    assert "kt-num-stepper-down" in html
    # Минимум 2 пары — для qty и markup
    assert html.count("kt-num-stepper-up") >= 2, (
        "Ожидалось минимум 2 stepper-кнопки 'вверх' на странице проекта "
        "(qty в карточке варианта + наценка в экспорте)"
    )

    # И никаких голых input[type=number] без обёртки .kt-num-stepper
    # рядом — проверим, что markup-input находится внутри stepper.
    assert 'id="markup-input"' in html
    markup_idx = html.index('id="markup-input"')
    # Ищем kt-num-stepper в окрестности markup-input (рядом с тегом)
    nearby = html[max(0, markup_idx - 400): markup_idx]
    assert "kt-num-stepper" in nearby, (
        "markup-input должен быть внутри обёртки .kt-num-stepper"
    )


def test_number_stepper_css_rules_compiled():
    """В собранном CSS присутствуют правила для .kt-num-stepper и
    его кнопок — без них кастомный stepper будет просто текстом."""
    css = _read_css()
    assert ".kt-num-stepper" in css
    # Кнопки должны иметь определения (hover-color, размер и т.д.)
    assert ".kt-num-stepper-up" in css
    assert ".kt-num-stepper-down" in css


def test_project_js_initializes_stepper():
    """В static/js/project.js есть код инициализации stepper-кнопок —
    клик по ним должен менять input.value и диспатчить change/input."""
    js_path = _project_root() / "static" / "js" / "project.js"
    js = js_path.read_text(encoding="utf-8")
    assert "kt-num-stepper" in js, (
        "static/js/project.js должен инициализировать stepper-кнопки"
    )
    # Проверяем, что есть отправка событий — иначе остальная логика
    # не подхватит изменение значения.
    assert "dispatchEvent" in js


# --------------------- 3. Логотип увеличен -----------------------------

def test_logo_max_width_increased():
    """В собранном CSS у класса .kt-brand-logo (логотип в сайдбаре)
    max-width >= 180px — иначе логотип останется визуально мелким
    относительно надписи «КОНФИГУРАТОР» под ним."""
    css = _read_css()
    assert ".kt-brand-logo" in css, (
        "Ожидался класс .kt-brand-logo для логотипа в сайдбаре"
    )

    # Найдём блок .kt-brand-logo{...} — может быть несколько (с media-query),
    # нас интересует базовый (без @media), у него max-width >= 180px.
    idx = css.find(".kt-brand-logo")
    assert idx != -1
    block_end = css.find("}", idx)
    base_block = css[idx:block_end]
    # max-width в px либо как 180px / 200px и т.д.
    import re
    m = re.search(r"max-width\s*:\s*(\d+)px", base_block)
    assert m, "У .kt-brand-logo не задан max-width в px"
    value = int(m.group(1))
    assert value >= 180, (
        f"max-width у .kt-brand-logo = {value}px, ожидалось >= 180px"
    )


def test_logo_uses_new_class_in_sidebar(manager_client):
    """В сайдбаре логотип теперь подключён через класс .kt-brand-logo,
    а не через старый h-8 (90px при аспекте PNG 2.83). Проверяем
    HTML — класс присутствует на <img> логотипа."""
    r = manager_client.get("/")
    assert r.status_code == 200
    html = r.text
    # PNG-ассет тот же, что и в 9А.1.1
    assert "/static/img/brand/quadro-logo-white.png" in html
    # У <img>-тега логотипа должен быть наш класс
    img_idx = html.index("/static/img/brand/quadro-logo-white.png")
    # Открывающий <img захватим назад, до 200 символов до URL хватит
    img_open = html.rfind("<img", 0, img_idx)
    assert img_open != -1
    img_tag = html[img_open: html.find(">", img_idx) + 1]
    assert "kt-brand-logo" in img_tag, (
        f"<img>-тег логотипа должен содержать класс kt-brand-logo, "
        f"получен: {img_tag}"
    )
    # Старый размер h-8 убран с этого тега
    assert "h-8" not in img_tag, (
        "Старый класс h-8 должен быть убран — теперь используется kt-brand-logo"
    )
