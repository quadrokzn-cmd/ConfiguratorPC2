# Юнит-тесты правок этапа 8.4:
# - санитизация имён файлов экспорта (Excel/КП);
# - структура Excel после удаления «Создан: …»: нет A2, формулы в N,
#   ИТОГО суммирует только строки системных блоков;
# - KP: новый порядок колонок, отсутствие «руб.» в значениях,
#   непустое ИТОГО с жирным форматированием;
# - GPU naming: «GeForce 210» вместо «RTX 1», fallback-подстраховка
#   на подозрительных моделях.

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

import docx as _docx
from openpyxl import load_workbook

from app.routers.export_router import _safe_project_filename
from app.services.export import excel_builder, kp_builder
from app.services.spec_naming import (
    _block_gpu,
    _looks_suspicious_gpu_model,
    _short_gpu_model,
)


# =============================================================================
# Блок B — санитизация имён файлов
# =============================================================================

def test_safe_project_filename_replaces_windows_forbidden_chars():
    assert _safe_project_filename("Запрос от 24.04.2026 23:54", 1) == (
        "Запрос от 24.04.2026 23_54"
    )


def test_safe_project_filename_replaces_slashes():
    assert _safe_project_filename("Мой/проект*", 7) == "Мой_проект_"


def test_safe_project_filename_strips_trailing_space_and_dot():
    assert _safe_project_filename("Проект. ", 5) == "Проект"


def test_safe_project_filename_fallback_on_empty():
    assert _safe_project_filename("   ", 42) == "project_42"
    assert _safe_project_filename(None, 9) == "project_9"


def test_safe_project_filename_length_capped():
    long = "A" * 300
    result = _safe_project_filename(long, 1)
    assert len(result) <= 150
    assert result == "A" * 150


def test_safe_project_filename_all_forbidden_charset():
    # <>:"/\|?* — девять запрещённых Windows-символов подряд
    assert _safe_project_filename('a<>:"/\\|?*b', 1) == "a_________b"


# =============================================================================
# Блок C — Excel-раскладка: нет A2, формулы SUM в N на comp-строке
# =============================================================================

def _fake_blocks(item_name: str = "Системный блок") -> list:
    item = {
        "id": 1, "query_id": 101, "variant_manufacturer": "Intel",
        "quantity": 1, "position": 1,
        "auto_name": item_name, "custom_name": None,
        "display_name": item_name,
        "unit_usd": 200.0, "unit_rub": 18000.0,
        "total_usd": 200.0, "total_rub": 18000.0,
    }
    variant = {"manufacturer": "Intel"}
    comps = [
        {
            "category": "cpu", "component_id": 999,
            "model": "Intel Core i5", "sku": "CPU", "manufacturer": "Intel",
            "quantity": 1, "price_usd": 180.0,
            "specs_short": "6C/12T",
        },
        {
            "category": "ram", "component_id": 888,
            "model": "Kingston 8GB DDR4", "sku": "RAM", "manufacturer": "Kingston",
            "quantity": 2, "price_usd": 10.0,
            "specs_short": "8GB DDR4",
        },
    ]
    return [(item, variant, comps)]


def _build_xlsx_with_fake_data(item_name: str = "Системный блок"):
    """Собирает xlsx без обращения к БД: мокаем _load_project,
    spec_service.list_spec_items, _collect_blocks и _fetch_gtin_map.
    """
    fake_project = {
        "id": 1, "name": "Запрос от 24.04.2026 23:54",
        "created_at": datetime(2026, 4, 24, 23, 54),
        "author_login": "manager1", "author_name": "Менеджер",
    }
    blocks = _fake_blocks(item_name)

    with patch.object(excel_builder, "_load_project", return_value=fake_project), \
         patch(
             "app.services.export.excel_builder.spec_service.list_spec_items",
             return_value=[blocks[0][0]],
         ), \
         patch.object(excel_builder, "_collect_blocks", return_value=blocks), \
         patch.object(excel_builder, "_fetch_gtin_map", return_value={}):
        xlsx = excel_builder.build_project_xlsx(
            project_id=1, db=None,
            rate=Decimal("92.5"), rate_date=date(2026, 4, 24),
        )
    return load_workbook(BytesIO(xlsx))


def test_excel_no_cell_a2_after_polish():
    wb = _build_xlsx_with_fake_data()
    ws = wb.active
    # После удаления «Создан: …» в A2 уже заголовки таблицы — но
    # слова «Создан» в A2 больше быть не должно.
    a2 = ws["A2"].value
    assert not (a2 and "Создан" in str(a2)), (
        f"A2 должен быть заголовком таблицы, не меткой «Создан»: {a2!r}"
    )


def test_excel_headers_moved_to_row_2():
    wb = _build_xlsx_with_fake_data()
    ws = wb.active
    # В шаблоне заголовок колонки D — «Наименование».
    assert (ws["D2"].value or "").startswith("Наименование") or \
           "аим" in (ws["D2"].value or "")  # допускаем разную формулировку


