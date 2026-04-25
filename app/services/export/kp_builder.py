# Генератор коммерческого предложения (docx) на основе шаблона KP.
#
# Этап 8.6: внутренняя таблица КП теперь строится с нуля программно через
# lxml. Шаблон kp_template.docx сохраняет верхнюю часть (реквизиты,
# изображения с подписью директора и печатью) и внешнюю таблицу-обложку.
# Внутренняя таблица из шаблона полностью заменяется на свежую с
# гарантированной структурой:
#   - 5 колонок: № п/п, Наименование, Кол-во, Цена с НДС (руб.),
#     Сумма с НДС (руб.).
#   - Стиль: тонкие чёрные границы со всех сторон (TableGrid).
#   - Шапка таблицы: bold, серая заливка #E0E0E0.
#   - Данные: имя — слева, числа — по правому краю.
#   - Финальная строка ИТОГО: 4 первые ячейки объединены через gridSpan,
#     текст «ИТОГО» bold по правому краю; 5-я ячейка — сумма bold.
#
# Замена шаблонной таблицы лечит баг 8.4, когда после клонирования
# tcPr и наличия «лишних» gridCol справа значение ИТОГО оказывалось в
# ячейке-«хвосте» с шириной 3545 twips (далеко за пределами видимой
# колонки «Сумма с НДС» шириной 1417), и пользователю казалось, что
# поле пустое.

from __future__ import annotations

import math
import re
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import docx
from lxml import etree
from sqlalchemy.orm import Session

from app.services import spec_service
from app.services.export import exchange_rate


_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[3]
    / "app" / "templates" / "export" / "kp_template.docx"
)


# --- XML namespaces ----------------------------------------------------------

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_XML = "http://www.w3.org/XML/1998/namespace"

def _w(tag: str) -> str:
    return f"{{{_W}}}{tag}"


# Регексп для поиска даты вида 22.04.2026 в параграфе «№ б/н от … г.».
_DATE_RE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{4}")

# Границы разумной наценки.
_MARKUP_MIN = 0
_MARKUP_MAX = 500


# --- Геометрия таблицы (в twips, 1/20 пункта; 1 см = 567 twips) -------------
#
# Ширины подобраны под доступную ширину A4 при полях 25 мм слева и справа:
# 210 - 25*2 = 160 мм = 16 см ≈ 9072 twips. Сумма колонок ниже даёт ровно
# эту ширину, чтобы Word не запускал auto-redistribute (даже при включённом
# tblLayout=fixed Word может «поджать» колонки, если их сумма больше
# доступной ширины страницы).
#
# Этап 8.8: «Кол-во» расширили с 1.2 → 1.4 см, потому что при 10pt в 1.2 см
# заголовок «Кол-во» обрезался в Word до «ол-во». Соответственно «Наименование»
# сократили с 8.5 → 8.3 см, чтобы суммарная ширина не уехала за пределы
# полезной ширины страницы.

_COL_W_NUM   = 454    # № п/п                 — 0.8 см
_COL_W_NAME  = 4706   # Наименование          — 8.3 см
_COL_W_QTY   = 794    # Кол-во                — 1.4 см
_COL_W_PRICE = 1531   # Цена с НДС (руб.)     — 2.7 см
_COL_W_SUM   = 1588   # Сумма с НДС (руб.)    — 2.8 см

_GRID_WIDTHS = (_COL_W_NUM, _COL_W_NAME, _COL_W_QTY, _COL_W_PRICE, _COL_W_SUM)
_INNER_TBL_WIDTH = sum(_GRID_WIDTHS)  # 9073 twips

# Высота строки ИТОГО (в twips). 0.7 см ≈ 397 twips — чтобы строка
# выглядела заметнее обычных data-строк.
_TOTAL_ROW_MIN_HEIGHT = 397


# ---------------------------------------------------------------------
# Арифметика цен
# ---------------------------------------------------------------------

