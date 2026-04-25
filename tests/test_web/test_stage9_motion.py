# Тесты Этапа 9А.1.2: микроанимации в дизайн-системе.
#
# Проверяем наличие ключевых CSS-правил в собранном static/dist/main.css —
# transitions/animations на кнопках и карточках, медиа-запрос
# prefers-reduced-motion, выезд rail-полосы у активного пункта сайдбара.

from __future__ import annotations

from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_css() -> str:
    css = _project_root() / "static" / "dist" / "main.css"
    assert css.exists(), (
        f"Не найден собранный CSS: {css}. "
        "Запустите `npm run build:css` перед коммитом."
    )
    return css.read_text(encoding="utf-8")


def test_btn_has_transition():
    """У базового класса .btn определена transition-property —
    значит при hover/active на кнопках работают плавные переходы.

    Tailwind при минификации может разделить @apply-часть и custom
    CSS на два блока с одинаковым селектором. Поэтому ищем любой
    блок `.btn{...}`, в котором есть `transition:`."""
    css = _read_css()
    import re
    blocks = re.findall(r"\.btn\{([^}]*)\}", css)
    assert blocks, "Класс .btn не найден в собранном CSS"
    has_transition = any("transition:" in b for b in blocks)
    has_transform = any("transform" in b for b in blocks) or \
                    any("transform" in b for b in re.findall(r"\.btn[^a-z-][^{]*\{([^}]*)\}", css))
    assert has_transition, (
        "Хотя бы один блок .btn должен содержать transition: "
        "(микроанимации 9А.1.2)"
    )
    # Минимум — переходы по background-color и transform упомянуты
    # в селекторах .btn / .btn:hover / .btn-secondary:hover.
    assert "translateY(-1px)" in css or "translateY(-2px)" in css, (
        "Должны быть translateY-эффекты у кнопок и/или карточек"
    )


def test_btn_secondary_hover_translate():
    """У .btn-secondary при hover есть translateY(-1px) — это
    «приподнимание», ключевой эффект 9А.1.2."""
    css = _read_css()
    # Tailwind минифицирует, hover-блок может быть либо .btn-secondary:hover{...}
    # либо вынесен; ищем подстроку с translateY(-1px) рядом с btn-secondary.
    assert ".btn-secondary:hover" in css
    hover_idx = css.find(".btn-secondary:hover")
    block = css[hover_idx: hover_idx + 400]
    assert "translateY(-1px)" in block, (
        "При hover на .btn-secondary ожидается transform: translateY(-1px)"
    )


def test_card_hover_classes():
    """У .card есть hover-стили — translateY и glow-soft."""
    css = _read_css()
    assert ".card:hover" in css
    hover_idx = css.find(".card:hover")
    block = css[hover_idx: hover_idx + 400]
    # «Выезд» вверх
    assert "translateY(-2px)" in block, (
        "При hover на .card ожидается transform: translateY(-2px)"
    )


def test_card_active_pulse_animation():
    """У .card-active определена пульсация glow-brand —
    keyframes kt-card-pulse + animation на классе."""
    css = _read_css()
    assert "kt-card-pulse" in css, "Ожидаются keyframes kt-card-pulse"
    # И сам класс ссылается на анимацию.
    active_idx = css.find(".card-active{")
    assert active_idx != -1
    block_end = css.find("}", active_idx)
    block = css[active_idx:block_end]
    assert "animation:" in block and "kt-card-pulse" in block


