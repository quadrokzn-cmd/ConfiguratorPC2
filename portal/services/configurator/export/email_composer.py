# Сборка черновиков писем поставщикам по позициям проекта (этап 8.3).
#
# Идея: менеджер выбрал несколько конфигураций — они лежат в
# specification_items; в их queries.build_result_json содержится
# список компонентов выбранного варианта. Мы хотим написать каждому
# поставщику письмо только про те позиции, где он — самый дешёвый.
#
# Алгоритм:
#   1. Для проекта берём spec_items и соответствующие build_result_json.
#   2. Для выбранного варианта собираем все компоненты варианта; их
#      количество = quantity_in_variant * quantity_of_configs. Одинаковые
#      (category, component_id) в разных конфигурациях суммируются.
#   3. По каждой позиции ищем самого дешёвого поставщика (price_usd,
#      полученная конвертацией supplier_prices.price+currency через курс ЦБ
#      на сегодня; тайбрейк по min(supplier_id)).
#   4. Группируем позиции по победителю-поставщику.
#   5. Из этих групп собираем SupplierEmailDraft: subject = имя проекта,
#      body_html = приветствие + таблица 3 колонки (артикул/название/кол-во)
#      + подпись. Поставщики без email попадают в drafts с to_email=None —
#      UI их покажет, но заблокирует кнопку отправки.

from __future__ import annotations

import html
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from portal.services.configurator import spec_service
from portal.services.configurator.export import exchange_rate


# ---------------------------------------------------------------------
# Публичный dataclass
# ---------------------------------------------------------------------

@dataclass
class SupplierEmailDraft:
    """Черновик письма одному поставщику по итогам анализа проекта."""
    supplier_id:   int
    supplier_name: str
    to_email:      str | None          # None — email у поставщика не задан
    subject:       str
    body_html:     str
    items_count:   int


# ---------------------------------------------------------------------
# Вспомогательные выборки БД
# ---------------------------------------------------------------------

def _load_project_name(db: Session, project_id: int) -> str:
    """Имя проекта — используется как заголовок письма.

    Если проекта нет (вызов мимо роутера/прав доступа), вернём
    пустую строку — решение о 404 принимает вызывающий уровень.
    """
    row = db.execute(
        text("SELECT name FROM projects WHERE id = :pid"),
        {"pid": project_id},
    ).first()
    return (row.name if row else "") or ""


def _load_winners(
    db: Session,
    keys: list[tuple[str, int]],
    usd_rub_rate: float,
) -> dict[tuple[str, int], dict]:
    """Для каждого (category, component_id) находит самое дешёвое в USD
    предложение среди supplier_prices. Возвращает словарь:
       (category, component_id) -> {
           'supplier_id', 'supplier_name', 'supplier_email',
           'supplier_sku', 'price_usd'
       }

    Тайбрейк по меньшему supplier_id — фиксирует поведение для тестов
    и для случая совсем одинаковых цен (формально редкий, но бывает).
    """
    if not keys:
        return {}

    # Вариант без CROSS JOIN / DISTINCT ON — максимально простой SELECT
    # с последующей обработкой в Python: объёмы маленькие (≤ сотни строк
    # на один проект), усложнять SQL ради микро-оптимизации не надо.
    cats = sorted({k[0] for k in keys})
    cids = sorted({k[1] for k in keys})
    rows = db.execute(
        text(
            # 9А.2: s.is_active = TRUE — не предлагаем письмо отключённому
            # поставщику. Если все поставщики позиции деактивированы, она
            # выпадет из черновика как «нет цены», что и нужно.
            "SELECT sp.category, sp.component_id, sp.supplier_id, "
            "       sp.supplier_sku, sp.price, sp.currency, "
            "       s.name AS supplier_name, s.email AS supplier_email "
            "FROM supplier_prices sp "
            "JOIN suppliers s ON s.id = sp.supplier_id "
            "WHERE sp.category = ANY(:cats) "
            "  AND sp.component_id = ANY(:cids) "
            "  AND s.is_active = TRUE "
            "  AND sp.price IS NOT NULL"
        ),
        {"cats": cats, "cids": cids},
    ).mappings().all()

    # Группируем по (cat, cid); в каждой группе выбираем минимум по price_usd.
    needed = set(keys)
    by_key: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for r in rows:
        k = (r["category"], int(r["component_id"]))
        if k not in needed:
            continue
        price_usd = _to_usd(r["price"], r["currency"], usd_rub_rate)
        by_key[k].append({
            "supplier_id":    int(r["supplier_id"]),
            "supplier_name":  r["supplier_name"],
            "supplier_email": r["supplier_email"],
            "supplier_sku":   r["supplier_sku"],
            "price_usd":      price_usd,
        })

    winners: dict[tuple[str, int], dict] = {}
    for k, offers in by_key.items():
        # Сортируем по (price_usd ASC, supplier_id ASC) — тайбрейк.
        offers.sort(key=lambda o: (o["price_usd"], o["supplier_id"]))
        winners[k] = offers[0]
    return winners


