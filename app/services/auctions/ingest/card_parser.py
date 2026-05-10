from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from bs4 import BeautifulSoup, Tag
from loguru import logger

from app.services.auctions.ingest.attrs_normalizer import normalize_attrs

KTRU_CODE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{2}\.\d{3}-\d{8}\b")
PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{6,}\d")
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
NUMBER_RE = re.compile(r"-?\d{1,3}(?:[   ]\d{3})*(?:[.,]\d+)?|-?\d+(?:[.,]\d+)?")
DATETIME_RE = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})(?:[ T](\d{2}):(\d{2}))?")


@dataclass
class TenderItem:
    position_num: int
    ktru_code: str | None
    name: str | None
    qty: Decimal
    unit: str | None
    nmck_per_unit: Decimal | None
    required_attrs_jsonb: dict[str, Any]


@dataclass
class TenderCard:
    reg_number: str
    url: str
    customer: str | None = None
    customer_region: str | None = None
    customer_contacts_jsonb: dict[str, Any] = field(default_factory=dict)
    nmck_total: Decimal | None = None
    publish_date: datetime | None = None
    submit_deadline: datetime | None = None
    delivery_deadline: datetime | None = None
    ktru_codes: list[str] = field(default_factory=list)
    items: list[TenderItem] = field(default_factory=list)
    raw_html: str = ""


