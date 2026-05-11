"""Логотип бренда — этап 9А.2.1.

Проверяет:
  - в шаблоне сайдбара логотип подключён через .svg (векторный);
  - логотип и подпись «КОНФИГУРАТОР» обёрнуты в один контейнер
    .kt-brand-wrap с фиксированной шириной (одинаковая ширина
    логотипа и caption — claim заказчика);
  - SVG отдаётся фронту по адресу /static/img/brand/quadro-logo.svg
    с корректным content-type (image/svg+xml).
"""

from __future__ import annotations

from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_css() -> str:
    css = _project_root() / "static" / "dist" / "main.css"
    assert css.exists(), (
        f"Не найден собранный CSS: {css}. Запустите `npm run build:css`."
    )
    return css.read_text(encoding="utf-8")


def test_logo_uses_svg(manager_client):
    """В отрендеренной странице путь к логотипу — .svg, а не .png."""
    r = manager_client.get("/configurator/")
    assert r.status_code == 200
    html = r.text
    assert "/static/img/brand/quadro-logo.svg" in html
    # PNG-вариант больше не используется в основном макросе brand_mark.
    assert "/static/img/brand/quadro-logo-white.png" not in html


def test_logo_svg_file_present_on_disk():
    """Файл lежит в static/img/brand/quadro-logo.svg и непустой."""
    p = _project_root() / "static" / "img" / "brand" / "quadro-logo.svg"
    assert p.exists(), f"SVG-логотип не найден: {p}"
    assert p.stat().st_size > 1000, "SVG слишком маленький, явно битый"
    head = p.read_text(encoding="utf-8")[:600]
    assert "<svg" in head


def test_logo_svg_served_with_correct_mime(manager_client):
    """Сервер отдаёт SVG как image/svg+xml."""
    r = manager_client.get("/static/img/brand/quadro-logo.svg")
    assert r.status_code == 200
    ctype = r.headers.get("content-type", "")
    assert "svg" in ctype.lower(), (
        f"Ожидался content-type с 'svg', получен: {ctype}"
    )


def test_logo_and_caption_in_same_wrap(manager_client):
    """Логотип и caption обёрнуты в один контейнер .kt-brand-wrap —
    значит у них одинаковая ширина."""
    r = manager_client.get("/configurator/")
    assert r.status_code == 200
    html = r.text
    assert "kt-brand-wrap" in html
    # И в этой же обёртке должны быть оба элемента: img.kt-brand-logo
    # и div.brand-caption.
    wrap_idx = html.find('class="block group kt-brand-wrap"')
    if wrap_idx == -1:
        # допускаем перестановку классов
        wrap_idx = html.find("kt-brand-wrap")
    assert wrap_idx != -1
    # </a> закрывает обёртку
    end_idx = html.find("</a>", wrap_idx)
    assert end_idx != -1
    block = html[wrap_idx:end_idx]
    assert "kt-brand-logo" in block, "В обёртке должен быть логотип"
    assert "brand-caption" in block, "В обёртке должен быть caption"


def test_brand_wrap_has_fixed_width_in_css():
    """В собранном CSS у .kt-brand-wrap задана width в px — без этого
    логотип и caption не будут одинаковой ширины."""
    css = _read_css()
    assert ".kt-brand-wrap" in css, "Класс .kt-brand-wrap отсутствует в CSS"
    # Проверим, что в базовом блоке есть width: NNNpx.
    import re
    idx = css.find(".kt-brand-wrap")
    end = css.find("}", idx)
    block = css[idx:end]
    assert re.search(r"width\s*:\s*\d+px", block), (
        "У .kt-brand-wrap должен быть width: NNNpx"
    )


def test_brand_caption_uses_flex_distribution():
    """Caption использует display:flex со space-between — это даёт
    точное распределение букв по всей ширине обёртки."""
    css = _read_css()
    assert ".brand-caption" in css
    idx = css.find(".brand-caption")
    end = css.find("}", idx)
    block = css[idx:end]
    assert "flex" in block, ".brand-caption должен быть display:flex"
    assert "space-between" in block, (
        ".brand-caption должен использовать justify-content: space-between"
    )
