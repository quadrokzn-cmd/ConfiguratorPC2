# Генератор коммерческого предложения (docx) по шаблону KP (этап 8.2).
#
# Берёт app/templates/export/kp_template.docx как основу, сохраняет её
# структуру (шапку с реквизитами, внешнюю таблицу-обложку «Коммерческое
# предложение», подпись директора и печать), а заполняет только:
#   - дату в параграфе «№ б/н от DD.MM.YYYYг.» (ставится сегодняшняя);
#   - строки внутренней таблицы: №, наименование, цена за шт., количество,
#     сумма (по одной строке на каждую конфигурацию проекта);
#   - поле ИТОГО в последней строке таблицы.
#
# Цены считаются из unit_usd конфигурации (снимок закупочной цены в $,
# сохранённый в specification_items при выборе варианта — тот же слой,
# что использует excel_builder) × курс ЦБ × (1 + наценка / 100). На каждом
# шаге применяется math.ceil, чтобы в итоговом документе не было копеек.

from __future__ import annotations

import copy
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
    """Возвращает (base_rub_per_unit, sell_rub_per_unit, line_total).

    Все промежуточные вычисления — в Decimal, округление math.ceil до
    целого рубля на каждом шаге.
    """
    unit_usd_dec = Decimal(str(unit_usd))
    base = _ceil_rub(unit_usd_dec * rate)
    multiplier = Decimal(100 + markup_percent) / Decimal(100)
    sell = _ceil_rub(Decimal(base) * multiplier)
    total = _ceil_rub(Decimal(sell) * qty)
    return base, sell, total


def _format_rub(value: int) -> str:
    """14500 → '14 500'. Пробел как разделитель тысяч, без копеек.

    «руб.» больше не добавляем — после живой проверки этапа 8.4
    менеджер попросил убрать дублирующую единицу измерения: она
    уже есть в заголовках колонок «Цена c НДС (руб.)» / «Сумма с НДС (руб.)».
    """
    return f"{value:,}".replace(",", " ")


# ---------------------------------------------------------------------
# Манипуляции с XML шаблона
# ---------------------------------------------------------------------

def _replace_date_in_header(doc, new_date: str) -> None:
    """Меняет дату в параграфе «№ б/н от DD.MM.YYYYг.» на переданную.

    Поиск: первый параграф документа, содержащий «№» и дату вида
    DD.MM.YYYY. Текст может быть разбит на несколько runs — собираем
    полный текст, заменяем дату регэкспом, кладём результат в первый
    run, удаляем остальные.
    """
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


def _find_inner_kp_table(doc) -> "docx.table.Table":
    """Находит внутреннюю таблицу в обложке «Коммерческое предложение»."""
    if not doc.tables:
        raise RuntimeError("В шаблоне KP нет ни одной таблицы.")
    outer = doc.tables[0]
    if not outer.rows:
        raise RuntimeError("Внешняя таблица KP пустая.")
    cell0 = outer.rows[0].cells[0]
    if not cell0.tables:
        raise RuntimeError("Внутренняя таблица KP не найдена.")
    return cell0.tables[0]


def _tcs_of_row(tr: etree._Element) -> list[etree._Element]:
    """Возвращает все <w:tc> элементы строки."""
    return tr.findall(_w("tc"))


def _set_tc_text(tc: etree._Element, text: str, rpr_template: etree._Element | None) -> None:
    """Ставит в ячейке ровно один параграф с одним run, в котором лежит text.

    Сохраняет первый <w:p> ячейки (его pPr — выравнивание/отступы),
    удаляет любые другие параграфы. В первый параграф кладёт одиночный
    <w:r>, опционально с копией rpr_template (чтобы новые ячейки
    получили единый шрифт — иначе брали бы default документа).
    """
    # Оставляем только первый параграф.
    ps = tc.findall(_w("p"))
    if ps:
        p = ps[0]
        for extra in ps[1:]:
            tc.remove(extra)
    else:
        p = etree.SubElement(tc, _w("p"))

    # Удаляем все существующие runs.
    for r in p.findall(_w("r")):
        p.remove(r)

    # Создаём один новый run.
    r = etree.SubElement(p, _w("r"))
    if rpr_template is not None:
        r.append(copy.deepcopy(rpr_template))

    t = etree.SubElement(r, _w("t"))
    t.text = text
    t.set(f"{{{_XML}}}space", "preserve")


def _extract_data_row_rpr(template_tr: etree._Element) -> etree._Element | None:
    """Берёт rPr из первого run первой ячейки шаблонной строки — оттуда
    «наследуют» форматирование все ячейки сгенерированной строки."""
    tcs = _tcs_of_row(template_tr)
    if not tcs:
        return None
    first_run = tcs[0].find(f".//{_w('r')}")
    if first_run is None:
        return None
    rpr = first_run.find(_w("rPr"))
    return rpr  # может быть None — тогда просто без rPr


def _fill_data_row_tr(
    tr: etree._Element,
    *,
    pos_num: int,
    name: str,
    price_rub: int,
    qty: int,
    total_rub: int,
    rpr_template: etree._Element | None,
) -> None:
    """Заливает в склонированную строку данные.

    Новый порядок (этап 8.4): №, Наименование, Кол-во, Цена c НДС, Сумма.
    """
    tcs = _tcs_of_row(tr)
    if len(tcs) < 5:
        raise RuntimeError(
            f"Шаблон строки данных KP ожидает 5 ячеек, получено {len(tcs)}."
        )
    _set_tc_text(tcs[0], str(pos_num), rpr_template)
    _set_tc_text(tcs[1], name, rpr_template)
    _set_tc_text(tcs[2], str(qty), rpr_template)
    _set_tc_text(tcs[3], _format_rub(price_rub), rpr_template)
    _set_tc_text(tcs[4], _format_rub(total_rub), rpr_template)