def test_excel_n_formula_on_comp_row_is_sum_of_components():
    wb = _build_xlsx_with_fake_data()
    ws = wb.active
    # Comp-строка — первая строка данных (row 3). Компоненты — 4 и 5.
    n_comp = ws["N3"].value
    assert isinstance(n_comp, str) and n_comp.startswith("=SUM(N"), (
        f"В N3 должна быть формула SUM по N компонентов, получили {n_comp!r}"
    )
    assert "N4" in n_comp and "N5" in n_comp
    # Компоненты сами содержат числа (реальные цены).
    assert ws["N4"].value == 180.0
    assert ws["N5"].value == 10.0


def test_excel_totals_sum_only_comp_rows():
    # Два конфигурации — должно быть =SUM(J<comp1>, J<comp2>),
    # не включая строки компонентов.
    item1 = {
        "id": 1, "query_id": 101, "variant_manufacturer": "Intel",
        "quantity": 1, "position": 1,
        "auto_name": "comp A", "custom_name": None, "display_name": "comp A",
        "unit_usd": 100.0, "unit_rub": 9000.0,
        "total_usd": 100.0, "total_rub": 9000.0,
    }
    item2 = {**item1, "id": 2, "query_id": 102, "auto_name": "comp B",
             "display_name": "comp B", "position": 2}
    blocks = [
        (item1, {"manufacturer": "Intel"}, [
            {"category": "cpu", "component_id": 1, "model": "CPU",
             "sku": "s1", "manufacturer": "Intel", "quantity": 1,
             "price_usd": 80.0, "specs_short": ""},
        ]),
        (item2, {"manufacturer": "AMD"}, [
            {"category": "cpu", "component_id": 2, "model": "CPU",
             "sku": "s2", "manufacturer": "AMD", "quantity": 1,
             "price_usd": 90.0, "specs_short": ""},
        ]),
    ]
    fake_project = {
        "id": 1, "name": "Два проекта",
        "created_at": datetime(2026, 4, 24, 12, 0),
        "author_login": "m", "author_name": "M",
    }
    with patch.object(excel_builder, "_load_project", return_value=fake_project), \
         patch(
             "app.services.export.excel_builder.spec_service.list_spec_items",
             return_value=[item1, item2],
         ), \
         patch.object(excel_builder, "_collect_blocks", return_value=blocks), \
         patch.object(excel_builder, "_fetch_gtin_map", return_value={}):
        xlsx = excel_builder.build_project_xlsx(
            project_id=1, db=None,
            rate=Decimal("90"), rate_date=date(2026, 4, 24),
        )
    wb = load_workbook(BytesIO(xlsx))
    ws = wb.active
    # Раскладка: row 3 comp A, row 4 CPU A, row 5 comp B, row 6 CPU B.
    # last_data_row=6 → sum_row=8. Формулы должны ссылаться на 3 и 5.
    g_formula = ws["G8"].value
    j_formula = ws["J8"].value
    assert g_formula == "=SUM(G3,G5)", f"Got {g_formula!r}"
    assert j_formula == "=SUM(J3,J5)", f"Got {j_formula!r}"


def test_excel_non_comp_rows_have_no_fill():
    wb = _build_xlsx_with_fake_data()
    ws = wb.active
    # Компонентные строки (4, 5) — без заливки.
    for r in (4, 5):
        for col in "BCDEFGHIJKLMN":
            cell = ws[f"{col}{r}"]
            fg = cell.fill.fgColor
            # fgColor.rgb у «прозрачных» ячеек либо None, либо '00000000'.
            rgb = getattr(fg, "rgb", None) if fg else None
            if isinstance(rgb, str) and rgb != "00000000":
                # Допустимо только в comp-строке (row 3) — на ней голубая.
                assert False, (
                    f"Неожиданная заливка в {col}{r}: {rgb!r}"
                )


def test_excel_totals_cells_have_borders():
    wb = _build_xlsx_with_fake_data()
    ws = wb.active
    # last_data_row = 5, sum_row = 7, pct_row = 9, abs_row = 10.
    for coord in ("F7", "G7", "I7", "J7", "I9", "J9", "I10", "J10"):
        b = ws[coord].border
        assert b.left and b.left.style, f"{coord}: нет левой границы"
        assert b.right and b.right.style, f"{coord}: нет правой границы"


# =============================================================================
# Блок E — KP: порядок колонок, нет «руб.», ИТОГО заполнено и жирное
# =============================================================================

_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _kp_inner_table(doc):
    return doc.tables[0].rows[0].cells[0].tables[0]


def _kp_header_texts(doc) -> list[str]:
    inner = _kp_inner_table(doc)
    header_tr = inner._tbl.findall(f"{_NS}tr")[0]
    out = []
    for tc in header_tr.findall(f"{_NS}tc"):
        out.append("".join(t.text or "" for t in tc.findall(f".//{_NS}t")))
    return out


def _mock_rate(value: str = "90"):
    return patch(
        "app.services.export.kp_builder.exchange_rate.get_usd_rate",
        return_value=(Decimal(value), date(2026, 4, 24), "cache"),
    )


