from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from app.services.auctions.ingest.card_parser import parse_card

FIXTURES = Path(__file__).parent / "fixtures" / "raw_html"

SAMPLE_HTML = """
<html><body>
<div class="cardMainInfo">
  <div class="cardMainInfo__title">Полное наименование заказчика</div>
  <div class="section__info">ФГБУ ТЕСТОВЫЙ ЗАКАЗЧИК</div>

  <div class="cardMainInfo__title">Место нахождения</div>
  <div class="section__info">Российская Федерация, 420015, Республика Татарстан, ул. Тестовая, 1</div>

  <div class="cardMainInfo__title">Начальная (максимальная) цена контракта</div>
  <div class="section__info">125 000,00 ₽</div>

  <div class="cardMainInfo__title">Размещено</div>
  <div class="section__info">10.04.2026</div>

  <div class="cardMainInfo__title">Дата и время окончания срока подачи заявок</div>
  <div class="section__info">25.04.2026 09:00 (МСК)</div>

  <div class="cardMainInfo__title">Срок исполнения контракта</div>
  <div class="section__info">15.07.2026</div>
</div>

<div class="blockInfo">
  <div class="blockInfo__title">Контактная информация</div>
  <div class="blockInfo__row">Ответственное должностное лицо</div>
  <div class="section__info">Иванов Иван Иванович</div>
  <div class="blockInfo__row">Должность</div>
  <div class="section__info">Контрактный управляющий</div>
  <div>Телефон: +7 (843) 555-12-34</div>
  <div>E-mail: contact@test-customer.ru</div>
</div>

<table>
  <tr><th>Код позиции КТРУ</th><th>Наименование товара</th><th>Количество</th><th>Единица измерения</th><th>Цена за единицу</th></tr>
  <tr>
    <td>26.20.18.000-00000069</td>
    <td>МФУ ч/б A4</td>
    <td>5</td>
    <td>шт</td>
    <td>25 000,00</td>
  </tr>
</table>
</body></html>
"""


def test_parse_card_extracts_core_fields():
    card = parse_card("0000000000000000001", "https://zakupki.gov.ru/x", SAMPLE_HTML)

    assert card.reg_number == "0000000000000000001"
    assert card.customer == "ФГБУ ТЕСТОВЫЙ ЗАКАЗЧИК"
    assert "Татарстан" in (card.customer_region or "")
    assert card.nmck_total == Decimal("125000.00")
    assert card.publish_date is not None
    assert card.publish_date.year == 2026
    assert card.submit_deadline is not None
    assert card.submit_deadline.hour == 9
    assert card.delivery_deadline is not None
    assert card.delivery_deadline.day == 15

    assert card.customer_contacts_jsonb.get("email") == "contact@test-customer.ru"
    assert "843" in card.customer_contacts_jsonb.get("phone", "")
    assert "Иванов" in (card.customer_contacts_jsonb.get("fio") or "")

    assert len(card.items) == 1
    item = card.items[0]
    assert item.ktru_code == "26.20.18.000-00000069"
    assert item.qty == Decimal("5")
    assert item.unit == "шт"
    assert item.nmck_per_unit == Decimal("25000.00")
    assert "26.20.18.000-00000069" in card.ktru_codes


def test_parse_card_handles_missing_data():
    card = parse_card("0000000000000000002", "https://zakupki.gov.ru/y", "<html><body></body></html>")
    assert card.reg_number == "0000000000000000002"
    assert card.customer is None
    assert card.nmck_total is None
    assert card.items == []
    assert card.ktru_codes == []


def _load_fixture(reg_number: str) -> str:
    path = FIXTURES / f"{reg_number}.html"
    if not path.exists():
        pytest.skip(f"raw_html fixture {path.name} missing — run scripts/_dump_raw_html.py")
    return path.read_text(encoding="utf-8")