def _ceil_rub(value: Decimal) -> int:
    """math.ceil, но гарантированно из Decimal — чтобы не плавал float."""
    return math.ceil(value)


def _compute_prices(
    unit_usd: float | Decimal,
    rate: Decimal,
    markup_percent: int,
    qty: int,
) -> tuple[int, int, int]:
    """Возвращает (base_rub_per_unit, sell_rub_per_unit, line_total)."""
    unit_usd_dec = Decimal(str(unit_usd))
    base = _ceil_rub(unit_usd_dec * rate)
    multiplier = Decimal(100 + markup_percent) / Decimal(100)
    sell = _ceil_rub(Decimal(base) * multiplier)
    total = _ceil_rub(Decimal(sell) * qty)
    return base, sell, total


def _format_rub(value: int) -> str:
    """14500 → '14 500'. Пробел как разделитель тысяч, без копеек.

    «руб.» не добавляем — единица уже в заголовках колонок.
    """
    return f"{value:,}".replace(",", " ")


# ---------------------------------------------------------------------
# Замена даты в верхней шапке КП
# ---------------------------------------------------------------------

def _replace_date_in_header(doc, new_date: str) -> None:
    """Меняет дату в параграфе «№ б/н от DD.MM.YYYYг.» на переданную."""
    for p in doc.paragraphs:
        if "№" not in p.text or not _DATE_RE.search(p.text):
            continue
        joined = "".join(r.text for r in p.runs)
        replaced = _DATE_RE.sub(new_date, joined)
        if replaced == joined:
            return
        runs = p.runs
        runs[0].text = replaced
        for r in runs[1:]:
            r._element.getparent().remove(r._element)
        return


# ---------------------------------------------------------------------
# Сборка таблицы из XML (lxml)
# ---------------------------------------------------------------------

def _make_pPr(*, jc: str | None = None) -> etree._Element:
    pPr = etree.Element(_w("pPr"))
    # Без spacing — параграф ляжет компактно в ячейку.
    spacing = etree.SubElement(pPr, _w("spacing"))
    spacing.set(_w("before"), "0")
    spacing.set(_w("after"), "0")
    spacing.set(_w("line"), "240")
    spacing.set(_w("lineRule"), "auto")
    if jc:
        jc_el = etree.SubElement(pPr, _w("jc"))
        jc_el.set(_w("val"), jc)
    return pPr


def _make_rPr(*, bold: bool = False, sz_half_pt: int = 22) -> etree._Element:
    rPr = etree.Element(_w("rPr"))
    # Times New Roman / 11pt — стандарт документа КП.
    rFonts = etree.SubElement(rPr, _w("rFonts"))
    rFonts.set(_w("ascii"), "Times New Roman")
    rFonts.set(_w("hAnsi"), "Times New Roman")
    rFonts.set(_w("cs"), "Times New Roman")
    if bold:
        etree.SubElement(rPr, _w("b"))
        etree.SubElement(rPr, _w("bCs"))
    sz = etree.SubElement(rPr, _w("sz"))
    sz.set(_w("val"), str(sz_half_pt))
    szCs = etree.SubElement(rPr, _w("szCs"))
    szCs.set(_w("val"), str(sz_half_pt))
    return rPr


def _make_paragraph(
    text: str,
    *,
    jc: str = "left",
    bold: bool = False,
    sz_half_pt: int = 22,
) -> etree._Element:
    p = etree.Element(_w("p"))
    p.append(_make_pPr(jc=jc))
    r = etree.SubElement(p, _w("r"))
    r.append(_make_rPr(bold=bold, sz_half_pt=sz_half_pt))
    t = etree.SubElement(r, _w("t"))
    t.text = text
    t.set(f"{{{_XML}}}space", "preserve")
    return p