def _to_usd(price, currency: str | None, usd_rub: float) -> float:
    """Конвертирует (price, currency) в USD. Неизвестная валюта = RUB."""
    p = float(price)
    if (currency or "").upper() == "USD":
        return p
    if usd_rub <= 0:
        # Защита от деления на 0 при неадекватном курсе — сделаем цену
        # стабильной и бесконечно большой, чтобы такая строка не победила.
        return float("inf")
    return p / float(usd_rub)


# ---------------------------------------------------------------------
# Агрегация позиций проекта
# ---------------------------------------------------------------------

def _iter_components_of_project(
    db: Session,
    project_id: int,
) -> list[dict]:
    """Разворачивает spec_items проекта в плоский список компонентов.

    Каждый возвращаемый dict:
      {category, component_id, brand, model, sku, quantity}
    где quantity = qty_компонента_в_варианте × qty_конфигурации_в_проекте.
    Дубликатов (category, component_id) не склеивает — это делает вызывающий.
    """
    spec_items = spec_service.list_spec_items(db, project_id=project_id)
    if not spec_items:
        return []

    query_ids = sorted({int(it["query_id"]) for it in spec_items})
    rows = db.execute(
        text("SELECT id, build_result_json FROM queries WHERE id = ANY(:ids)"),
        {"ids": query_ids},
    ).all()
    build_results = {int(r.id): r.build_result_json for r in rows}

    out: list[dict] = []
    for item in spec_items:
        spec_qty = int(item.get("quantity") or 1)
        br = build_results.get(int(item["query_id"]))
        if not br:
            continue
        mfg = (item.get("variant_manufacturer") or "").lower()
        # Вытаскиваем из build_result сырой список компонентов нужного варианта.
        # Берём raw-список (а не _prepare_variants), чтобы не терять повторные
        # storage-строки: одна конфигурация может содержать несколько накопителей,
        # которые должны попасть в письмо как отдельные позиции.
        variant = next(
            (v for v in (br.get("variants") or [])
             if (v.get("manufacturer") or "").lower() == mfg),
            None,
        )
        if variant is None:
            continue
        for comp in (variant.get("components") or []):
            cat = comp.get("category")
            cid = comp.get("component_id")
            if not cat or cid is None:
                continue
            per_qty = int(comp.get("quantity") or 1)
            out.append({
                "category":     cat,
                "component_id": int(cid),
                "brand":        (comp.get("manufacturer") or "").strip(),
                "model":        (comp.get("model") or "").strip(),
                "sku":          comp.get("sku"),
                "quantity":     per_qty * spec_qty,
            })
    return out


def _aggregate(components: list[dict]) -> list[dict]:
    """Складывает quantity для одинаковых (category, component_id).

    Берёт brand/model/sku из первого встреченного экземпляра — для задач
    письма все одинаковые component_id физически являются одним товаром
    и различия в подписи не случаются.
    """
    agg: dict[tuple[str, int], dict] = {}
    for c in components:
        key = (c["category"], c["component_id"])
        existing = agg.get(key)
        if existing is None:
            agg[key] = {
                "category":     c["category"],
                "component_id": c["component_id"],
                "brand":        c["brand"],
                "model":        c["model"],
                "sku":          c["sku"],
                "quantity":     c["quantity"],
            }
        else:
            existing["quantity"] += c["quantity"]
    return list(agg.values())


# ---------------------------------------------------------------------
# HTML-рендер письма
# ---------------------------------------------------------------------

# Подпись, которую видит поставщик под таблицей. Вынесена как константа,
# чтобы в тестах её можно было сверить точно.
_SIGNATURE_HTML = (
    '<p>&nbsp;</p>\n'
    '<p>С уважением,<br>\n'
    'ООО "КВАДРО-ТЕХ"<br>\n'
    '<a href="https://www.quadro.tatar">www.quadro.tatar</a><br>\n'
    '420129, г. Казань, ул. С.Батыева, д.13, оф.1017<br>\n'
    'тел: 8 (843) 239-26-95</p>'
)