def test_multi_position_lot_extracts_per_unit_for_every_position():
    """0848300064126000162 (13 строк в карточке zakupki, из них 10 настоящих позиций
    с КТРУ + 3 «строки-единицы» из expander rows). После фикса в _parse_items
    мусорные строки отсечены, у всех настоящих позиций есть nmck_per_unit."""
    rn = "0848300064126000162"
    card = parse_card(rn, f"https://zakupki.gov.ru/{rn}", _load_fixture(rn))
    assert card.nmck_total == Decimal("518774.75")
    assert len(card.items) >= 10
    no_pu = [it for it in card.items if it.nmck_per_unit is None]
    assert no_pu == [], f"per-unit missing for: {[it.position_num for it in no_pu]}"
    by_ktru = {it.ktru_code: it for it in card.items if it.ktru_code}
    # известные строки из карточки (см. диагностику Волны 2)
    assert by_ktru["26.20.16.170-00000002"].nmck_per_unit == Decimal("455.00")  # мышь × 24
    assert by_ktru["26.20.15.000-00000028"].nmck_per_unit == Decimal("49227.25")  # системный блок × 4
    assert by_ktru["26.20.18.000-00000068"].nmck_per_unit == Decimal("162069.50")  # МФУ A3 × 1


def test_multi_position_lot_with_intercom_panels_extracts_per_unit():
    """0107300018926000042 — 12 строк в карточке, 10 настоящих позиций с КТРУ.
    Цены: ноутбук 75358.33, интерактивная панель 240500, принтер 23633.33,
    МФУ 33700, системный блок 90533.33."""
    rn = "0107300018926000042"
    card = parse_card(rn, f"https://zakupki.gov.ru/{rn}", _load_fixture(rn))
    assert card.nmck_total == Decimal("4788308.96")
    no_pu = [it for it in card.items if it.nmck_per_unit is None]
    assert no_pu == []
    by_ktru = {it.ktru_code: it for it in card.items if it.ktru_code}
    assert by_ktru["26.20.13.000-00000002"].nmck_per_unit == Decimal("240500.00")  # интерактивная панель
    assert by_ktru["26.20.11.110-00000139"].nmck_per_unit == Decimal("75358.33")  # ноутбук
    assert by_ktru["26.20.18.000-00000069"].nmck_per_unit == Decimal("33700.00")  # МФУ A4


def test_multi_position_lot_extracts_per_unit_third_sample():
    """0317100032926000169 — 12 строк, 10 настоящих позиций с КТРУ + 2 единицы
    измерения. Принтер: 15661.45, мышь × 100: 2899.00, источник питания: 19750.00."""
    rn = "0317100032926000169"
    card = parse_card(rn, f"https://zakupki.gov.ru/{rn}", _load_fixture(rn))
    assert card.nmck_total == Decimal("3371417.90")
    no_pu = [it for it in card.items if it.nmck_per_unit is None]
    assert no_pu == []
    by_ktru = {it.ktru_code: it for it in card.items if it.ktru_code}
    assert by_ktru["26.20.16.120-00000101"].nmck_per_unit == Decimal("15661.45")  # принтер
    assert by_ktru["26.20.40.110-00000001"].nmck_per_unit == Decimal("19750.00")  # ИБП


def test_single_position_lot_still_works_after_fix():
    """0358200055826000034 — single-position лот; должен остаться 1 элемент с per-unit."""
    rn = "0358200055826000034"
    card = parse_card(rn, f"https://zakupki.gov.ru/{rn}", _load_fixture(rn))
    assert len(card.items) == 1
    assert card.items[0].nmck_per_unit == Decimal("833408.00")
    assert card.items[0].qty == Decimal("2.00")


