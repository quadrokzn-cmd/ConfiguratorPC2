# Сервис управления компонентами раздела «Базы данных» портала
# (UI-лейбл «Комплектующие для ПК»). Изначально создан на этапе 9А.2
# как app/services/component_service.py для /admin/components в
# конфигураторе. На этапе UI-2 Пути B (2026-05-11) переехал сюда
# вместе со страницами /databases/components.
#
# Закрывает технический долг обогащения:
#   - cooler.max_tdp_watts (228 cooler-скелетов остались не обогащёнными
#     с офсайтов — нужно ручное проставление);
#   - 4 Netac USB-C SSD не подходят под схему — кандидаты на is_hidden=True;
#   - оставшиеся NULL-значения в gpu/case/cooler.
#
# Список компонентов отдаётся постранично с тремя фильтрами (категория,
# поиск, статус: все/скелеты/скрытые); детальная карточка позволяет
# редактировать характеристики и переключать is_hidden.
#
# Безопасность SQL: имена таблиц и редактируемых полей всегда выбираются
# из белого списка (ALLOWED_TABLES + EDITABLE_FIELDS), параметры всегда
# bind-переменные.

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.enrichment.base import ALLOWED_TABLES, CATEGORY_TO_TABLE


# --- Редактируемые характеристики по категориям -------------------------
#
# Тип определяет UI-инпут:
#   'int'    — number, целое
#   'float'  — number, дробное
#   'str'    — text
#   'bool'   — toggle (галочка)
#   'array'  — text "comma,separated" (PostgreSQL TEXT[])
#
# Поля model/manufacturer/sku/gtin не редактируются: они идут от парсеров
# прайсов и используются для матчинга — менять руками опасно.