def _make_tcPr(
    *,
    width: int,
    grid_span: int = 1,
    fill: str | None = None,
    no_wrap: bool = False,
) -> etree._Element:
    tcPr = etree.Element(_w("tcPr"))
    tcW = etree.SubElement(tcPr, _w("tcW"))
    tcW.set(_w("w"), str(width))
    tcW.set(_w("type"), "dxa")
    if grid_span > 1:
        gs = etree.SubElement(tcPr, _w("gridSpan"))
        gs.set(_w("val"), str(grid_span))
    if fill:
        shd = etree.SubElement(tcPr, _w("shd"))
        shd.set(_w("val"), "clear")
        shd.set(_w("color"), "auto")
        shd.set(_w("fill"), fill)
    if no_wrap:
        # noWrap: запрет переноса текста внутри ячейки. Word всё равно
        # уважает заданную ширину tcW, но если строка не помещается — он
        # её усечёт, а не разобьёт на две. Применяем для заголовков и
        # числовых ячеек, где перенос «42 / 064» выглядит хуже сжатого
        # шрифта.
        etree.SubElement(tcPr, _w("noWrap"))
    # Этап 8.8: вертикальное выравнивание по центру для всех ячеек таблицы
    # (по запросу пользователя — числа в data-строках смотрелись прибитыми
    # к верху и не совпадали по высоте с шапкой).
    vAlign = etree.SubElement(tcPr, _w("vAlign"))
    vAlign.set(_w("val"), "center")
    return tcPr


def _make_tc(
    text: str,
    *,
    width: int,
    grid_span: int = 1,
    fill: str | None = None,
    jc: str = "left",
    bold: bool = False,
    no_wrap: bool = False,
    sz_half_pt: int = 22,
) -> etree._Element:
    tc = etree.Element(_w("tc"))
    tc.append(_make_tcPr(
        width=width, grid_span=grid_span, fill=fill, no_wrap=no_wrap,
    ))
    tc.append(_make_paragraph(text, jc=jc, bold=bold, sz_half_pt=sz_half_pt))
    return tc


def _make_tbl_borders() -> etree._Element:
    """Тонкие чёрные границы со всех сторон, включая внутренние."""
    borders = etree.Element(_w("tblBorders"))
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        b = etree.SubElement(borders, _w(side))
        b.set(_w("val"), "single")
        b.set(_w("sz"), "4")          # 0.5 pt
        b.set(_w("space"), "0")
        b.set(_w("color"), "000000")
    return borders