def test_expander_attrs_extracted_from_truinfo_sibling():
    """0373100056024000064 — богатая таблица характеристик в `<tr class="truInfo_…">`-сёстре.
    После фикса scope в `_collect_raw_position_attrs` и нормализации в schema-keys
    `required_attrs_jsonb` у обеих позиций должен содержать print_speed_ppm=30,
    max_format=A4, colorness=ч/б, USB+LAN."""
    rn = "0373100056024000064"
    card = parse_card(rn, f"https://zakupki.gov.ru/{rn}", _load_fixture(rn))
    assert len(card.items) == 2
    # Обе позиции — МФУ ч/б A4, 30 стр/мин, электрографическая, USB+LAN.
    for it in card.items:
        attrs = it.required_attrs_jsonb
        assert attrs.get("colorness") == "ч/б", f"pos {it.position_num}: {attrs}"
        assert attrs.get("max_format") == "A4", f"pos {it.position_num}: {attrs}"
        assert attrs.get("print_speed_ppm") == 30, f"pos {it.position_num}: {attrs}"
        assert attrs.get("print_technology") == "электрографическая", f"pos {it.position_num}: {attrs}"
        assert attrs.get("usb") == "yes", f"pos {it.position_num}: {attrs}"
        assert attrs.get("network_interface") == "LAN", f"pos {it.position_num}: {attrs}"
        assert attrs.get("resolution_dpi") == 600, f"pos {it.position_num}: {attrs}"


def test_expander_attrs_per_position_in_multi_position_lot():
    """0848300064126000162 — multi-position лот, 10 настоящих позиций (мыши, ноутбуки,
    панели, принтер, МФУ). Только ~3 позиции из 10 — печатные устройства; у них и
    должны быть schema-атрибуты, у остальных `required_attrs_jsonb` = {}."""
    rn = "0848300064126000162"
    card = parse_card(rn, f"https://zakupki.gov.ru/{rn}", _load_fixture(rn))
    items_with_print_attrs = [
        it for it in card.items
        if it.required_attrs_jsonb.get("colorness") or it.required_attrs_jsonb.get("max_format")
    ]
    assert len(items_with_print_attrs) >= 2, (
        f"expected >=2 print-related positions with schema attrs, got "
        f"{[(it.position_num, it.ktru_code, list(it.required_attrs_jsonb)) for it in card.items]}"
    )
    # МФУ A3 (26.20.18.000-00000068) должен иметь max_format=A3.
    by_ktru = {it.ktru_code: it for it in card.items if it.ktru_code}
    if "26.20.18.000-00000068" in by_ktru:
        a3_mfu = by_ktru["26.20.18.000-00000068"]
        assert a3_mfu.required_attrs_jsonb.get("max_format") == "A3"


def test_single_position_lot_no_expander_does_not_break():
    """0358200055826000034 — single-position; задача проверки в том, что фолбэк
    (нет chevron / нет truInfo-сестры в синтетических случаях) не падает и возвращает
    либо нормализованные атрибуты из реального expander'а, либо пустой dict."""
    rn = "0358200055826000034"
    card = parse_card(rn, f"https://zakupki.gov.ru/{rn}", _load_fixture(rn))
    assert len(card.items) == 1
    # Структурно не должно падать; required_attrs_jsonb — dict (возможно пустой).
    assert isinstance(card.items[0].required_attrs_jsonb, dict)


def test_inline_synthetic_table_still_works_via_fallback():
    """Минимальная синтетическая карточка с таблицей характеристик прямо внутри row
    (без truInfo-сестры) — фолбэк `_extract_position_attrs(row)` должен сработать."""
    html = """
    <html><body>
    <div class="cardMainInfo__title">Полное наименование заказчика</div>
    <div class="section__info">ФГБУ ТЕСТ</div>
    <table>
      <tr><th>Код позиции КТРУ</th><th>Наименование товара</th><th>Количество</th><th>Цена за ед., ₽</th></tr>
      <tr>
        <td>26.20.18.000-00000069</td>
        <td>МФУ ч/б A4 со встроенной таблицей характеристик</td>
        <td>1</td>
        <td>10000,00</td>
        <td>
          <table>
            <tr><th>Наименование характеристики</th><th>Значение характеристики</th></tr>
            <tr><td>Цветность</td><td>Черно-Белая</td></tr>
            <tr><td>Максимальный формат печати</td><td>А4</td></tr>
            <tr><td>Скорость черно-белой печати, стр/мин</td><td>≥ 22</td></tr>
          </table>
        </td>
      </tr>
    </table>
    </body></html>
    """
    card = parse_card("0000000000000000004", "https://x", html)
    assert len(card.items) == 1
    attrs = card.items[0].required_attrs_jsonb
    assert attrs.get("colorness") == "ч/б"
    assert attrs.get("max_format") == "A4"
    assert attrs.get("print_speed_ppm") == 22