EDITABLE_FIELDS: dict[str, list[tuple[str, str, str]]] = {
    "cpu": [
        ("socket",                  "Сокет",                   "str"),
        ("cores",                   "Ядер",                    "int"),
        ("threads",                 "Потоков",                 "int"),
        ("base_clock_ghz",          "Базовая частота, ГГц",    "float"),
        ("turbo_clock_ghz",         "Турбо-частота, ГГц",      "float"),
        ("tdp_watts",               "TDP, Вт",                 "int"),
        ("has_integrated_graphics", "Есть iGPU",               "bool"),
        ("memory_type",             "Тип памяти",              "str"),
        ("package_type",            "Тип упаковки (BOX/OEM)",  "str"),
        ("l3_cache_mb",             "L3-кэш, МБ",              "int"),
        ("max_memory_freq",         "Макс. частота памяти",    "int"),
        ("release_year",            "Год выпуска",             "int"),
    ],
    "motherboard": [
        ("socket",          "Сокет",            "str"),
        ("chipset",         "Чипсет",           "str"),
        ("form_factor",     "Форм-фактор",      "str"),
        ("memory_type",     "Тип памяти",       "str"),
        ("memory_slots",    "Слотов памяти",    "int"),
        ("max_memory_gb",   "Макс. память, ГБ", "int"),
        ("max_memory_freq", "Макс. частота",    "int"),
        ("sata_ports",      "SATA-портов",      "int"),
        ("m2_slots",        "M.2-слотов",       "int"),
        ("has_m2_slot",     "Есть M.2",         "bool"),
        ("has_wifi",        "Wi-Fi",            "bool"),
        ("has_bluetooth",   "Bluetooth",        "bool"),
        ("pcie_version",    "PCIe-версия",      "str"),
        ("pcie_x16_slots",  "Слотов PCIe x16",  "int"),
        ("usb_ports",       "USB-портов",       "int"),
    ],
    "ram": [
        ("memory_type",    "Тип памяти",        "str"),
        ("form_factor",    "Форм-фактор",       "str"),
        ("module_size_gb", "Модуль, ГБ",        "int"),
        ("modules_count",  "В комплекте, шт",   "int"),
        ("frequency_mhz",  "Частота, МГц",      "int"),
        ("cl_timing",      "CL",                "int"),
        ("voltage",        "Напряжение, В",     "float"),
        ("has_heatsink",   "Радиатор",          "bool"),
        ("has_rgb",        "RGB",               "bool"),
    ],
    "gpu": [
        ("vram_gb",               "VRAM, ГБ",                "int"),
        ("vram_type",             "Тип VRAM",                "str"),
        ("tdp_watts",             "TDP, Вт",                 "int"),
        ("needs_extra_power",     "Нужно доп. питание",      "bool"),
        ("video_outputs",         "Видеовыходы",             "str"),
        ("core_clock_mhz",        "Частота ядра, МГц",       "int"),
        ("memory_clock_mhz",      "Частота памяти, МГц",     "int"),
        ("gpu_chip",              "GPU-чип",                 "str"),
        ("recommended_psu_watts", "Рекомендованный БП, Вт",  "int"),
        ("length_mm",             "Длина, мм",               "int"),
        ("height_mm",             "Высота, мм",              "int"),
        ("power_connectors",      "Разъёмы питания",         "str"),
        ("fans_count",            "Вентиляторов",            "int"),
    ],
    "storage": [
        ("storage_type",   "Тип (SSD/HDD)",   "str"),
        ("form_factor",    "Форм-фактор",     "str"),
        ("interface",      "Интерфейс",       "str"),
        ("capacity_gb",    "Объём, ГБ",       "int"),
        ("read_speed_mb",  "Чтение, МБ/с",    "int"),
        ("write_speed_mb", "Запись, МБ/с",    "int"),
        ("tbw",            "TBW",             "int"),
        ("rpm",            "RPM (HDD)",       "int"),
        ("cache_mb",       "Кэш, МБ",         "int"),
    ],
    "case": [
        ("supported_form_factors", "Поддерживаемые форм-факторы", "array"),
        ("has_psu_included",       "БП в комплекте",              "bool"),
        ("included_psu_watts",     "Мощность встроенного БП, Вт", "int"),
        ("max_gpu_length_mm",      "Макс. длина GPU, мм",         "int"),
        ("max_cooler_height_mm",   "Макс. высота кулера, мм",     "int"),
        ("psu_form_factor",        "Форм-фактор БП",              "str"),
        ("color",                  "Цвет",                        "str"),
        ("material",               "Материал",                    "str"),
        ("drive_bays",             "Отсеков под диски",           "int"),
        ("fans_included",          "Вентиляторов в комплекте",    "int"),
        ("has_glass_panel",        "Стеклянная панель",           "bool"),
        ("has_rgb",                "RGB",                         "bool"),
    ],
    "psu": [
        ("power_watts",          "Мощность, Вт",        "int"),
        ("form_factor",          "Форм-фактор",         "str"),
        ("efficiency_rating",    "Сертификация",        "str"),
        ("modularity",           "Модульность",         "str"),
        ("has_12vhpwr",          "12VHPWR",             "bool"),
        ("sata_connectors",      "SATA-разъёмов",       "int"),
        ("main_cable_length_mm", "Длина шнура 24-pin",  "int"),
        ("warranty_years",       "Гарантия, лет",       "int"),
    ],
    "cooler": [
        ("supported_sockets", "Поддерживаемые сокеты", "array"),
        ("max_tdp_watts",     "Макс. TDP, Вт",         "int"),
        ("cooler_type",       "Тип (air/aio)",         "str"),
        ("height_mm",         "Высота, мм",            "int"),
        ("radiator_size_mm",  "Размер радиатора, мм",  "int"),
        ("fans_count",        "Вентиляторов",          "int"),
        ("noise_db",          "Шум, дБ",               "float"),
        ("has_rgb",           "RGB",                   "bool"),
    ],
}