def _fake_items(items_spec):
    out = []
    for i, (uu, qty, name) in enumerate(items_spec, start=1):
        out.append({
            "id": i, "query_id": 100 + i, "variant_manufacturer": "Intel",
            "quantity": qty, "position": i,
            "auto_name": name, "custom_name": None, "display_name": name,
            "unit_usd": uu, "unit_rub": 0.0,
            "total_usd": round(uu * qty, 2), "total_rub": 0.0,
            "created_at": None, "updated_at": None,
        })
    return out


def _build_kp_with_fake(items_spec, markup=15):
    with patch(
        "app.services.export.kp_builder.spec_service.list_spec_items",
        return_value=_fake_items(items_spec),
    ), _mock_rate("90"):
        data = kp_builder.build_kp_docx(
            project_id=1, markup_percent=markup, db=None,
        )
    return _docx.Document(BytesIO(data))


def test_kp_columns_order_kol_before_price():
    doc = _build_kp_with_fake([(100.0, 1, "Конфиг")])
    headers = _kp_header_texts(doc)
    # tc[0]=№, tc[1]=Наименование, tc[2]=Кол-во, tc[3]=Цена..., tc[4]=Сумма...
    assert len(headers) == 5
    assert "Кол" in headers[2], f"В колонке 2 должно быть «Кол-во»: {headers[2]!r}"
    assert "Цена" in headers[3], f"В колонке 3 — «Цена»: {headers[3]!r}"
    assert "Сумма" in headers[4], f"В колонке 4 — «Сумма»: {headers[4]!r}"


def test_kp_no_ruble_suffix_in_value_cells():
    doc = _build_kp_with_fake([(100.0, 1, "x")])
    inner = _kp_inner_table(doc)
    rows = inner._tbl.findall(f"{_NS}tr")
    # Проходим по строкам данных и строке ИТОГО: значений «… руб.» быть не должно.
    for tr in rows[1:]:
        for tc in tr.findall(f"{_NS}tc"):
            text = "".join(t.text or "" for t in tc.findall(f".//{_NS}t"))
            assert "руб" not in text.lower(), (
                f"В ячейке встретилось «руб»: {text!r}"
            )


def test_kp_itogo_filled_for_single_item():
    doc = _build_kp_with_fake([(50.0, 1, "ОдинТовар")])
    inner = _kp_inner_table(doc)
    rows = inner._tbl.findall(f"{_NS}tr")
    itogo_tc = rows[-1].findall(f"{_NS}tc")[-1]
    text = "".join(t.text or "" for t in itogo_tc.findall(f".//{_NS}t"))
    assert text.strip(), "ИТОГО не должно быть пустым даже при одной позиции"
    # 50*90=4500 → +15% = 5175
    assert "5 175" in text


def test_kp_itogo_is_bold():
    doc = _build_kp_with_fake([(100.0, 1, "жирный итог")])
    inner = _kp_inner_table(doc)
    rows = inner._tbl.findall(f"{_NS}tr")
    itogo_tc = rows[-1].findall(f"{_NS}tc")[-1]
    # Найдём первый run и проверим rPr/b.
    run = itogo_tc.find(f".//{_NS}r")
    assert run is not None, "В ИТОГО нет run-а"
    rpr = run.find(f"{_NS}rPr")
    assert rpr is not None, "У run-а ИТОГО нет rPr — нельзя гарантировать bold"
    b_el = rpr.find(f"{_NS}b")
    assert b_el is not None, "В ИТОГО значение должно быть жирным (<w:b/>)"


# =============================================================================
# Блок D — GPU naming
# =============================================================================

def test_gpu_geforce_210_is_no_longer_rtx_1():
    raw = (
        "Видеокарта Biostar PCI-E G210-1GB D3 LP NVIDIA GeForce 210 "
        "1Gb 64bit DDR3 589/1333 DVIx1 HDMIx1 CRTx1 Ret low profile [VN2103NHG6]"
    )
    assert _short_gpu_model(raw) == "GeForce 210"


def test_gpu_rtx_not_matched_inside_word():
    # CRTx1 не должен давать ложный «RTX».
    raw = "CRTx1 Ret low profile"
    # В такой строке нет валидных маркеров → возвращаем то что осталось.
    out = _short_gpu_model(raw)
    assert out != "RTX x1" and not (out or "").startswith("RTX ")


def test_gpu_fallback_uses_brand_and_vram_on_suspicious_model():
    # Симулируем ситуацию, когда парсер всё же вернул подозрительное.
    out = _block_gpu(
        # Модель, из которой парсер извлечёт «RTX 1».
        "test something RTX 1 etc",
        {"vram_gb": 2, "vram_type": "DDR3"},
        "NVIDIA Corporation",
    )
    assert out is not None
    # В fallback нет «RTX 1», есть бренд и объём.
    assert "RTX 1" not in out
    assert "2GB" in out


def test_gpu_suspicious_detector():
    assert _looks_suspicious_gpu_model("RTX 1")
    assert _looks_suspicious_gpu_model("GTX 2")
    assert _looks_suspicious_gpu_model("Radeon 9")
    assert not _looks_suspicious_gpu_model("RTX 4060")
    assert not _looks_suspicious_gpu_model("Radeon RX 7600")
    assert not _looks_suspicious_gpu_model(None)
