# Генератор коммерческого предложения (docx) — программная сборка с нуля.
#
# Этап 9А.2.7: документ строится программно через python-docx + lxml. От
# шаблона kp_template.docx остаются только реквизиты «ООО КВАДРО-ТЕХ»
# (логотип + текстовые строки) и параграф с подписью директора + печатью
# (inline-картинка). Всё, что между ними — дата, заголовок «Коммерческое
# предложение» и таблица позиций — генерируется заново.
#
# Зачем переписали: после серии итераций (8.6, 8.7, 8.8, 9А.2.5, 9А.2.6) с
# заменой внутренней таблицы остались артефакты Word-рендера в узких
# числовых колонках («ол-во» вместо «Кол-во», обрезание №-позиции, лишние
# пропуски перед текстом). Корень — Normal-стиль шаблона жёстко прибивал
# Times New Roman 14pt поверх Calibri-тем, и пересчёт ширин для зрелых
# чисел шёл «впритык». В этой ревизии Normal-стиль шаблона переведён на
# Calibri 11pt (sz=22), а каждый параграф/run в таблице получает явный
# rPr/rFonts с Calibri — никакого наследования.
#
# Структура документа (после build_kp_docx):
#   p[0..N]:  реквизиты (из шаблона, не трогаем)
#   p:        empty + горизонтальная линия (из шаблона)
#   p:        «№ б/н от DD.MM.YYYYг.» — программно
#   p:        spacer
#   p:        «Коммерческое предложение» (центр, bold, 14pt) — программно
#   p:        spacer
#   tbl:      таблица позиций со строкой ИТОГО — программно
#   p:        подпись директора + печать (inline-картинка из шаблона)
#   sectPr

from __future__ import annotations

import math
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import docx
from docx.shared import Cm
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


# Границы разумной наценки.
_MARKUP_MIN = 0
_MARKUP_MAX = 500


# --- Геометрия таблицы (в twips, 1/20 пункта; 1 см = 567 twips) -------------
#
# Поля страницы 2.0/2.0/2.0/1.5 (top/bottom/left/right, см) — текстовая зона
# на A4 = 210 - 2.0 - 1.5 = 16.5 см ≈ 9355 twips. Сумма колонок ниже даёт
# ровно 16.0 см (9072 twips), таблица помещается с запасом 0.5 см.

_COL_W_NUM   = 454    # № п/п                 — 0.8 см
_COL_W_NAME  = 3686   # Наименование          — 6.5 см
_COL_W_QTY   = 794    # Кол-во                — 1.4 см
_COL_W_PRICE = 2041   # Цена с НДС (руб.)     — 3.6 см
_COL_W_SUM   = 2098   # Сумма с НДС (руб.)    — 3.7 см

_GRID_WIDTHS = (_COL_W_NUM, _COL_W_NAME, _COL_W_QTY, _COL_W_PRICE, _COL_W_SUM)
_INNER_TBL_WIDTH = sum(_GRID_WIDTHS)  # 9073 twips ≈ 16 см

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
    """14500 → '14 500'. Между разрядами — non-breaking space (U+00A0),
    чтобы Word не рвал «37 338» на две строки в узких ячейках Цена/Сумма.
    «руб.» не добавляем — единица уже в заголовках колонок."""
    return f"{value:,}".replace(",", " ")


# ---------------------------------------------------------------------
# Поля страницы
# ---------------------------------------------------------------------

def _normalize_page_margins(doc) -> None:
    """Жёстко переписывает поля страницы во всех секциях документа.

    Исходный kp_template.docx имеет ассиметричные поля. Сбрасываем к
    симметричным 2.0 / 2.0 / 2.0 / 1.5 см (top/bottom/left/right). На A4
    (210×297мм) это даёт текстовую зону 165 мм — таблица 160 мм
    (9073 twips) укладывается с запасом."""
    for section in doc.sections:
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(2.0)
        section.right_margin = Cm(1.5)


# ---------------------------------------------------------------------
# Конструкторы XML-элементов (lxml)
# ---------------------------------------------------------------------