def _make_inner_tbl(
    rows_data: list[dict],
    total_rub: int,
) -> etree._Element:
    """Собирает <w:tbl> с шапкой, строками данных и ИТОГО."""
    tbl = etree.Element(_w("tbl"))

    # tblPr
    tblPr = etree.SubElement(tbl, _w("tblPr"))
    tblW = etree.SubElement(tblPr, _w("tblW"))
    tblW.set(_w("w"), str(sum(_GRID_WIDTHS)))
    tblW.set(_w("type"), "dxa")
    tblPr.append(_make_tbl_borders())
    layout = etree.SubElement(tblPr, _w("tblLayout"))
    layout.set(_w("type"), "fixed")
    look = etree.SubElement(tblPr, _w("tblLook"))
    look.set(_w("val"), "04A0")

    # tblGrid
    grid = etree.SubElement(tbl, _w("tblGrid"))
    for w in _GRID_WIDTHS:
        gc = etree.SubElement(grid, _w("gridCol"))
        gc.set(_w("w"), str(w))

    # ── Шапка ──────────────────────────────────────────────────────────
    # Заголовки — 10pt (sz=20) и noWrap, чтобы «Кол-во», «Цена с НДС
    # (руб.)», «Сумма с НДС (руб.)» помещались в одну строку и не
    # рвались на «ол-во» / «Цен/а с НДС/(руб.)».
    header = etree.SubElement(tbl, _w("tr"))
    trPr_h = etree.SubElement(header, _w("trPr"))
    etree.SubElement(trPr_h, _w("cantSplit"))
    th_titles = ("№ п/п", "Наименование", "Кол-во",
                 "Цена с НДС (руб.)", "Сумма с НДС (руб.)")
    for title, w in zip(th_titles, _GRID_WIDTHS):
        header.append(_make_tc(
            title, width=w, fill="E0E0E0", jc="center", bold=True,
            no_wrap=True, sz_half_pt=20,
        ))

    # ── Строки данных ──────────────────────────────────────────────────
    # noWrap для числовых колонок (Цена/Сумма) — числа вида «1 234 567»
    # не должны рваться на две строки.
    for i, drow in enumerate(rows_data, start=1):
        tr = etree.SubElement(tbl, _w("tr"))
        tr.append(_make_tc(str(i), width=_COL_W_NUM, jc="center"))
        tr.append(_make_tc(drow["name"], width=_COL_W_NAME, jc="left"))
        tr.append(_make_tc(str(drow["qty"]), width=_COL_W_QTY, jc="center"))
        tr.append(_make_tc(
            _format_rub(drow["price_rub"]),
            width=_COL_W_PRICE, jc="right", no_wrap=True,
        ))
        tr.append(_make_tc(
            _format_rub(drow["total_rub"]),
            width=_COL_W_SUM, jc="right", no_wrap=True,
        ))

    # ── Строка ИТОГО ───────────────────────────────────────────────────
    # Минимальная высота 0.7 см — чтобы строка визуально выделялась.
    itogo = etree.SubElement(tbl, _w("tr"))
    trPr_t = etree.SubElement(itogo, _w("trPr"))
    trHeight = etree.SubElement(trPr_t, _w("trHeight"))
    trHeight.set(_w("val"), str(_TOTAL_ROW_MIN_HEIGHT))
    trHeight.set(_w("hRule"), "atLeast")
    etree.SubElement(trPr_t, _w("cantSplit"))
    itogo_label_w = _COL_W_NUM + _COL_W_NAME + _COL_W_QTY + _COL_W_PRICE
    itogo.append(_make_tc(
        "ИТОГО", width=itogo_label_w, grid_span=4, jc="right", bold=True,
        no_wrap=True,
    ))
    itogo.append(_make_tc(
        _format_rub(total_rub),
        width=_COL_W_SUM, jc="right", bold=True, no_wrap=True,
    ))

    return tbl


def _normalize_outer_table(outer_tbl: etree._Element) -> None:
    """Чистит обёрточную таблицу шаблона: убирает паразитные gridCol-ы,
    отрицательный tblInd и нормализует tblW/tcW под фактическую ширину
    внутренней КП-таблицы.

    Этап 8.8: исходный kp_template.docx содержит внешнюю таблицу 1×2 с
    gridCol [10490, 284], tblW=10774 и tblInd=-743. Сумма gridCol уезжает
    за полезную ширину A4 (9072 twips), а отрицательный отступ слева
    смещает таблицу за поля. На печати Word при этом дезорганизует
    раскладку внутренней таблицы: заголовок «Кол-во» обрезается до
    «ол-во», цифра quantity рендерится как «0», числа Цена/Сумма
    переносятся на вторую строку. После нормализации внешняя таблица
    становится одноколоночной шириной ровно как внутренняя — Word больше
    не пытается «разъехаться».
    """
    inner_w = _INNER_TBL_WIDTH

    # tblPr: убираем tblInd, фиксируем tblW.
    tblPr = outer_tbl.find(_w("tblPr"))
    if tblPr is not None:
        tblInd = tblPr.find(_w("tblInd"))
        if tblInd is not None:
            tblPr.remove(tblInd)
        tblW = tblPr.find(_w("tblW"))
        if tblW is None:
            tblW = etree.SubElement(tblPr, _w("tblW"))
        tblW.set(_w("w"), str(inner_w))
        tblW.set(_w("type"), "dxa")

    # tblGrid: ровно один gridCol во всю ширину.
    grid = outer_tbl.find(_w("tblGrid"))
    if grid is not None:
        for gc in list(grid.findall(_w("gridCol"))):
            grid.remove(gc)
        gc = etree.SubElement(grid, _w("gridCol"))
        gc.set(_w("w"), str(inner_w))

    # В каждой строке оставляем только первую ячейку, контент дополнительных
    # переносим в первую (на случай если там вдруг что-то лежит). Tcw
    # первой ячейки — на полную ширину; gridSpan убираем.
    for tr in outer_tbl.findall(_w("tr")):
        tcs = tr.findall(_w("tc"))
        if not tcs:
            continue
        first = tcs[0]
        for extra in tcs[1:]:
            for child in list(extra):
                if child.tag == _w("tcPr"):
                    continue
                first.append(child)
            tr.remove(extra)
        first_tcPr = first.find(_w("tcPr"))
        if first_tcPr is not None:
            tcW = first_tcPr.find(_w("tcW"))
            if tcW is None:
                tcW = etree.SubElement(first_tcPr, _w("tcW"))
            tcW.set(_w("w"), str(inner_w))
            tcW.set(_w("type"), "dxa")
            gs = first_tcPr.find(_w("gridSpan"))
            if gs is not None:
                first_tcPr.remove(gs)