def _clean(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned or None


def _parse_decimal(text: str | None) -> Decimal | None:
    if text is None:
        return None
    match = NUMBER_RE.search(text)
    if not match:
        return None
    raw = match.group(0)
    raw = raw.replace(" ", "").replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def _parse_dt(text: str | None) -> datetime | None:
    if text is None:
        return None
    match = DATETIME_RE.search(text)
    if not match:
        return None
    day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
    hour = int(match.group(4)) if match.group(4) else 0
    minute = int(match.group(5)) if match.group(5) else 0
    msk = timezone(timedelta(hours=3))
    try:
        return datetime(year, month, day, hour, minute, tzinfo=msk)
    except ValueError:
        return None


def _section_candidates(soup: BeautifulSoup, label: str) -> list[str]:
    """Yield all values that follow any label-matching element. Multiple matches are common
    on zakupki notice pages (e.g., the same label may appear in a tab nav + a section header)."""
    results: list[str] = []
    for node in soup.find_all(string=re.compile(re.escape(label), re.IGNORECASE)):
        parent = node.parent
        if not isinstance(parent, Tag):
            continue
        for cls in ("section__info", "section__title", "row__info"):
            sibling = parent.find_next(class_=cls)
            if sibling and isinstance(sibling, Tag):
                text = _clean(sibling.get_text(" ", strip=True))
                if text and text != label and text not in results:
                    results.append(text)
        nxt = parent.find_next_sibling()
        if nxt and isinstance(nxt, Tag):
            text = _clean(nxt.get_text(" ", strip=True))
            if text and text != label and text not in results:
                results.append(text)
    return results


def _section_text(soup: BeautifulSoup, label: str) -> str | None:
    candidates = _section_candidates(soup, label)
    return candidates[0] if candidates else None


_ORG_NEGATIVE_PREFIXES = (
    "ВНИМАНИЕ", "ЗА НАРУШЕНИЕ", "ИНФОРМАЦИЯ О", "СВЕДЕНИЯ", "ПРИМЕЧАНИЕ",
)
_ORG_NEGATIVE_SUBSTRINGS = (
    "АНТИМОНОПОЛЬНОГО ЗАКОНОДАТЕЛЬСТВА", "КОАП РФ", "УК РФ",
)


def _looks_like_org(text: str | None) -> bool:
    if not text:
        return False
    upper = text.upper()
    if any(upper.startswith(prefix) for prefix in _ORG_NEGATIVE_PREFIXES):
        return False
    if any(sub in upper for sub in _ORG_NEGATIVE_SUBSTRINGS):
        return False
    org_markers = (
        "ОБЩЕСТВО", "АО ", " АО", "ЗАО", "ОАО", "ПАО", "ООО", "ИП ",
        "УЧРЕЖДЕНИЕ", "АДМИНИСТРАЦИЯ",
        "МИНИСТЕРСТВО", "ФЕДЕРАЛЬНОЕ", "ГОСУДАРСТВЕННОЕ",
        "МУНИЦИПАЛЬНОЕ", "МКУ", "МБУ", "МКОУ", "МАУ",
        "ГБУ", "ГКУ", "ГБОУ", "ФГУП", "ФГБУ", "ФКУ",
        "ОТДЕЛЕНИЕ", "УПРАВЛЕНИЕ", "ИНСТИТУТ", "АКАДЕМИЯ",
        "АГЕНТСТВО", "СЛУЖБА", "ИНСПЕКЦИЯ", "КОМИТЕТ", "ДЕПАРТАМЕНТ",
        "ПРАВИТЕЛЬСТВО", "СОВЕТ", "ШКОЛА", "БОЛЬНИЦА", "ПОЛИКЛИНИКА",
        "БИБЛИОТЕКА", "ПРОКУРАТУРА", "ДЕТСКИЙ САД", "ДЕТСКОЕ",
    )
    return any(marker in upper for marker in org_markers)


def _first_org_like(soup: BeautifulSoup, labels: tuple[str, ...]) -> str | None:
    """Try labels in order; for each, scan all sibling candidates and pick the first whose
    text looks like an org name. Robust to layout drift on zakupki: some lots use
    «Организация, осуществляющая размещение», others use «Наименование заказчика». A match
    is accepted only if it looks like an org (contains keywords like «УЧРЕЖДЕНИЕ», «ООО» etc.).
    """
    for label in labels:
        for candidate in _section_candidates(soup, label):
            if _looks_like_org(candidate):
                return candidate
    for label in labels:
        for candidate in _section_candidates(soup, label):
            if candidate:
                return candidate
    return None


def _extract_region(text: str | None) -> str | None:
    if not text:
        return None
    if "," in text:
        # "Российская Федерация, 125993, Москва, УЛ. БАРРИКАДНАЯ" → take 3rd component
        parts = [p.strip() for p in text.split(",") if p.strip()]
        for part in parts:
            if part.lower().startswith("российская федерация"):
                continue
            if re.fullmatch(r"\d{5,6}", part):
                continue
            return part
    return text


def _extract_contacts(soup: BeautifulSoup) -> dict[str, Any]:
    contacts: dict[str, Any] = {}
    contact_block: Tag | None = None
    for header in soup.find_all(string=re.compile("Контактн", re.IGNORECASE)):
        parent = header.parent
        if isinstance(parent, Tag):
            block = parent.find_parent(class_=re.compile(r"(blockInfo|cardMainInfo|noticeTabBoxWrapper)"))
            if block:
                contact_block = block
                break
    haystack = contact_block.get_text(" ", strip=True) if contact_block else soup.get_text(" ", strip=True)
    haystack = haystack[:8000]
    email_match = EMAIL_RE.search(haystack)
    if email_match:
        contacts["email"] = email_match.group(0)
    phone_match = PHONE_RE.search(haystack)
    if phone_match:
        contacts["phone"] = re.sub(r"\s+", " ", phone_match.group(0)).strip()
    fio = _section_text(soup, "Ответственное должностное лицо") or _section_text(soup, "Контактное лицо")
    if fio:
        contacts["fio"] = fio
    position = _section_text(soup, "Должность")
    if position:
        contacts["position"] = position
    return contacts


def _extract_ktru_codes(soup: BeautifulSoup) -> list[str]:
    text = soup.get_text(" ", strip=True)
    return sorted({m.group(0) for m in KTRU_CODE_RE.finditer(text)})


def _parse_items(soup: BeautifulSoup) -> list[TenderItem]:
    """Parse positions table from "Информация об объекте закупки" section.

    Layout: each position is a card-like block with headers «Код позиции КТРУ»,
    «Наименование товара», «Количество», «Единица измерения», «Цена за единицу»,
    plus a characteristics sub-table.
    """
    items: list[TenderItem] = []
    seen_keys: set[str] = set()
    unknown_attr_keys: set[str] = set()

    candidate_tables = soup.find_all("table")
    position_idx = 0
    for table in candidate_tables:
        header_row = table.find("tr")
        if not header_row:
            continue
        header_text = _clean(header_row.get_text(" ", strip=True)) or ""
        if "КТРУ" not in header_text and "позиц" not in header_text.lower():
            continue
        headers = [_clean(th.get_text(" ", strip=True)) or "" for th in header_row.find_all(["th", "td"])]
        col_index = {h: i for i, h in enumerate(headers)}

        def col(row_cells: list[Tag], names: list[str]) -> str | None:
            for name in names:
                idx = next(
                    (i for h, i in col_index.items() if h and name.lower() in h.lower()),
                    None,
                )
                if idx is not None and idx < len(row_cells):
                    return _clean(row_cells[idx].get_text(" ", strip=True))
            return None

        for row in table.find_all("tr")[1:]:
            # 9a-fixes-3 #3: пропускаем expander-сёстры (`<tr class="truInfo_…">`).
            # Они принадлежат позиции выше и обрабатываются в
            # `_collect_raw_position_attrs(row)`. Без явного skip парсер
            # может ошибочно засчитать содержимое expander'а как новую
            # позицию (если внутри есть длинный текст).
            row_classes = " ".join(row.get("class", []) or [])
            if "truInfo_" in row_classes:
                continue
            cells = row.find_all(["td"])
            if not cells or len(cells) < 3:
                continue
            row_text = _clean(row.get_text(" ", strip=True)) or ""
            if not row_text or not _is_position_row(row_text):
                continue
            ktru = None
            ktru_match = KTRU_CODE_RE.search(row_text)
            if ktru_match:
                ktru = ktru_match.group(0)
            name = col(cells, ["Наименование", "Товар"])
            qty_raw = col(cells, ["Количество"])
            unit = col(cells, ["Единица измерения", "Ед. изм"])
            # zakupki в реальной разметке использует «Цена за ед., ₽»; «Цена за единицу»
            # встречается только в синтетических примерах. Подстрока «Цена за ед» покрывает оба.
            price_raw = col(cells, ["Цена за ед", "Цена единицы"])
            qty = _parse_decimal(qty_raw) or Decimal("1")
            nmck_per_unit = _parse_decimal(price_raw)

            if ktru is None and not _is_meaningful_position_name(name):
                continue
            # Доп.фильтр от «мусорных» строк — единиц измерения из вложенных таблиц
            # характеристик («Мегабайт в секунду», «Ампер-час», «Градус Цельсия» и т.п.):
            # настоящие позиции всегда имеют либо КТРУ, либо распарсенные qty/price.
            if ktru is None and qty_raw is None and price_raw is None:
                continue

            key = f"{ktru or ''}|{name or ''}|{qty_raw or ''}|{price_raw or ''}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            position_idx += 1
            raw_attrs = _collect_raw_position_attrs(row)
            normalized, unknown = normalize_attrs(raw_attrs) if raw_attrs else ({}, [])
            unknown_attr_keys.update(unknown)
            # 9a-fixes-3 #3: дополняем name атрибутами из expander'ов,
            # чтобы «Полный текст требования» в карточке лота отражал
            # полный набор характеристик, а не только короткую колонку
            # «Наименование товара».
            extended_name = _extend_name_with_raw_attrs(name, raw_attrs)
            items.append(
                TenderItem(
                    position_num=position_idx,
                    ktru_code=ktru,
                    name=extended_name,
                    qty=qty,
                    unit=unit,
                    nmck_per_unit=nmck_per_unit,
                    required_attrs_jsonb=normalized,
                )
            )

    if unknown_attr_keys:
        # Однократный сигнал за прогон одной карточки: какие zakupki-ключи мы
        # увидели в expander-таблицах, но ни одного из 9 schema-атрибутов в них
        # нет, и они не в списке «известно-не-схемных». Если такой список
        # стабильно содержит одни и те же ключи — повод расширить schema.
        logger.info(
            "card_parser: unknown attribute keys (not mapped to schema): {}",
            sorted(unknown_attr_keys),
        )

    if items:
        return items

    # Fallback: card-block layout (no <table>, divs with sections per position)
    blocks = soup.find_all(class_=re.compile(r"(blockInfo|product__row|tableBlock)"))
    for block in blocks:
        block_text = _clean(block.get_text(" ", strip=True)) or ""
        if "КТРУ" not in block_text:
            continue
        ktru_match = KTRU_CODE_RE.search(block_text)
        ktru = ktru_match.group(0) if ktru_match else None
        name = _section_text(block, "Наименование товара")
        qty_text = _section_text(block, "Количество")
        unit = _section_text(block, "Единица измерения")
        price_text = _section_text(block, "Цена за единицу") or _section_text(block, "Цена за ед.")
        qty = _parse_decimal(qty_text) or Decimal("1")
        nmck_per_unit = _parse_decimal(price_text)
        if ktru is None and name is None and nmck_per_unit is None:
            continue
        position_idx += 1
        raw_attrs = _extract_position_attrs(block)
        normalized, unknown = normalize_attrs(raw_attrs) if raw_attrs else ({}, [])
        unknown_attr_keys.update(unknown)
        items.append(
            TenderItem(
                position_num=position_idx,
                ktru_code=ktru,
                name=name,
                qty=qty,
                unit=unit,
                nmck_per_unit=nmck_per_unit,
                required_attrs_jsonb=normalized,
            )
        )
    return items


_POSITION_NAME_GARBAGE = {
    "наименование характеристики",
    "единица измерения характеристики",
    "значение характеристики",
    "штука", "шт", "ватт", "герц", "секунда", "минута",
    "дюйм", "дюйм (25,4 мм)", "грамм", "килограмм", "литр",
    "наименование", "характеристики",
}


def _is_meaningful_position_name(name: str | None) -> bool:
    if not name:
        return False
    n = name.strip().lower()
    if n in _POSITION_NAME_GARBAGE:
        return False
    if len(n) < 12:
        return False
    if len(n.split()) < 2:
        return False
    return True


def _is_position_row(row_text: str) -> bool:
    """Heuristic: a real position row contains either a KTRU code, or substantial product
    description text. Pure attribute rows (single label or unit) are rejected."""
    if KTRU_CODE_RE.search(row_text):
        return True
    cleaned = row_text.strip()
    if len(cleaned) < 30:
        return False
    return True


_TRU_INFO_ID_RE = re.compile(r"truInfo_(\d+)")


def _collect_raw_position_attrs(row: Tag) -> dict[str, Any]:
    """Собирает сырые пары «характеристика → значение(я)» для позиции.

    На zakupki характеристики позиции лежат в `<tr class="truInfo_NNN"
    style="display:none">`-сёстрах, идущих сразу за визуальной строкой
    позиции. У одной позиции может быть 1-4 expander-tr (BS4 + lxml
    раскладывают характеристики между ними по-разному в зависимости от
    того, насколько кривой исходный HTML). Логика:

    1. Из `<span class="chevronRight">` достаём `truInfo_NNN`-id.
    2. Идём по next_siblings и собираем ВСЕ <tr>, у которых класс
       содержит `truInfo_NNN` с тем же id.
    3. Останавливаемся, когда встречаем:
       - `<tr>` с другим `truInfo_NNN` (expander соседней позиции), или
       - `<tr>` с `<span class="chevronRight">` (визуальная строка
         следующей позиции).
       Промежуточные «обычные» <tr> без класса (служебные/спейсер) —
       пропускаем, чтобы не оборвать сбор раньше времени (9a-fixes-3 #3).
    4. Если chevron не нашёлся (старый layout / мини-фикстура) —
       фолбэк на сам `row`.

    Возвращает dict с ключом-zakupki и значением либо строкой (один
    rowspan), либо list[str] (несколько rowspan-значений, как у «Способ
    подключения USB+LAN»). Нормализатор `normalize_attrs` обрабатывает
    оба варианта."""
    chevron = row.find("span", class_=re.compile(r"chevronRight"))
    onclick = chevron.get("onclick") if chevron else None
    tru_id = None
    if onclick:
        m = _TRU_INFO_ID_RE.search(onclick)
        if m:
            tru_id = m.group(1)

    expanders: list[Tag] = []
    if tru_id:
        target = re.compile(rf"\btruInfo_{tru_id}\b")
        for sib in row.find_next_siblings("tr"):
            cls_str = " ".join(sib.get("class", []) or [])
            if target.search(cls_str):
                expanders.append(sib)
                continue
            if "truInfo_" in cls_str:
                # Чужой expander (соседняя позиция) — стоп.
                break
            if sib.find("span", class_=re.compile(r"chevronRight")):
                # Визуальная строка следующей позиции — стоп.
                break
            # Иначе: служебный/спейсер <tr> между expander'ами одной
            # позиции — пропускаем дальше.
    if not expanders:
        # Фолбэк: ищем характеристики прямо внутри row (старый layout
        # из синтетических примеров и тех тендеров, где expander не
        # отделён в отдельный <tr>).
        return _extract_position_attrs(row)

    merged: dict[str, Any] = {}
    for exp in expanders:
        part = _extract_position_attrs(exp)
        for k, v in part.items():
            if k in merged:
                # Несколько вхождений одного ключа в разных expander'ах —
                # склеиваем в список.
                existing = merged[k]
                existing_list = existing if isinstance(existing, list) else [existing]
                new_list = v if isinstance(v, list) else [v]
                merged[k] = existing_list + new_list
            else:
                merged[k] = v
    return merged


def _extend_name_with_raw_attrs(name: str | None, raw_attrs: dict[str, Any] | None) -> str | None:
    """Дописывает к `name` пары «характеристика: значение» из `raw_attrs`,
    если они ещё не содержатся в name. Используется, чтобы `<details>`
    «Полный текст требования» в карточке лота показывал полный набор
    характеристик из expander'а zakupki, а не только короткое название
    из колонки «Наименование товара» (9a-fixes-3 #3)."""
    if not name or not raw_attrs:
        return name
    name_lower = name.lower()
    parts: list[str] = []
    for k, v in raw_attrs.items():
        if not k:
            continue
        if isinstance(v, list):
            v_str = "+".join(str(x).strip() for x in v if x is not None and str(x).strip())
        else:
            v_str = str(v).strip() if v is not None else ""
        if not v_str:
            continue
        # Не дублируем: если значение уже встречается в name — пропускаем.
        # Ключ-«заголовок» проверяем менее строго (он часто короткий
        # и может случайно встретиться как подстрока).
        if v_str.lower() in name_lower:
            continue
        parts.append(f"{k}: {v_str}")
    if not parts:
        return name
    return name + "\n" + "\n".join(parts)


def _extract_position_attrs(scope: Tag) -> dict[str, Any]:
    """Извлекает пары «характеристика → значение(я)» из таблицы характеристик
    внутри `scope`. `scope` — это либо expander-`<tr class="truInfo_…">`,
    либо (для legacy-фолбэка) сам position-row.

    Поддерживает rowspan>1 (например, «Способ подключения» с rowspan=2 —
    значения USB и LAN на двух соседних строках): такие значения
    собираются в list[str]. Один rowspan возвращается как str."""
    attrs: dict[str, list[str]] = {}
    for table in scope.find_all("table"):
        head = table.find("tr")
        if not head:
            continue
        head_text = (_clean(head.get_text(" ", strip=True)) or "").lower()
        if "характеристик" not in head_text and "значение" not in head_text:
            continue
        last_key: str | None = None
        for row in table.find_all("tr")[1:]:
            cells = [_clean(c.get_text(" ", strip=True)) or "" for c in row.find_all(["td", "th"])]
            if len(cells) >= 2 and cells[0]:
                key = cells[0]
                value = cells[1]
                if value:
                    attrs.setdefault(key, []).append(value)
                last_key = key
            elif len(cells) == 1 and last_key and cells[0]:
                # Продолжение rowspan-блока: предыдущая строка указала
                # ключ через rowspan, в этой строке только значение.
                attrs.setdefault(last_key, []).append(cells[0])
    out: dict[str, Any] = {}
    for k, vs in attrs.items():
        if len(vs) == 1:
            out[k] = vs[0]
        else:
            out[k] = vs
    return out


def parse_card(reg_number: str, url: str, html: str) -> TenderCard:
    soup = BeautifulSoup(html, "lxml")
    card = TenderCard(reg_number=reg_number, url=url, raw_html=html)

    customer = _first_org_like(
        soup,
        labels=(
            "Полное наименование заказчика",
            "Наименование заказчика",
            "Полное наименование организации",
            "Организация, осуществляющая размещение",
            "Заказчик",
        ),
    )
    card.customer = customer

    location = _section_text(soup, "Место нахождения") or _section_text(soup, "Почтовый адрес")
    card.customer_region = _extract_region(location)

    card.customer_contacts_jsonb = _extract_contacts(soup)

    nmck_text = (
        _section_text(soup, "Начальная (максимальная) цена контракта")
        or _section_text(soup, "Начальная цена контракта")
        or _section_text(soup, "НМЦК")
    )
    card.nmck_total = _parse_decimal(nmck_text)

    card.publish_date = _parse_dt(_section_text(soup, "Размещено")) or _parse_dt(
        _section_text(soup, "Дата размещения")
    )
    card.submit_deadline = (
        _parse_dt(_section_text(soup, "Дата и время окончания срока подачи заявок"))
        or _parse_dt(_section_text(soup, "окончания срока подачи"))
    )
    card.delivery_deadline = (
        _parse_dt(_section_text(soup, "Срок исполнения контракта"))
        or _parse_dt(_section_text(soup, "Дата окончания исполнения"))
    )

    card.items = _parse_items(soup)
    card.ktru_codes = sorted({i.ktru_code for i in card.items if i.ktru_code} | set(_extract_ktru_codes(soup)))

    if card.nmck_total is None or not card.customer:
        logger.warning("card {} parsed with gaps: nmck={} customer={}", reg_number, card.nmck_total, card.customer)
    return card
