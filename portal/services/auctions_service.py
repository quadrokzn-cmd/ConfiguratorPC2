# Сервис-слой модуля «Аукционы» для портала (этап 9a слияния).
#
# Чистый text-SQL через SQLAlchemy. ORM не используется — это
# проектное правило C-PC2 (см. CLAUDE.md). Все функции возвращают
# готовые dict'ы и list[dict] под Jinja-шаблоны.
#
# Структура:
#   - read_settings/save_setting          — пороги/тумблеры из таблицы settings.
#   - list_excluded_regions/toggle_region — стоп-лист регионов.
#   - list_ktru_watchlist/...             — KTRU-зонтики.
#   - get_inbox_data                      — секционный inbox: срочно/ревью/работа/архив.
#   - get_lot_card                        — карточка одного лота: тендер + позиции
#                                            + matches (primary + alternative).
#   - update_status/update_contract/update_note
#                                          — переходы и правки tender_status.
#   - state-machine ALLOWED_TRANSITIONS    — допустимые переходы статусов.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.orm import Session


# --- State machine ------------------------------------------------------

# Допустимые переходы статусов лота. Карта направленная: from → набор to.
# Кроме «вперёд», разрешён шаг назад (in_review → new и т.п.) — менеджер
# должен иметь возможность откатить ошибочный клик. Терминальные
# (won/skipped) — финальные, обратных переходов нет.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "new":       frozenset({"in_review", "skipped"}),
    "in_review": frozenset({"new", "will_bid", "skipped"}),
    "will_bid":  frozenset({"in_review", "submitted", "skipped"}),
    "submitted": frozenset({"will_bid", "won", "skipped"}),
    "won":       frozenset(),  # терминальный
    "skipped":   frozenset(),  # терминальный
}

ACTIVE_STATUSES: frozenset[str] = frozenset({"new", "in_review", "will_bid", "submitted"})
ARCHIVE_STATUSES: frozenset[str] = frozenset({"won", "skipped"})

# Метки на русском для UI (бейджи статусов).
STATUS_LABELS: dict[str, str] = {
    "new":       "новый",
    "in_review": "в ревью",
    "will_bid":  "будем подавать",
    "submitted": "заявка подана",
    "won":       "выиграно",
    "skipped":   "пропущено",
}


def is_transition_allowed(from_status: str, to_status: str) -> bool:
    """True, если переход from→to допускается state-machine."""
    return to_status in ALLOWED_TRANSITIONS.get(from_status, frozenset())


# --- Settings -----------------------------------------------------------

# Канон ключей в таблице settings (есть в миграции 030, seed-зачения).
SETTING_KEYS: tuple[str, ...] = (
    "margin_threshold_pct",
    "nmck_min_rub",
    "max_price_per_unit_rub",
    "deadline_alert_hours",
    "contract_reminder_days",
    "auctions_ingest_enabled",
)

# Дефолты — те же, что в seed миграции 030/034.
SETTING_DEFAULTS: dict[str, str] = {
    "margin_threshold_pct":   "15",
    "nmck_min_rub":           "30000",
    "max_price_per_unit_rub": "300000",
    "deadline_alert_hours":   "24",
    "contract_reminder_days": "3",
    "auctions_ingest_enabled": "true",
}


def read_settings(db: Session) -> dict[str, str]:
    """Возвращает все известные настройки (с дефолтами для отсутствующих)."""
    rows = db.execute(text("SELECT key, value FROM settings")).all()
    out = dict(SETTING_DEFAULTS)
    for r in rows:
        if r.key in SETTING_DEFAULTS:
            out[r.key] = str(r.value)
    return out


def read_setting_int(db: Session, key: str, default: int) -> int:
    val = read_settings(db).get(key, str(default))
    try:
        return int(str(val).strip())
    except (ValueError, TypeError):
        return default