def _update_header_row(header_tr: etree._Element) -> None:
    """Перестраивает заголовочную строку под новый порядок колонок.

    Было: №, Наименование, Цена c НДС (руб.), Кол-во, Сумма с НДС (руб.).
    Надо: №, Наименование, Кол-во, Цена c НДС (руб.), Сумма с НДС (руб.).

    Меняем только текст в tcs[2] и tcs[3]; rPr/стиль ячеек не трогаем.
    """
    tcs = _tcs_of_row(header_tr)
    if len(tcs) < 5:
        return
    # Берём rPr из существующего run в tcs[2], чтобы сохранить
    # жирность/шрифт заголовка.
    src_run = tcs[2].find(f".//{_w('r')}")
    rpr = src_run.find(_w("rPr")) if src_run is not None else None
    _set_tc_text(tcs[2], "Кол-во", rpr)
    _set_tc_text(tcs[3], "Цена c НДС (руб.)", rpr)


def _force_bold(rpr: etree._Element | None) -> etree._Element:
    """Возвращает копию rpr с принудительным <w:b/>.

    Если rpr — None, создаёт новый <w:rPr> только с bold-атрибутом.
    Используется в ИТОГО, чтобы значение было жирным независимо от
    того, что лежало в шаблоне.
    """
    if rpr is None:
        rpr_out = etree.Element(_w("rPr"))
    else:
        rpr_out = copy.deepcopy(rpr)
    # Удаляем имеющиеся <w:b>/<w:bCs> и добавляем заново один раз.
    for tag in ("b", "bCs"):
        for el in rpr_out.findall(_w(tag)):
            rpr_out.remove(el)
    etree.SubElement(rpr_out, _w("b"))
    etree.SubElement(rpr_out, _w("bCs"))
    return rpr_out


def _update_itogo(itogo_tr: etree._Element, total_rub: int) -> None:
    """Обновляет сумму в последней ячейке строки ИТОГО. Значение всегда
    жирное — подстраховка, если в шаблоне rPr оказался не bold."""
    tcs = _tcs_of_row(itogo_tr)
    if not tcs:
        raise RuntimeError("В строке ИТОГО нет ячеек.")
    value_tc = tcs[-1]
    # В этой ячейке уже есть run с жирным форматированием — сохраняем его
    # как базу, но принудительно гарантируем <w:b/>.
    existing_run = value_tc.find(f".//{_w('r')}")
    rpr_src = existing_run.find(_w("rPr")) if existing_run is not None else None
    _set_tc_text(value_tc, _format_rub(total_rub), _force_bold(rpr_src))


# ---------------------------------------------------------------------
# Публичная функция
# ---------------------------------------------------------------------

def build_kp_docx(
    project_id: int,
    markup_percent: int,
    db: Session,
) -> bytes:
    """Собирает docx коммерческого предложения по проекту.

    markup_percent — целое 0..500, интерпретируется как +X% к закупочной
    цене в рублях. За пределами диапазона → ValueError.

    Курс берётся через exchange_rate.get_usd_rate(); если курс недоступен
    и нет кэша — пробрасывается RuntimeError (роутер отдаёт 503).
    """
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

    # Спецификация проекта (доступ/существование проверяет роутер через
    # _load_project_or_raise — здесь это чистая функция над БД).
    spec_items = spec_service.list_spec_items(db, project_id=project_id)

    # Считаем строки + общий итог.
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

    # Открываем шаблон.
    doc = docx.Document(str(_TEMPLATE_PATH))

    # Заменяем дату на сегодняшнюю.
    _replace_date_in_header(doc, date.today().strftime("%d.%m.%Y"))

    # Достаём внутреннюю таблицу (№/Наименование/Цена/Кол-во/Сумма + ИТОГО).
    inner = _find_inner_kp_table(doc)
    tbl_el = inner._tbl

    rows = tbl_el.findall(_w("tr"))
    if len(rows) < 3:
        raise RuntimeError(
            f"Шаблон KP: ожидалось >=3 строк во внутренней таблице, {len(rows)}."
        )
    header_tr = rows[0]      # «№ п/п, Наименование, Цена, Кол-во, Сумма»
    template_tr = rows[1]    # шаблонная строка с «1» и пустыми ячейками.
    itogo_tr = rows[-1]      # строка «ИТОГО».

    # Переставляем местами заголовки «Цена» и «Кол-во» — теперь «Кол-во»
    # идёт раньше, а цена — ближе к итоговой сумме.
    _update_header_row(header_tr)

    rpr_template = _extract_data_row_rpr(template_tr)

    # Вставляем сгенерированные строки ПЕРЕД ИТОГО — каждая клонируется
    # из шаблонной, чтобы унаследовать границы/ширину/заливку.
    for i, drow in enumerate(data_rows, start=1):
        new_tr = copy.deepcopy(template_tr)
        _fill_data_row_tr(
            new_tr,
            pos_num=i,
            name=drow["name"],
            price_rub=drow["price_rub"],
            qty=drow["qty"],
            total_rub=drow["total_rub"],
            rpr_template=rpr_template,
        )
        itogo_tr.addprevious(new_tr)

    # Убираем «болванку» (исходную шаблонную строку с «1»).
    tbl_el.remove(template_tr)

    # Обновляем ИТОГО.
    _update_itogo(itogo_tr, grand_total)

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