# Ключевые поля «без NULL» для статуса «полная карточка» по категории.
# Если хотя бы одно из этих полей NULL — это «скелет».
SKELETON_KEY_FIELDS: dict[str, list[str]] = {
    "cpu":         ["socket", "cores", "threads", "tdp_watts"],
    "motherboard": ["socket", "chipset", "form_factor"],
    "ram":         ["memory_type", "module_size_gb", "frequency_mhz"],
    "gpu":         ["vram_gb", "tdp_watts"],
    "storage":     ["storage_type", "capacity_gb"],
    "case":        ["supported_form_factors"],
    "psu":         ["power_watts"],
    "cooler":      ["supported_sockets", "max_tdp_watts"],
}

CATEGORY_LABELS: dict[str, str] = {
    "cpu":         "CPU",
    "motherboard": "Материнская плата",
    "ram":         "Оперативная память",
    "gpu":         "Видеокарта",
    "storage":     "Накопитель",
    "case":        "Корпус",
    "psu":         "Блок питания",
    "cooler":      "Кулер",
}


def _table_for(category: str) -> str:
    """Имя таблицы по category из whitelist; иначе ValueError."""
    table = CATEGORY_TO_TABLE.get(category)
    if not table or table not in ALLOWED_TABLES:
        raise ValueError(f"Неизвестная категория компонента: {category!r}")
    return table


def _editable_fields(category: str) -> list[tuple[str, str, str]]:
    fields = EDITABLE_FIELDS.get(category)
    if not fields:
        raise ValueError(f"Категория {category!r} не имеет редактируемой схемы")
    return fields


def _allowed_field_names(category: str) -> set[str]:
    return {name for name, _label, _t in _editable_fields(category)}


# --- Список ----------------------------------------------------------------

_SORT_COLUMNS = {
    "category":     "category",
    "manufacturer": "manufacturer",
    "model":        "model",
    "price":        "min_price_usd",
    "status":       "status_rank",
}


def _parse_sort(raw: str) -> tuple[str | None, str]:
    """Разбирает 'column,direction' → (column, 'asc'|'desc') либо (None, '').

    Невалидные значения молча игнорируются — возврат (None, ''), т.е.
    дефолтная сортировка."""
    if not raw:
        return None, ""
    parts = raw.split(",", 1)
    col = parts[0].strip().lower()
    direction = (parts[1].strip().lower() if len(parts) > 1 else "asc")
    if col not in _SORT_COLUMNS:
        return None, ""
    if direction not in ("asc", "desc"):
        direction = "asc"
    return col, direction