def _make_rPr(*, bold: bool = False, sz_half_pt: int = 22) -> etree._Element:
    """Calibri явно прописан на всех осях (ascii/hAnsi/cs/eastAsia) —
    перебивает любые наследования из стилей шаблона. sz_half_pt=22 = 11pt."""
    rPr = etree.Element(_w("rPr"))
    rFonts = etree.SubElement(rPr, _w("rFonts"))
    rFonts.set(_w("ascii"), "Calibri")
    rFonts.set(_w("hAnsi"), "Calibri")
    rFonts.set(_w("cs"), "Calibri")
    rFonts.set(_w("eastAsia"), "Calibri")
    if bold:
        etree.SubElement(rPr, _w("b"))
        etree.SubElement(rPr, _w("bCs"))
    sz = etree.SubElement(rPr, _w("sz"))
    sz.set(_w("val"), str(sz_half_pt))
    szCs = etree.SubElement(rPr, _w("szCs"))
    szCs.set(_w("val"), str(sz_half_pt))
    return rPr


def _make_pPr(
    *,
    jc: str | None = None,
    space_before: int = 0,
    space_after: int = 0,
    no_indent: bool = True,
) -> etree._Element:
    pPr = etree.Element(_w("pPr"))
    if no_indent:
        # Сбрасываем firstLine-отступ из Normal-стиля шаблона (ind:firstLine=720).
        ind = etree.SubElement(pPr, _w("ind"))
        ind.set(_w("firstLine"), "0")
    spacing = etree.SubElement(pPr, _w("spacing"))
    spacing.set(_w("before"), str(space_before))
    spacing.set(_w("after"), str(space_after))
    spacing.set(_w("line"), "240")
    spacing.set(_w("lineRule"), "auto")
    if jc:
        jc_el = etree.SubElement(pPr, _w("jc"))
        jc_el.set(_w("val"), jc)
    return pPr


def _make_paragraph(
    text: str,
    *,
    jc: str = "left",
    bold: bool = False,
    sz_half_pt: int = 22,
    space_before: int = 0,
    space_after: int = 0,
) -> etree._Element:
    p = etree.Element(_w("p"))
    p.append(_make_pPr(
        jc=jc, space_before=space_before, space_after=space_after,
    ))
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
        etree.SubElement(tcPr, _w("noWrap"))
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
    tblW.set(_w("w"), str(_INNER_TBL_WIDTH))
    tblW.set(_w("type"), "dxa")
    # Центрирование таблицы относительно текстовой зоны.
    tblJc = etree.SubElement(tblPr, _w("jc"))
    tblJc.set(_w("val"), "center")
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
    # Заголовки — 10pt (sz=20), чтобы «Кол-во», «Цена с НДС (руб.)»,
    # «Сумма с НДС (руб.)» помещались в одну строку.
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
        tr.append(_make_tc(
            str(i), width=_COL_W_NUM, jc="center", sz_half_pt=20,
        ))
        tr.append(_make_tc(
            drow["name"], width=_COL_W_NAME, jc="left", sz_half_pt=20,
        ))
        tr.append(_make_tc(
            str(drow["qty"]), width=_COL_W_QTY, jc="center", sz_half_pt=20,
        ))
        tr.append(_make_tc(
            _format_rub(drow["price_rub"]),
            width=_COL_W_PRICE, jc="right", no_wrap=True, sz_half_pt=20,
        ))
        tr.append(_make_tc(
            _format_rub(drow["total_rub"]),
            width=_COL_W_SUM, jc="right", no_wrap=True, sz_half_pt=20,
        ))

    # ── Строка ИТОГО ───────────────────────────────────────────────────
    # 4 первые ячейки объединены через gridSpan=4, в первой — текст
    # «ИТОГО» по правому краю; пятая ячейка — сумма по правому краю.
    itogo = etree.SubElement(tbl, _w("tr"))
    trPr_t = etree.SubElement(itogo, _w("trPr"))
    trHeight = etree.SubElement(trPr_t, _w("trHeight"))
    trHeight.set(_w("val"), str(_TOTAL_ROW_MIN_HEIGHT))
    trHeight.set(_w("hRule"), "atLeast")
    etree.SubElement(trPr_t, _w("cantSplit"))
    itogo_label_w = _COL_W_NUM + _COL_W_NAME + _COL_W_QTY + _COL_W_PRICE
    itogo.append(_make_tc(
        "ИТОГО", width=itogo_label_w, grid_span=4, fill="F8F8F8",
        jc="right", bold=True, no_wrap=True,
    ))
    itogo.append(_make_tc(
        _format_rub(total_rub),
        width=_COL_W_SUM, fill="F8F8F8", jc="right", bold=True, no_wrap=True,
    ))

    return tbl