def test_multiple_expander_siblings_merged_into_one_position():
    """9a-fixes-3 #3: у одной позиции бывает 1-4 sibling-<tr> с одним
    truInfo_NNN id (BS4 + lxml склеивают часть характеристик в одну
    sibling-строку, часть в другую). Все они должны слиться в один
    required_attrs_jsonb."""
    html = """
    <html><body>
    <div class="cardMainInfo__title">Полное наименование заказчика</div>
    <div class="section__info">ФГБУ ТЕСТ</div>
    <table>
      <tr><th>Код позиции КТРУ</th><th>Наименование товара</th><th>Количество</th><th>Цена за ед., ₽</th></tr>
      <tr>
        <td>26.20.18.000-00000069</td>
        <td><span class="chevronRight" onclick="toggle('truInfo_111111')">▶</span> МФУ ч/б A4 multi-expander</td>
        <td>1</td>
        <td>10000,00</td>
      </tr>
      <tr class="truInfo_111111">
        <td colspan="4">
          <table>
            <tr><th>Наименование характеристики</th><th>Значение характеристики</th></tr>
            <tr><td>Цветность</td><td>Черно-Белая</td></tr>
          </table>
        </td>
      </tr>
      <tr class="truInfo_111111 tableBlock__row">
        <td colspan="4">
          <table>
            <tr><th>Наименование характеристики</th><th>Значение характеристики</th></tr>
            <tr><td>Максимальный формат печати</td><td>А4</td></tr>
          </table>
        </td>
      </tr>
      <tr class="truInfo_111111">
        <td colspan="4">
          <table>
            <tr><th>Наименование характеристики</th><th>Значение характеристики</th></tr>
            <tr><td>Скорость черно-белой печати, стр/мин</td><td>≥ 24</td></tr>
            <tr><td>Разрешение печати, dpi</td><td>≥ 600</td></tr>
          </table>
        </td>
      </tr>
    </table>
    </body></html>
    """
    card = parse_card("0000000000000000777", "https://x", html)
    assert len(card.items) == 1
    item = card.items[0]
    attrs = item.required_attrs_jsonb
    # Все три expander'а должны быть слиты:
    assert attrs.get("colorness") == "ч/б", attrs
    assert attrs.get("max_format") == "A4", attrs
    assert attrs.get("print_speed_ppm") == 24, attrs
    assert attrs.get("resolution_dpi") == 600, attrs
    # name расширен характеристиками из expander'ов (9a-fixes-3 #3).
    # Чтобы в карточке лота `<details>` показывал не только короткое имя
    # из колонки, но и атрибуты, попавшие в expander'ы.
    assert item.name is not None
    assert "Цветность" in item.name or "Скорость" in item.name or "Разрешение" in item.name


def test_expander_collection_skips_intermediate_blank_tr():
    """Промежуточный служебный <tr> между expander'ами одной позиции
    не должен оборвать сбор — собираем все expander'ы до chevron'а
    следующей позиции или до другого truInfo_-id."""
    html = """
    <html><body>
    <div class="cardMainInfo__title">Полное наименование заказчика</div>
    <div class="section__info">ФГБУ ТЕСТ</div>
    <table>
      <tr><th>Код позиции КТРУ</th><th>Наименование товара</th><th>Количество</th><th>Цена за ед., ₽</th></tr>
      <tr>
        <td>26.20.18.000-00000069</td>
        <td><span class="chevronRight" onclick="toggle('truInfo_222222')">▶</span> МФУ A4 c пропуском</td>
        <td>2</td>
        <td>15000,00</td>
      </tr>
      <tr class="truInfo_222222">
        <td colspan="4">
          <table>
            <tr><th>Наименование характеристики</th><th>Значение характеристики</th></tr>
            <tr><td>Цветность</td><td>Черно-Белая</td></tr>
          </table>
        </td>
      </tr>
      <tr><td colspan="4"></td></tr>
      <tr class="truInfo_222222">
        <td colspan="4">
          <table>
            <tr><th>Наименование характеристики</th><th>Значение характеристики</th></tr>
            <tr><td>Максимальный формат печати</td><td>А4</td></tr>
          </table>
        </td>
      </tr>
    </table>
    </body></html>
    """
    card = parse_card("0000000000000000888", "https://x", html)
    assert len(card.items) == 1
    attrs = card.items[0].required_attrs_jsonb
    assert attrs.get("colorness") == "ч/б", attrs
    assert attrs.get("max_format") == "A4", attrs


