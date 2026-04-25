# Сервис управления компонентами для /admin/components (этап 9А.2).
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

def list_components(
    session: Session,
    *,
    category: str | None,         # 'cpu' / 'motherboard' / ... / None=все
    search: str = "",             # ILIKE по model / manufacturer / sku / gtin
    only_skeletons: bool = False,
    only_hidden: bool = False,
    page: int = 1,
    per_page: int = 30,
) -> dict:
    """Постраничный список компонентов с фильтрами.

    Возвращает {items: [...], total: int, page, per_page, total_pages}.
    Каждый item: {category, id, model, manufacturer, sku, gtin, is_hidden,
                  is_skeleton, has_price (bool)}.
    """
    page = max(1, int(page))
    per_page = max(5, min(int(per_page), 200))

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
        # has_price — есть ли хотя бы одна позиция в supplier_prices с stock>0
        # или transit>0; считаем простым EXISTS
        sub_sql = (
            f"SELECT '{cat}' AS category, "
            f"       c.id, c.model, c.manufacturer, c.sku, c.gtin, "
            f"       c.is_hidden, "
            f"       {skel_expr} AS is_skeleton, "
            f"       EXISTS (SELECT 1 FROM supplier_prices sp "
            f"               WHERE sp.category = '{cat}' AND sp.component_id = c.id "
            f"               AND (sp.stock_qty > 0 OR sp.transit_qty > 0)) AS has_price "
            f"FROM {table} c "
            "WHERE 1=1 "
        )
        if search:
            sub_sql += f"AND {where_search} "
        if only_skeletons:
            sub_sql += f"AND {skel_expr} "
        if only_hidden:
            sub_sql += "AND c.is_hidden = TRUE "
        union_parts.append(sub_sql)

    union_sql = " UNION ALL ".join(f"({p})" for p in union_parts)
    # Сортировка: сначала скелеты+скрытые сверху, потом по category, model
    order_by = (
        " ORDER BY is_skeleton DESC, is_hidden DESC, category ASC, model ASC "
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
        items.append({
            "category":     r["category"],
            "category_label": CATEGORY_LABELS.get(r["category"], r["category"]),
            "id":           int(r["id"]),
            "model":        r["model"],
            "manufacturer": r["manufacturer"],
            "sku":          r["sku"],
            "gtin":         r["gtin"],
            "is_hidden":    bool(r["is_hidden"]),
            "is_skeleton":  bool(r["is_skeleton"]),
            "has_price":    bool(r["has_price"]),
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