def save_setting(db: Session, key: str, value: str, *, updated_by: str) -> None:
    """Идемпотентный UPSERT в таблицу settings."""
    if key not in SETTING_DEFAULTS:
        raise ValueError(f"Неизвестный ключ настройки: {key!r}")
    db.execute(
        text(
            "INSERT INTO settings (key, value, updated_by) "
            "VALUES (:k, :v, :by) "
            "ON CONFLICT (key) DO UPDATE "
            "  SET value = EXCLUDED.value, "
            "      updated_at = NOW(), "
            "      updated_by = EXCLUDED.updated_by"
        ),
        {"k": key, "v": value, "by": updated_by},
    )
    db.commit()


# --- Excluded regions ---------------------------------------------------

def list_excluded_regions(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT region_code, region_name, excluded, reason "
            "FROM excluded_regions ORDER BY region_name"
        )
    ).all()
    return [
        {
            "region_code":  r.region_code,
            "region_name":  r.region_name,
            "excluded":     bool(r.excluded),
            "reason":       r.reason or "",
        }
        for r in rows
    ]


def toggle_region(db: Session, region_code: str, *, changed_by: str) -> bool:
    """Инвертирует excluded для региона. Возвращает новое значение."""
    row = db.execute(
        text("SELECT excluded FROM excluded_regions WHERE region_code = :rc"),
        {"rc": region_code},
    ).first()
    if row is None:
        raise ValueError(f"Регион не найден: {region_code!r}")
    new_value = not bool(row.excluded)
    db.execute(
        text(
            "UPDATE excluded_regions "
            "   SET excluded = :v, changed_at = NOW(), changed_by = :by "
            " WHERE region_code = :rc"
        ),
        {"v": new_value, "by": changed_by, "rc": region_code},
    )
    db.commit()
    return new_value


# --- KTRU watchlist -----------------------------------------------------

def list_ktru_watchlist(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT code, COALESCE(display_name, '') AS display_name, "
            "       is_active, COALESCE(note, '') AS note "
            "FROM ktru_watchlist ORDER BY is_active DESC, code"
        )
    ).all()
    return [
        {
            "code":         r.code,
            "display_name": r.display_name,
            "is_active":    bool(r.is_active),
            "note":         r.note,
        }
        for r in rows
    ]


def add_ktru(db: Session, *, code: str, display_name: str) -> None:
    code_clean = (code or "").strip()
    name_clean = (display_name or "").strip()
    if not code_clean:
        raise ValueError("KTRU-код не может быть пустым.")
    db.execute(
        text(
            "INSERT INTO ktru_watchlist (code, display_name, is_active, note) "
            "VALUES (:c, :dn, TRUE, :note) "
            "ON CONFLICT (code) DO UPDATE "
            "  SET display_name = EXCLUDED.display_name, "
            "      is_active    = TRUE"
        ),
        {"c": code_clean, "dn": name_clean or None, "note": name_clean or None},
    )
    db.commit()


def toggle_ktru(db: Session, code: str) -> bool:
    """Инвертирует is_active. Возвращает новое значение."""
    row = db.execute(
        text("SELECT is_active FROM ktru_watchlist WHERE code = :c"),
        {"c": code},
    ).first()
    if row is None:
        raise ValueError(f"KTRU не найден: {code!r}")
    new_value = not bool(row.is_active)
    db.execute(
        text("UPDATE ktru_watchlist SET is_active = :v WHERE code = :c"),
        {"v": new_value, "c": code},
    )
    db.commit()
    return new_value


# --- Inbox --------------------------------------------------------------

@dataclass
class InboxFilters:
    statuses: tuple[str, ...] = ()
    nmck_min: Decimal | None = None
    nmck_max: Decimal | None = None
    search: str | None = None
    urgent_only: bool = False
    print_only: bool = False
    # Стоп-лист регионов по умолчанию жёсткий: лоты с
    # `flags_jsonb.excluded_by_region=true` скрыты из инбокса. Менеджер
    # может вручную включить их через UI-чекбокс «показать стоп-регионы».
    include_excluded_regions: bool = False