def list_components(
    session: Session,
    *,
    category: str | None,         # 'cpu' / 'motherboard' / ... / None=все
    search: str = "",             # ILIKE по model / manufacturer / sku / gtin
    only_skeletons: bool = False,
    only_hidden: bool = False,
    status: str = "",             # 9А.2.2: '', 'full', 'skeleton', 'hidden',
                                  # 'with_price', 'no_price'
    sort: str = "",               # 9А.2.2: '<column>,<asc|desc>'; пусто = дефолт
    page: int = 1,
    per_page: int = 30,
) -> dict:
    """Постраничный список компонентов с фильтрами.

    Возвращает {items: [...], total: int, page, per_page, total_pages}.
    Каждый item: {category, id, model, manufacturer, sku, gtin, is_hidden,
                  is_skeleton, has_price (bool)}.

    9А.2.2: единый параметр `status` (приоритетнее старых only_skeletons /
    only_hidden — если задан, перекрывает их). Параметр `sort` —
    'column,direction', где column ∈ {category, manufacturer, model,
    price, status}, direction ∈ {asc, desc}.
    """
    page = max(1, int(page))
    per_page = max(5, min(int(per_page), 200))

    # 9А.2.2: status имеет приоритет над старыми чекбоксами (если придут).
    status = (status or "").strip().lower()
    if status == "skeleton":
        only_skeletons = True
        only_hidden = False
    elif status == "hidden":
        only_skeletons = False
        only_hidden = True
    elif status == "full":
        only_skeletons = False
        only_hidden = False
    elif status in ("with_price", "no_price", "all", ""):
        pass  # обрабатываются ниже отдельно

    if category and category != "all":
        cats = [category]
    else:
        cats = list(EDITABLE_FIELDS.keys())

    items: list[dict] = []
    total = 0
    where_search = ""
    params: dict[str, Any] = {}
    if search:
        where_search = (
            "(c.model ILIKE :q OR c.manufacturer ILIKE :q "
            "OR c.sku ILIKE :q OR c.gtin ILIKE :q)"
        )
        params["q"] = f"%{search}%"

    union_parts: list[str] = []
    for cat in cats:
        table = _table_for(cat)
        skel_fields = SKELETON_KEY_FIELDS.get(cat) or []
        if skel_fields:
            skel_expr = (
                "(" + " OR ".join(f"c.{f} IS NULL" for f in skel_fields) + ")"
            )
        else:
            skel_expr = "FALSE"
        # supplier_count и min_price_usd: сколько активных поставщиков
        # отдают этот компонент (с stock>0 или transit>0) и какая
        # минимальная цена в USD среди них (RUB переводим по курсу 90).
        # supplier_min_name — имя поставщика с минимальной ценой
        # (для случая «$X у Merlion», когда поставщик ровно один или
        # хотим показать «от X у …»).
        # 9А.2.2: добавляем status_rank — числовой код статуса для
        # сортировки по столбцу «Статус». 0 = полная, 1 = скелет,
        # 2 = скрыт. По возрастанию: сначала полные, затем скелеты,
        # затем скрытые.
        sub_sql = (
            f"SELECT '{cat}' AS category, "
            f"       c.id, c.model, c.manufacturer, c.sku, c.gtin, "
            f"       c.is_hidden, "
            f"       {skel_expr} AS is_skeleton, "
            f"       CASE WHEN c.is_hidden THEN 2 "
            f"            WHEN {skel_expr} THEN 1 "
            f"            ELSE 0 END AS status_rank, "
            f"       COALESCE(p.supplier_count, 0) AS supplier_count, "
            f"       p.min_price_usd AS min_price_usd, "
            f"       p.supplier_min_name AS supplier_min_name "
            f"FROM {table} c "
            f"LEFT JOIN ( "
            f"    SELECT sp.component_id, "
            f"           COUNT(DISTINCT sp.supplier_id) AS supplier_count, "
            f"           MIN(CASE WHEN sp.currency = 'USD' THEN sp.price "
            f"                    ELSE sp.price / 90.0 END) AS min_price_usd, "
            f"           (ARRAY_AGG(s.name ORDER BY "
            f"                     CASE WHEN sp.currency = 'USD' THEN sp.price "
            f"                          ELSE sp.price / 90.0 END ASC))[1] AS supplier_min_name "
            f"    FROM supplier_prices sp "
            f"    JOIN suppliers s ON s.id = sp.supplier_id "
            f"    WHERE sp.category = '{cat}' "
            f"      AND s.is_active = TRUE "
            f"      AND (sp.stock_qty > 0 OR sp.transit_qty > 0) "
            f"    GROUP BY sp.component_id "
            f") p ON p.component_id = c.id "
            "WHERE 1=1 "
        )
        if search:
            sub_sql += f"AND {where_search} "
        if only_skeletons:
            sub_sql += f"AND {skel_expr} "
        if only_hidden:
            sub_sql += "AND c.is_hidden = TRUE "
        if status == "full":
            sub_sql += f"AND NOT {skel_expr} AND c.is_hidden = FALSE "
        elif status == "with_price":
            sub_sql += "AND COALESCE(p.supplier_count, 0) > 0 "
        elif status == "no_price":
            sub_sql += "AND COALESCE(p.supplier_count, 0) = 0 "
        union_parts.append(sub_sql)

    union_sql = " UNION ALL ".join(f"({p})" for p in union_parts)

    # 9А.2.2: сортировка по выбранной колонке.
    # По умолчанию: сначала скелеты+скрытые сверху, потом по category, model.
    sort_col, sort_dir = _parse_sort(sort)
    if sort_col is None:
        order_by = (
            " ORDER BY is_skeleton DESC, is_hidden DESC, "
            "          category ASC, model ASC "
        )
    else:
        col_sql = _SORT_COLUMNS[sort_col]
        dir_sql = "ASC" if sort_dir == "asc" else "DESC"
        # NULLS LAST для цен — компоненты без цены оказываются в конце
        # при сортировке asc (логичнее, чем сверху NULL'ы).
        nulls_clause = ""
        if sort_col == "price":
            nulls_clause = " NULLS LAST" if sort_dir == "asc" else " NULLS LAST"
        order_by = (
            f" ORDER BY {col_sql} {dir_sql}{nulls_clause}, "
            f"          category ASC, model ASC "
        )
    offset = (page - 1) * per_page
    paged_sql = (
        f"SELECT * FROM ({union_sql}) AS u "
        f"{order_by} "
        f"LIMIT {int(per_page)} OFFSET {int(offset)}"
    )
    count_sql = f"SELECT COUNT(*) FROM ({union_sql}) AS u"

    rows = session.execute(text(paged_sql), params).mappings().all()
    total = int(session.execute(text(count_sql), params).scalar() or 0)

    for r in rows:
        sup_count = int(r["supplier_count"] or 0)
        min_price = r["min_price_usd"]
        items.append({
            "category":          r["category"],
            "category_label":    CATEGORY_LABELS.get(r["category"], r["category"]),
            "id":                int(r["id"]),
            "model":              r["model"],
            "manufacturer":       r["manufacturer"],
            "sku":                r["sku"],
            "gtin":               r["gtin"],
            "is_hidden":          bool(r["is_hidden"]),
            "is_skeleton":        bool(r["is_skeleton"]),
            "has_price":          sup_count > 0,
            "supplier_count":     sup_count,
            "min_price_usd":      float(min_price) if min_price is not None else None,
            "supplier_min_name":  r["supplier_min_name"],
        })

    total_pages = max(1, (total + per_page - 1) // per_page)
    # Ключ "rows" вместо "items": в Jinja2 атрибут .items на dict-е
    # перетягивает встроенный метод dict.items() и шаблон падает с
    # «builtin_function_or_method object is not iterable». Имя rows
    # таких коллизий не даёт.
    return {
        "rows":        items,
        "total":       total,
        "page":        page,
        "per_page":    per_page,
        "total_pages": total_pages,
    }


# --- Детальная карточка ---------------------------------------------------

def list_supplier_prices_for_component(
    session: Session,
    *,
    category: str,
    component_id: int,
) -> list[dict]:
    """Все supplier_prices для компонента (включая неактивных поставщиков).

    Возвращает список dict-ов:
      {supplier_id, supplier_name, supplier_active, supplier_sku,
       price, currency, price_usd, price_rub, stock_qty, transit_qty,
       updated_at}.
    Активные поставщики идут сначала, дальше — по цене.
    """
    rows = session.execute(
        text(
            "SELECT s.id            AS supplier_id, "
            "       s.name          AS supplier_name, "
            "       s.is_active     AS supplier_active, "
            "       sp.supplier_sku AS supplier_sku, "
            "       sp.price        AS price, "
            "       sp.currency     AS currency, "
            "       sp.stock_qty    AS stock_qty, "
            "       sp.transit_qty  AS transit_qty, "
            "       sp.updated_at   AS updated_at "
            "FROM supplier_prices sp "
            "JOIN suppliers s ON s.id = sp.supplier_id "
            "WHERE sp.category = :cat AND sp.component_id = :cid "
            "ORDER BY s.is_active DESC, sp.price ASC"
        ),
        {"cat": category, "cid": int(component_id)},
    ).mappings().all()
    out: list[dict] = []
    for r in rows:
        price = float(r["price"]) if r["price"] is not None else 0.0
        currency = (r["currency"] or "RUB").upper()
        if currency == "USD":
            price_usd = price
            price_rub = price * 90.0
        else:
            price_rub = price
            price_usd = price / 90.0
        out.append({
            "supplier_id":     int(r["supplier_id"]),
            "supplier_name":   r["supplier_name"],
            "supplier_active": bool(r["supplier_active"]),
            "supplier_sku":    r["supplier_sku"],
            "price":           round(price, 2),
            "currency":        currency,
            "price_usd":       round(price_usd, 2),
            "price_rub":       round(price_rub, 2),
            "stock_qty":       int(r["stock_qty"] or 0),
            "transit_qty":     int(r["transit_qty"] or 0),
            "updated_at":      r["updated_at"],
        })
    return out


def get_component(
    session: Session,
    *,
    category: str,
    component_id: int,
) -> dict | None:
    """Один компонент со всеми полями. Возвращает dict с ключом 'fields'
    (список (name, label, type, value)) — готовый для рендеринга формы."""
    table = _table_for(category)
    fields = _editable_fields(category)

    cols = "id, model, manufacturer, sku, gtin, is_hidden, " + ", ".join(
        f"{name}" for name, _l, _t in fields
    )
    row = session.execute(
        text(f"SELECT {cols} FROM {table} WHERE id = :id"),
        {"id": int(component_id)},
    ).mappings().first()
    if row is None:
        return None

    values: list[dict] = []
    for name, label, ftype in fields:
        v = row.get(name)
        # Decimal → float для шаблона
        if isinstance(v, Decimal):
            v = float(v)
        values.append({
            "name": name, "label": label, "type": ftype, "value": v,
        })
    return {
        "category":       category,
        "category_label": CATEGORY_LABELS.get(category, category),
        "id":             int(row["id"]),
        "model":          row["model"],
        "manufacturer":   row["manufacturer"],
        "sku":            row["sku"],
        "gtin":           row["gtin"],
        "is_hidden":      bool(row["is_hidden"]),
        "fields":         values,
    }


# --- Обновление ----------------------------------------------------------

def _coerce_value(raw: str | None, ftype: str) -> Any:
    """Приведение строкового значения формы к нужному типу.
    Пустая строка → None (NULL в БД). 'on'/'off' для bool — для toggle/checkbox.
    """
    s = (raw or "").strip() if raw is not None else None
    if ftype == "bool":
        return raw == "on" or raw == "true" or raw == "1"
    if s is None or s == "":
        return None
    if ftype == "int":
        return int(float(s))
    if ftype == "float":
        return float(s.replace(",", "."))
    if ftype == "array":
        return [t.strip() for t in s.split(",") if t.strip()]
    # str
    return s


def update_component_fields(
    session: Session,
    *,
    category: str,
    component_id: int,
    raw_fields: dict[str, Any],
) -> bool:
    """Обновляет компонент. raw_fields — сырые значения из формы (str/None
    для скаляров, для bool — отсутствие ключа = False, любая строка = True).

    Только поля из EDITABLE_FIELDS попадут в UPDATE — остальные ключи
    raw_fields игнорируются (защита от попытки записать в model/sku).

    Возвращает True если запись была.
    """
    table = _table_for(category)
    schema = _editable_fields(category)
    set_parts: list[str] = []
    params: dict[str, Any] = {"id": int(component_id)}
    for name, _label, ftype in schema:
        if ftype == "bool":
            # Для bool отсутствие ключа в raw_fields — это False (галочка снята).
            value = (name in raw_fields and raw_fields[name] in ("on", "true", "1", True))
        else:
            value = _coerce_value(raw_fields.get(name), ftype)
        set_parts.append(f"{name} = :{name}")
        params[name] = value
    set_sql = ", ".join(set_parts)
    res = session.execute(
        text(f"UPDATE {table} SET {set_sql} WHERE id = :id"),
        params,
    )
    session.commit()
    return res.rowcount > 0


def toggle_hidden(
    session: Session,
    *,
    category: str,
    component_id: int,
) -> bool | None:
    """Переключает is_hidden. Возвращает новое значение (True/False) или None."""
    table = _table_for(category)
    r = session.execute(
        text(
            f"UPDATE {table} SET is_hidden = NOT is_hidden "
            f"WHERE id = :id RETURNING is_hidden"
        ),
        {"id": int(component_id)},
    ).first()
    session.commit()
    return bool(r.is_hidden) if r else None