# ---------------------------------------------------------------------
# Программная вставка содержимого в шаблон
# ---------------------------------------------------------------------

def _find_signature_paragraph(body) -> etree._Element:
    """Параграф с inline-картинкой подписи + печати — нижний якорь.

    В шаблоне это единственный параграф body, содержащий
    <w:drawing>/<wp:inline> (логотип сверху — anchor, поэтому отсеивается)."""
    WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    for ch in body:
        if ch.tag != _w("p"):
            continue
        if ch.find(f".//{{{WP}}}inline") is not None:
            return ch
    raise RuntimeError("Параграф с подписью директора не найден в шаблоне.")


def _find_top_anchor(body) -> etree._Element:
    """Параграф «www.quadro.tatar» — нижняя строка реквизитов, верхний якорь.

    Содержимое СТРОГО ПОСЛЕ него и до signature-параграфа удаляется и
    замещается программным контентом."""
    for ch in body:
        if ch.tag != _w("p"):
            continue
        text = "".join((t.text or "") for t in ch.iter(_w("t")))
        if "quadro.tatar" in text.lower():
            return ch
    raise RuntimeError("Параграф 'www.quadro.tatar' не найден в шаблоне.")


def _build_kp_body(doc, data_rows: list[dict], total_rub: int, today_str: str) -> None:
    """Стирает всё между www.quadro.tatar и подписью, вставляет
    программно собранные параграфы и таблицу."""
    body = doc.element.body
    top_anchor = _find_top_anchor(body)
    signature_p = _find_signature_paragraph(body)

    children = list(body)
    top_idx = children.index(top_anchor)
    sig_idx = children.index(signature_p)
    if sig_idx <= top_idx:
        raise RuntimeError("Подпись расположена выше якоря реквизитов — шаблон сломан.")

    # Удаляем всё строго между top_anchor и signature_p.
    for ch in children[top_idx + 1: sig_idx]:
        body.remove(ch)

    # Программно собираем элементы для вставки.
    elems: list[etree._Element] = []
    # Пустой параграф-разделитель после реквизитов.
    elems.append(_make_paragraph("", jc="left"))
    # Дата.
    date_p = etree.Element(_w("p"))
    date_p.append(_make_pPr(jc="left", space_before=0, space_after=120))
    r = etree.SubElement(date_p, _w("r"))
    r.append(_make_rPr(bold=True, sz_half_pt=22))
    t = etree.SubElement(r, _w("t"))
    t.text = f"№ б/н от {today_str}г."
    t.set(f"{{{_XML}}}space", "preserve")
    elems.append(date_p)
    # Заголовок.
    elems.append(_make_paragraph(
        "Коммерческое предложение",
        jc="center", bold=True, sz_half_pt=28,
        space_before=240, space_after=240,
    ))
    # Spacer перед таблицей.
    elems.append(_make_paragraph("", jc="left"))
    # Таблица.
    elems.append(_make_inner_tbl(data_rows, total_rub))
    # Spacer после таблицы (перед подписью).
    elems.append(_make_paragraph("", jc="left"))

    # Вставляем перед signature_p.
    sig_p_el = signature_p  # ещё в дереве, на новом индексе
    for el in elems:
        sig_p_el.addprevious(el)


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
    _normalize_page_margins(doc)
    _build_kp_body(
        doc,
        data_rows=data_rows,
        total_rub=grand_total,
        today_str=date.today().strftime("%d.%m.%Y"),
    )

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