_INBOX_SQL = """
WITH max_margin AS (
    SELECT ti.tender_id, MAX(m.margin_pct) AS max_pct
      FROM matches m
      JOIN tender_items ti ON ti.id = m.tender_item_id
     WHERE m.match_type = 'primary'
       AND m.margin_pct IS NOT NULL
     GROUP BY ti.tender_id
),
primary_count AS (
    SELECT ti.tender_id, COUNT(*) AS n
      FROM matches m
      JOIN tender_items ti ON ti.id = m.tender_item_id
     WHERE m.match_type = 'primary'
     GROUP BY ti.tender_id
),
items_breakdown AS (
    -- Префиксы 26.20.18.000- (МФУ) и 26.20.16.120- (Принтер) — печатная техника.
    -- Остальные коды (или NULL) — non-print позиции (ПК, мониторы и т.п.).
    SELECT ti.tender_id,
           COUNT(*) AS total_cnt,
           COUNT(*) FILTER (
               WHERE ti.ktru_code LIKE '26.20.18.000-%'
                  OR ti.ktru_code LIKE '26.20.16.120-%'
           ) AS printer_cnt
      FROM tender_items ti
     GROUP BY ti.tender_id
)
SELECT t.reg_number,
       t.customer,
       t.customer_region,
       t.nmck_total,
       t.submit_deadline,
       t.url                                                     AS eis_url,
       ts.status,
       mm.max_pct                                                AS max_margin_pct,
       COALESCE(pc.n, 0)                                         AS primary_count,
       COALESCE(ib.total_cnt, 0)                                 AS total_items_count,
       COALESCE(ib.printer_cnt, 0)                               AS printer_items_count,
       COALESCE(ib.total_cnt, 0) - COALESCE(ib.printer_cnt, 0)   AS non_printer_items_count,
       (t.submit_deadline IS NOT NULL AND t.submit_deadline < NOW()) AS is_overdue
  FROM tenders t
  LEFT JOIN tender_status ts ON ts.tender_id = t.reg_number
  LEFT JOIN max_margin    mm ON mm.tender_id = t.reg_number
  LEFT JOIN primary_count pc ON pc.tender_id = t.reg_number
  LEFT JOIN items_breakdown ib ON ib.tender_id = t.reg_number
 WHERE (:has_status_filter = 0 OR ts.status = ANY(CAST(:statuses AS text[])))
   AND (:nmck_min IS NULL OR t.nmck_total >= :nmck_min)
   AND (:nmck_max IS NULL OR t.nmck_total <= :nmck_max)
   AND (
        :search IS NULL
        OR t.reg_number      ILIKE :search_like
        OR t.customer        ILIKE :search_like
        OR t.customer_region ILIKE :search_like
       )
   AND (
        :print_only = 0
        OR (COALESCE(ib.total_cnt, 0) > 0
            AND COALESCE(ib.total_cnt, 0) = COALESCE(ib.printer_cnt, 0))
       )
   -- Стоп-лист регионов: по умолчанию скрываем лоты с флагом
   -- excluded_by_region (взводится в ingest/filters.py::compute_flags
   -- по канонической форме customer_region — см. region_normalizer.py).
   -- Менеджер может временно показать их, отметив UI-чекбокс
   -- «показать стоп-регионы» (?include_excluded_regions=1).
   AND (
        :include_excluded_regions = 1
        OR NOT COALESCE((t.flags_jsonb->>'excluded_by_region')::boolean, false)
       )
 ORDER BY t.submit_deadline DESC NULLS LAST, t.reg_number
"""


def _classify_section(
    *,
    status: str,
    is_overdue: bool,
    submit_deadline,
    deadline_alert_hours: int,
    max_margin_pct,
    margin_threshold_pct: int,
) -> str:
    """Классификатор секции для inbox — см. описание секций в плане 9a (DoD §4.3)."""
    if status in ARCHIVE_STATUSES:
        return "archive"

    # Просрочки также прячем в архив (per spec — «в работе» не должно
    # содержать лоты с дедлайном в прошлом).
    if is_overdue:
        return "archive"

    # Срочные — статусы пред-подачи + дедлайн в ближайшие N часов.
    if status in {"new", "in_review", "will_bid"} and submit_deadline is not None:
        if isinstance(submit_deadline, datetime):
            deadline = submit_deadline
            now = datetime.now(tz=deadline.tzinfo) if deadline.tzinfo else datetime.utcnow()
            if deadline < now + timedelta(hours=deadline_alert_hours):
                return "urgent"

    if status == "new":
        if (
            max_margin_pct is not None
            and Decimal(max_margin_pct) >= Decimal(margin_threshold_pct)
        ):
            return "ready_to_review"
        # Без сильной маржи — в ту же секцию, но визуально dimmer.
        return "new_low_margin"

    if status in {"in_review", "will_bid", "submitted"}:
        return "in_work"

    # На всякий случай — fallback в архив.
    return "archive"