def _replace_inner_table(doc, new_tbl: etree._Element) -> None:
    """Находит шаблонную внутреннюю таблицу и заменяет её на new_tbl,
    после чего нормализует обёрточную таблицу.

    Шаблон: внешняя таблица 1×2, внутренняя — в первой ячейке внешней.
    """
    if not doc.tables:
        raise RuntimeError("В шаблоне KP нет ни одной таблицы.")
    outer = doc.tables[0]
    if not outer.rows:
        raise RuntimeError("Внешняя таблица KP пустая.")
    cell0 = outer.rows[0].cells[0]
    if not cell0.tables:
        raise RuntimeError("Внутренняя таблица KP не найдена.")
    old_tbl = cell0.tables[0]._tbl
    parent = old_tbl.getparent()
    parent.replace(old_tbl, new_tbl)
    _normalize_outer_table(outer._tbl)


# ---------------------------------------------------------------------
# Публичная функция
# ---------------------------------------------------------------------

def build_kp_docx(
    project_id: int,
    markup_percent: int,
    db: Session,
) -> bytes:
    """Собирает docx коммерческого предложения по проекту."""
    if not isinstance(markup_percent, int) or isinstance(markup_percent, bool):
        raise ValueError(
            "Наценка должна быть целым числом процентов (0..500)."
        )
    if markup_percent < _MARKUP_MIN or markup_percent > _MARKUP_MAX:
        raise ValueError(
            f"Наценка {markup_percent}% вне допустимого диапазона "
            f"{_MARKUP_MIN}..{_MARKUP_MAX}."
        )

    rate, _rate_date, _source = exchange_rate.get_usd_rate()
    spec_items = spec_service.list_spec_items(db, project_id=project_id)

    data_rows: list[dict] = []
    grand_total = 0
    for item in spec_items:
        qty = int(item.get("quantity") or 1)
        unit_usd = item.get("unit_usd") or 0.0
        _base, sell, line_total = _compute_prices(
            unit_usd=unit_usd,
            rate=rate,
            markup_percent=markup_percent,
            qty=qty,
        )
        grand_total += line_total
        data_rows.append({
            "name":      item.get("display_name") or item.get("auto_name") or "Конфигурация",
            "price_rub": sell,
            "qty":       qty,
            "total_rub": line_total,
        })

    doc = docx.Document(str(_TEMPLATE_PATH))
    _replace_date_in_header(doc, date.today().strftime("%d.%m.%Y"))

    new_tbl = _make_inner_tbl(data_rows, grand_total)
    _replace_inner_table(doc, new_tbl)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