def test_nav_rail_animation():
    """У активного пункта сайдбара полоса слева выезжает сверху вниз —
    keyframes kt-nav-rail-in + animation property с длительностью.
    Tailwind/PostCSS минифицируют ::before в :before, обе формы валидны."""
    css = _read_css()
    assert "kt-nav-rail-in" in css, "Ожидаются keyframes kt-nav-rail-in"
    # На псевдоэлементе .nav-item-active::before/:before ожидаем animation
    # с длительностью 250ms.
    rail_idx = css.find(".nav-item-active::before")
    if rail_idx == -1:
        rail_idx = css.find(".nav-item-active:before")
    assert rail_idx != -1, (
        "Не найден псевдоэлемент .nav-item-active::before/:before"
    )
    block_end = css.find("}", rail_idx)
    block = css[rail_idx:block_end]
    assert "animation:" in block
    # Минификатор может писать `.25s` вместо `250ms`.
    assert ("250ms" in block) or (".25s" in block), (
        "Ожидается длительность 250мс у выезда rail-полосы"
    )


def test_nav_item_icon_hover_translate():
    """При hover на .nav-item иконка слева смещается на 2px вправо —
    намёк «кликни сюда»."""
    css = _read_css()
    # Tailwind может скомпилировать селектор как .nav-item:hover .nav-item-icon
    assert ".nav-item:hover .nav-item-icon" in css
    idx = css.find(".nav-item:hover .nav-item-icon")
    block = css[idx: idx + 400]
    assert "translateX(2px)" in block, (
        "Ожидается transform: translateX(2px) у иконки при hover на nav-item"
    )


def test_modal_open_animations():
    """У .modal-overlay и .modal-container есть animation на появление,
    причём контейнер использует «spring out» easing."""
    css = _read_css()
    assert "kt-modal-overlay-in" in css
    assert "kt-modal-container-in" in css
    # Контейнер — cubic-bezier(0.16, 1, 0.3, 1) (Apple HIG / Linear / Vercel).
    container_idx = css.find(".modal-container{")
    assert container_idx != -1
    block_end = css.find("}", container_idx)
    block = css[container_idx:block_end]
    assert "cubic-bezier(.16,1,.3,1)" in block.replace(" ", "") or \
           "cubic-bezier(0.16,1,0.3,1)" in block.replace(" ", ""), (
        "Ожидается easing cubic-bezier(0.16, 1, 0.3, 1) у .modal-container"
    )


def test_reduced_motion_respected():
    """В собранном CSS присутствует медиа-запрос prefers-reduced-motion
    с обнулением transition-duration и animation-duration — для людей
    с настройкой «уменьшить движение» в системе."""
    css = _read_css()
    assert "prefers-reduced-motion:reduce" in css.replace(" ", "") or \
           "prefers-reduced-motion: reduce" in css, (
        "Ожидается @media (prefers-reduced-motion: reduce) в собранном CSS"
    )
    # Внутри блока — обнуление длительностей.
    rm_idx = css.find("prefers-reduced-motion")
    rm_block = css[rm_idx: rm_idx + 1500]
    assert "transition-duration:.01ms" in rm_block.replace(" ", "") or \
           "transition-duration: 0.01ms" in rm_block, (
        "Ожидается transition-duration: 0.01ms внутри prefers-reduced-motion"
    )
    assert "animation-duration:.01ms" in rm_block.replace(" ", "") or \
           "animation-duration: 0.01ms" in rm_block, (
        "Ожидается animation-duration: 0.01ms внутри prefers-reduced-motion"
    )


def test_num_flip_animation_present():
    """Класс .num-flip и keyframes kt-num-flip существуют —
    используется JS-кодом project.js для подсветки изменившихся цифр
    в спецификации (animated numbers по ТЗ)."""
    css = _read_css()
    assert ".num-flip" in css
    assert "kt-num-flip" in css


def test_project_js_uses_num_flip():
    """project.js помечает изменившиеся ячейки спецификации классом
    .num-flip — иначе анимации не будут срабатывать."""
    js = (_project_root() / "static" / "js" / "project.js").read_text(
        encoding="utf-8"
    )
    assert "num-flip" in js, (
        "static/js/project.js должен использовать класс num-flip "
        "для animated numbers"
    )
    # Должна быть хелпер-функция flip и инициализация lastSpec.
    assert "function flip(" in js
    assert "lastSpec" in js