def _compose_title(brand: str, model: str) -> str:
    """Собирает наименование позиции для письма: «{brand} {model}».

    Если один из кусков пустой — используем тот, что есть; если оба пусты —
    возвращаем прочерк, чтобы таблица никогда не падала.
    """
    parts = [p for p in (brand, model) if p]
    return " ".join(parts) if parts else "—"


def _render_body_html(rows: list[dict]) -> str:
    """Собирает HTML-тело письма: приветствие + таблица + подпись.

    rows — список {'article', 'title', 'qty'}. article и title перед
    вставкой в HTML экранируются html.escape — поставщики иногда кладут
    в SKU ломаные символы, а в model встречаются кавычки.
    """
    body_rows: list[str] = []
    for r in rows:
        article = html.escape(r["article"] or "")
        title = html.escape(r["title"] or "")
        qty = int(r["qty"])
        body_rows.append(
            f'    <tr><td>{article}</td><td>{title}</td>'
            f'<td style="text-align:center;">{qty}</td></tr>'
        )
    rows_html = "\n".join(body_rows) if body_rows else (
        '    <tr><td colspan="3" style="text-align:center;">—</td></tr>'
    )
    return (
        '<p>Привет!</p>\n'
        '<p>Подскажи по наличию и возможной скидке пожалуйста:</p>\n'
        '<table border="1" cellpadding="6" cellspacing="0" '
        'style="border-collapse:collapse;font-family:Arial,sans-serif;font-size:13px;">\n'
        '  <thead>\n'
        '    <tr style="background:#f0f0f0;">\n'
        '      <th>Артикул</th><th>Наименование</th><th>Кол-во</th>\n'
        '    </tr>\n'
        '  </thead>\n'
        '  <tbody>\n'
        f'{rows_html}\n'
        '  </tbody>\n'
        '</table>\n'
        f'{_SIGNATURE_HTML}'
    )


# ---------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------

def build_supplier_emails(
    project_id: int,
    db: Session,
) -> list[SupplierEmailDraft]:
    """Собирает черновики писем по всем поставщикам, у которых есть хотя
    бы одна позиция-победитель в данном проекте.

    Возвращает список SupplierEmailDraft, отсортированный по имени поставщика
    (чтобы UI стабильно рендерил табы). Поставщики без email попадают в
    список с to_email=None — модалка UI покажет их, но заблокирует
    кнопку отправки.
    """
    project_name = _load_project_name(db, project_id)
    subject = project_name

    components = _aggregate(_iter_components_of_project(db, project_id))
    if not components:
        return []

    # Получаем курс ЦБ для конвертации price -> USD. Если curs source=cache —
    # нас это устраивает; сеть не трогаем, если есть запись.
    rate, _date, _source = exchange_rate.get_usd_rate()
    usd_rub = float(rate)

    keys = [(c["category"], c["component_id"]) for c in components]
    winners = _load_winners(db, keys, usd_rub)

    # Группируем позиции по победителю.
    by_supplier: dict[int, dict[str, Any]] = {}
    for comp in components:
        key = (comp["category"], comp["component_id"])
        win = winners.get(key)
        if win is None:
            # Нет ни одного supplier_prices с price — позицию не у кого
            # спрашивать, молча пропускаем (в UI это видно по отсутствию
            # суммы в Excel-экспорте — отдельно для email-флоу не алертим).
            continue
        sid = win["supplier_id"]
        bucket = by_supplier.setdefault(sid, {
            "supplier_id":    sid,
            "supplier_name":  win["supplier_name"],
            "supplier_email": win["supplier_email"],
            "rows":           [],
        })
        article = win["supplier_sku"] or comp["sku"] or ""
        title = _compose_title(comp["brand"], comp["model"])
        bucket["rows"].append({
            "article": article,
            "title":   title,
            "qty":     comp["quantity"],
        })

    drafts: list[SupplierEmailDraft] = []
    for sid, data in by_supplier.items():
        # Сортируем строки внутри письма — стабильно для тестов и чтения:
        # сначала по наименованию, потом по артикулу.
        data["rows"].sort(key=lambda r: (r["title"], r["article"]))
        body_html = _render_body_html(data["rows"])
        drafts.append(SupplierEmailDraft(
            supplier_id=sid,
            supplier_name=data["supplier_name"],
            to_email=data["supplier_email"],
            subject=subject,
            body_html=body_html,
            items_count=len(data["rows"]),
        ))

    # Стабильный порядок табов в UI и в JSON-ответе /preview.
    drafts.sort(key=lambda d: d.supplier_name.lower())
    return drafts