def test_expander_collection_stops_on_next_position_chevron():
    """Сбор expander'ов одной позиции должен остановиться, когда
    встречается визуальная строка следующей позиции (chevronRight)."""
    html = """
    <html><body>
    <div class="cardMainInfo__title">Полное наименование заказчика</div>
    <div class="section__info">ФГБУ ТЕСТ</div>
    <table>
      <tr><th>Код позиции КТРУ</th><th>Наименование товара</th><th>Количество</th><th>Цена за ед., ₽</th></tr>
      <tr>
        <td>26.20.18.000-00000069</td>
        <td><span class="chevronRight" onclick="toggle('truInfo_333111')">▶</span> МФУ позиция 1 длинное название</td>
        <td>1</td>
        <td>10000,00</td>
      </tr>
      <tr class="truInfo_333111">
        <td colspan="4">
          <table>
            <tr><th>Наименование характеристики</th><th>Значение характеристики</th></tr>
            <tr><td>Цветность</td><td>Черно-Белая</td></tr>
          </table>
        </td>
      </tr>
      <tr>
        <td>26.20.16.120-00000139</td>
        <td><span class="chevronRight" onclick="toggle('truInfo_333222')">▶</span> Принтер позиция 2 длинное название</td>
        <td>3</td>
        <td>8000,00</td>
      </tr>
      <tr class="truInfo_333222">
        <td colspan="4">
          <table>
            <tr><th>Наименование характеристики</th><th>Значение характеристики</th></tr>
            <tr><td>Максимальный формат печати</td><td>А3</td></tr>
          </table>
        </td>
      </tr>
    </table>
    </body></html>
    """
    card = parse_card("0000000000000000999", "https://x", html)
    assert len(card.items) == 2
    # Позиция 1: только colorness (max_format=A3 принадлежит позиции 2).
    pos1 = next(it for it in card.items if it.ktru_code == "26.20.18.000-00000069")
    assert pos1.required_attrs_jsonb.get("colorness") == "ч/б"
    assert "max_format" not in pos1.required_attrs_jsonb
    # Позиция 2: max_format=A3, без colorness.
    pos2 = next(it for it in card.items if it.ktru_code == "26.20.16.120-00000139")
    assert pos2.required_attrs_jsonb.get("max_format") == "A3"
    assert "colorness" not in pos2.required_attrs_jsonb


def test_parse_card_no_price_structure_returns_none_per_unit():
    """Карточка без колонки цены (только КТРУ + qty) — nmck_per_unit=None, без падения."""
    html = """
    <html><body>
    <div class="cardMainInfo__title">Полное наименование заказчика</div>
    <div class="section__info">ФГБУ ТЕСТ</div>
    <table>
      <tr><th>Код позиции КТРУ</th><th>Наименование товара</th><th>Количество</th></tr>
      <tr>
        <td>26.20.18.000-00000069</td>
        <td>МФУ ч/б A4 без указания цены</td>
        <td>3</td>
      </tr>
    </table>
    </body></html>
    """
    card = parse_card("0000000000000000003", "https://x", html)
    assert len(card.items) == 1
    assert card.items[0].nmck_per_unit is None
    assert card.items[0].qty == Decimal("3")