def get_inbox_data(
    db: Session,
    *,
    filters: InboxFilters,
    deadline_alert_hours: int,
    margin_threshold_pct: int,
) -> dict[str, Any]:
    """Возвращает {sections, totals, filters_echo}.

    sections — dict с ключами 'urgent', 'ready_to_review', 'new_low_margin',
    'in_work', 'archive'. Каждый — list[dict] с полями ряда.
    """
    statuses_param = list(filters.statuses) if filters.statuses else []
    params: dict[str, Any] = {
        "has_status_filter": 1 if filters.statuses else 0,
        "statuses":          statuses_param,
        "nmck_min":          filters.nmck_min,
        "nmck_max":          filters.nmck_max,
        "search":            filters.search,
        "search_like":       f"%{filters.search}%" if filters.search else None,
        "print_only":        1 if filters.print_only else 0,
        "include_excluded_regions": 1 if filters.include_excluded_regions else 0,
    }

    rows = db.execute(text(_INBOX_SQL), params).all()

    sections: dict[str, list[dict[str, Any]]] = {
        "urgent":            [],
        "ready_to_review":   [],
        "new_low_margin":    [],
        "in_work":           [],
        "archive":           [],
    }

    for r in rows:
        section = _classify_section(
            status=r.status or "new",
            is_overdue=bool(r.is_overdue),
            submit_deadline=r.submit_deadline,
            deadline_alert_hours=deadline_alert_hours,
            max_margin_pct=r.max_margin_pct,
            margin_threshold_pct=margin_threshold_pct,
        )
        if filters.urgent_only and section != "urgent":
            continue
        sections[section].append({
            "reg_number":      r.reg_number,
            "customer":        r.customer or "",
            "customer_region": r.customer_region or "",
            "nmck_total":      r.nmck_total,
            "submit_deadline": r.submit_deadline,
            "eis_url":         r.eis_url or "",
            "status":          r.status or "new",
            "status_label":    STATUS_LABELS.get(r.status or "new", r.status or "new"),
            "max_margin_pct":  r.max_margin_pct,
            "primary_count":   int(r.primary_count or 0),
            "is_overdue":      bool(r.is_overdue),
            "total_items_count":       int(r.total_items_count or 0),
            "printer_items_count":     int(r.printer_items_count or 0),
            "non_printer_items_count": int(r.non_printer_items_count or 0),
        })

    totals = {k: len(v) for k, v in sections.items()}
    totals["total"] = sum(totals.values())
    return {
        "sections":            sections,
        "totals":              totals,
        "margin_threshold_pct": margin_threshold_pct,
        "deadline_alert_hours": deadline_alert_hours,
    }


# --- Карточка лота ------------------------------------------------------

def get_tender(db: Session, reg_number: str) -> dict[str, Any] | None:
    row = db.execute(
        text(
            "SELECT t.reg_number, t.customer, t.customer_region, "
            "       t.customer_contacts_jsonb, t.nmck_total, "
            "       t.publish_date, t.submit_deadline, t.delivery_deadline, "
            "       t.ktru_codes_array, t.url, t.flags_jsonb, "
            "       t.ingested_at, t.updated_at, "
            "       COALESCE(ts.status, 'new')                         AS status, "
            "       ts.note, ts.contract_registry_number, "
            "       ts.contract_key_dates_jsonb, ts.changed_at, ts.changed_by "
            "  FROM tenders t "
            "  LEFT JOIN tender_status ts ON ts.tender_id = t.reg_number "
            " WHERE t.reg_number = :rn"
        ),
        {"rn": reg_number},
    ).first()
    if row is None:
        return None
    return {
        "reg_number":              row.reg_number,
        "customer":                row.customer or "",
        "customer_region":         row.customer_region or "",
        "customer_contacts_jsonb": row.customer_contacts_jsonb or {},
        "nmck_total":              row.nmck_total,
        "publish_date":            row.publish_date,
        "submit_deadline":         row.submit_deadline,
        "delivery_deadline":       row.delivery_deadline,
        "ktru_codes_array":        list(row.ktru_codes_array or []),
        "url":                     row.url or "",
        "flags_jsonb":             row.flags_jsonb or {},
        "ingested_at":             row.ingested_at,
        "updated_at":              row.updated_at,
        "status":                  row.status,
        "status_label":            STATUS_LABELS.get(row.status, row.status),
        "note":                    row.note or "",
        "contract_registry_number": row.contract_registry_number or "",
        "contract_key_dates_jsonb": row.contract_key_dates_jsonb or {},
        "changed_at":              row.changed_at,
        "changed_by":              row.changed_by or "",
    }


def get_tender_items_with_matches(
    db: Session, reg_number: str, *, alternatives_limit: int = 10,
) -> list[dict[str, Any]]:
    """Список позиций тендера с primary+top-N alternative матчами."""
    items = db.execute(
        text(
            "SELECT id, position_num, ktru_code, name, qty, unit, "
            "       required_attrs_jsonb, nmck_per_unit "
            "  FROM tender_items "
            " WHERE tender_id = :rn "
            " ORDER BY position_num"
        ),
        {"rn": reg_number},
    ).all()
    if not items:
        return []

    item_ids = [int(i.id) for i in items]

    # primary + top-N alternative для всех позиций сразу.
    # cheapest_supplier — имя поставщика с самой низкой ценой при stock_qty>0
    # для category IN ('printer','mfu'). Берём через коррелированный
    # подзапрос, чтобы не зависеть от того, какой именно поставщик попадёт
    # в JOIN с supplier_prices.
    matches = db.execute(
        text(
            """
            SELECT m.id, m.tender_item_id, m.nomenclature_id, m.match_type,
                   m.rule_hits_jsonb, m.price_total_rub, m.margin_rub, m.margin_pct,
                   pmu.sku, pmu.brand, pmu.mpn, pmu.name AS pmu_name,
                   pmu.cost_base_rub, pmu.attrs_jsonb,
                   (
                     SELECT s.name
                       FROM supplier_prices sp
                       JOIN suppliers s ON s.id = sp.supplier_id
                      WHERE sp.component_id = m.nomenclature_id
                        AND sp.category IN ('printer', 'mfu')
                        AND sp.stock_qty > 0
                      ORDER BY sp.price ASC
                      LIMIT 1
                   ) AS cheapest_supplier,
                   ROW_NUMBER() OVER (
                       PARTITION BY m.tender_item_id, m.match_type
                       ORDER BY m.margin_pct DESC NULLS LAST, m.id
                   ) AS rn
              FROM matches m
              LEFT JOIN printers_mfu pmu ON pmu.id = m.nomenclature_id
             WHERE m.tender_item_id = ANY(CAST(:ids AS bigint[]))
            """
        ),
        {"ids": item_ids},
    ).all()

    by_item: dict[int, dict[str, Any]] = {iid: {"primary": None, "alternatives": []} for iid in item_ids}
    for m in matches:
        m_dict = {
            "id":               int(m.id),
            "match_type":       m.match_type,
            "nomenclature_id":  int(m.nomenclature_id),
            "sku":              m.sku or "",
            "brand":            m.brand or "",
            "mpn":              getattr(m, "mpn", None) or "",
            "name":             m.pmu_name or "",
            "cost_base_rub":    m.cost_base_rub,
            "attrs_jsonb":      m.attrs_jsonb or {},
            "rule_hits_jsonb":  m.rule_hits_jsonb or {},
            "price_total_rub":  m.price_total_rub,
            "margin_rub":       m.margin_rub,
            "margin_pct":       m.margin_pct,
            "cheapest_supplier": getattr(m, "cheapest_supplier", None) or "",
            "needs_manual_verification":
                bool((m.rule_hits_jsonb or {}).get("needs_manual_verification")),
        }
        ti = int(m.tender_item_id)
        if m.match_type == "primary" and by_item[ti]["primary"] is None:
            by_item[ti]["primary"] = m_dict
        elif m.match_type == "alternative" and int(m.rn) <= alternatives_limit:
            by_item[ti]["alternatives"].append(m_dict)

    out: list[dict[str, Any]] = []
    for it in items:
        m_data = by_item[int(it.id)]
        out.append({
            "id":                  int(it.id),
            "position_num":        int(it.position_num),
            "ktru_code":           it.ktru_code or "",
            "name":                it.name or "",
            "qty":                 it.qty,
            "unit":                it.unit or "",
            "required_attrs_jsonb": it.required_attrs_jsonb or {},
            "nmck_per_unit":       it.nmck_per_unit,
            "primary":             m_data["primary"],
            "alternatives":        m_data["alternatives"],
        })
    return out


def update_status(
    db: Session,
    *,
    reg_number: str,
    new_status: str,
    changed_by: str,
) -> str:
    """Меняет статус по state-machine. Возвращает прежнее значение
    (для аудита). Бросает ValueError при недопустимом переходе."""
    if new_status not in ALLOWED_TRANSITIONS:
        raise ValueError(f"Неизвестный статус: {new_status!r}")

    row = db.execute(
        text(
            "SELECT status FROM tender_status WHERE tender_id = :rn"
        ),
        {"rn": reg_number},
    ).first()
    if row is None:
        # Если строки в tender_status ещё нет — лот не ингестирован
        # (не должно случаться для валидного reg_number, но страхуемся).
        raise ValueError(f"tender_status для {reg_number!r} не найден.")
    current = row.status or "new"

    if not is_transition_allowed(current, new_status):
        raise ValueError(
            f"Переход {current!r} → {new_status!r} запрещён state-machine."
        )

    db.execute(
        text(
            "UPDATE tender_status "
            "   SET status = :s, changed_at = NOW(), changed_by = :by "
            " WHERE tender_id = :rn"
        ),
        {"s": new_status, "by": changed_by, "rn": reg_number},
    )
    db.commit()
    return current


def update_contract(
    db: Session,
    *,
    reg_number: str,
    contract_registry_number: str,
    key_dates: dict[str, str],
    changed_by: str,
) -> None:
    """Обновляет реквизиты контракта и ключевые даты после статуса 'won'."""
    import json
    db.execute(
        text(
            "UPDATE tender_status "
            "   SET contract_registry_number = :crn, "
            "       contract_key_dates_jsonb = CAST(:dates AS JSONB), "
            "       changed_at = NOW(), "
            "       changed_by = :by "
            " WHERE tender_id = :rn"
        ),
        {
            "crn":   contract_registry_number or None,
            "dates": json.dumps(key_dates, ensure_ascii=False),
            "by":    changed_by,
            "rn":    reg_number,
        },
    )
    db.commit()


def update_note(
    db: Session,
    *,
    reg_number: str,
    note: str,
    changed_by: str,
) -> None:
    db.execute(
        text(
            "UPDATE tender_status "
            "   SET note = :note, "
            "       changed_at = NOW(), "
            "       changed_by = :by "
            " WHERE tender_id = :rn"
        ),
        {"note": (note or "").strip() or None, "by": changed_by, "rn": reg_number},
    )
    db.commit()


# --- Форматтеры для шаблонов --------------------------------------------

def _to_msk(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("Europe/Moscow"))
    except Exception:
        return dt.astimezone(timezone(timedelta(hours=3)))


def format_msk_dt(dt: datetime | None) -> str:
    """«dd.mm.yyyy HH:MM» в МСК. Пустая строка для None."""
    msk = _to_msk(dt)
    if msk is None:
        return "—"
    return msk.strftime("%d.%m.%Y %H:%M")


def format_msk_date(dt: datetime | None) -> str:
    """«dd.mm.yyyy» в МСК."""
    msk = _to_msk(dt)
    if msk is None:
        return "—"
    return msk.strftime("%d.%m.%Y")
