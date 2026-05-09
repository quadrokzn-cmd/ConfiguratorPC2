# Дашборд портала: сбор данных для виджетов главной (этап 9Б.2).
#
# Все запросы — text-SQL через SQLAlchemy. Никакого ORM здесь не нужно:
# 5 виджетов читают агрегаты из таблиц, и единственная задача сервиса —
# вернуть готовый dict для шаблона home.html.
#
# Виджеты доступны всем авторизованным (admin + manager) — это «общие»
# метрики компании: «всё видят все внутри компании». Поэтому фильтра
# по user_id нет.
#
# Если данных в БД нет (например, ещё ни одного загруженного прайса) —
# функция не падает, поля приходят пустыми/нулевыми; шаблон показывает
# «—» или специальный no-data state.

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, date, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


# 8 категорий компонентов и их человекочитаемые подписи + ключи иконок.
# Порядок — тот же, что в брифе 9Б.2.
COMPONENT_CATEGORIES: list[tuple[str, str, str]] = [
    # (table_name, label, icon_key)
    ("cpus",         "CPU",         "cpu"),
    ("gpus",         "GPU",         "monitor"),
    ("rams",         "RAM",         "ram"),
    ("storages",     "Накопители",  "drive"),
    ("motherboards", "Материнки",   "circuit"),
    ("psus",         "БП",          "power"),
    ("cases",        "Корпуса",     "case"),
    ("coolers",      "Охлаждение",  "fan"),
]


# Целевые поставщики, по которым показываем «свежесть прайсов».
# Порядок имеет значение — в этом порядке отрисовываются строки.
# 11.2: после миграции 019 у нас 6 поставщиков; виджет показывает все.
SUPPLIERS_FOR_FRESHNESS: list[str] = [
    "OCS", "Merlion", "Treolan",
    "Netlab", "Ресурс Медиа", "Green Place",
]


# Сколько дней без обновления → бейдж «устарел».
STALE_PRICE_DAYS = 14


@dataclass
class _SupplierFreshness:
    name: str
    last_loaded_at: datetime | None
    is_stale: bool          # True, если > STALE_PRICE_DAYS назад
    days_ago: int | None    # сколько дней прошло; None если данных нет


def _count_table(db: Session, table: str, where: str = "") -> int:
    """COUNT(*) с опциональным WHERE. Не падает на отсутствующих таблицах
    (хотя они обязательно есть после миграций — это страховка)."""
    sql = f"SELECT COUNT(*) AS n FROM {table}"
    if where:
        sql += f" WHERE {where}"
    try:
        row = db.execute(text(sql)).first()
        return int(row.n) if row else 0
    except Exception:
        return 0


def _get_active_projects(db: Session) -> dict[str, Any]:
    """Виджет 1 — активные проекты.

    «Активный» в текущей схеме projects = просто все проекты (deleted_at
    нет; soft-delete не предусмотрен). Если когда-то появится — здесь
    будет фильтр.
    """
    total = _count_table(db, "projects")
    return {
        "total": total,
        "label": "Активные проекты",
    }


def _get_managers(db: Session) -> dict[str, Any]:
    """Виджет 2 — менеджеры.

    Считаем активных пользователей с role='manager'. Поля last_login_at
    в схеме нет (миграция 007), поэтому сабтайтл «X из них активны за
    неделю» пропускаем — указано в брифе 9Б.2.
    """
    total = _count_table(db, "users", where="role = 'manager' AND is_active = TRUE")
    return {
        "total": total,
        "label": "Менеджеры",
    }


def _get_exchange_rate(db: Session) -> dict[str, Any]:
    """Виджет 3 — курс доллара ЦБ.

    Берём САМУЮ свежую запись из exchange_rates по rate_date DESC,
    fetched_at DESC (как в app/services/export/exchange_rate.py).
    Дополнительно ходить на ЦБ из портала не надо — у конфигуратора
    стоит APScheduler, который кладёт сюда курсы 5 раз в день.
    """
    try:
        row = db.execute(
            text(
                "SELECT rate_date, rate_usd_rub, source, fetched_at "
                "FROM exchange_rates "
                "ORDER BY rate_date DESC, fetched_at DESC LIMIT 1"
            )
        ).first()
    except Exception:
        row = None

    if row is None:
        return {
            "rate":       None,
            "rate_date":  None,
            "fetched_at": None,
            "source":     "cbr",
            "label":      "Курс доллара ЦБ",
        }

    fetched_at = row.fetched_at
    if fetched_at is not None and fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)

    return {
        "rate":       float(row.rate_usd_rub),
        "rate_date":  row.rate_date,
        "fetched_at": fetched_at,
        "source":     row.source,
        "label":      "Курс доллара ЦБ",
    }


def _get_suppliers_freshness(db: Session) -> list[dict[str, Any]]:
    """Виджет 4 — свежесть прайсов.

    Для каждого поставщика из SUPPLIERS_FOR_FRESHNESS читаем
    MAX(uploaded_at) из price_uploads с фильтром status='success'
    (миграция 001 — uploaded_at, не loaded_at). Если поставщика в БД
    ещё нет, или прайсов не было — возвращаем строку с last_loaded_at=None
    и бейджем «нет данных».
    """
    today = date.today()
    rows: list[dict[str, Any]] = []
    try:
        # Один запрос: LEFT JOIN price_uploads, чтобы получить даже
        # тех поставщиков, у которых ни одного загруженного прайса не было.
        # Учитываем status IN ('success', 'partial') — partial = «часть
        # строк сматчилась, часть нет», это нормальная штатная ситуация
        # при загрузке через orchestrator (см. price_loaders/orchestrator.py).
        # Только 'failed' не считаем «загрузкой прайса».
        result = db.execute(
            text(
                "SELECT s.name AS name, MAX(pu.uploaded_at) AS last_at "
                "FROM suppliers s "
                "LEFT JOIN price_uploads pu "
                "  ON pu.supplier_id = s.id "
                " AND (pu.status IS NULL OR pu.status IN ('success', 'partial')) "
                "WHERE s.name = ANY(:names) "
                "GROUP BY s.name"
            ),
            {"names": SUPPLIERS_FOR_FRESHNESS},
        ).all()
        latest_by_name: dict[str, datetime | None] = {
            r.name: r.last_at for r in result
        }
    except Exception:
        latest_by_name = {}

    for name in SUPPLIERS_FOR_FRESHNESS:
        last_at = latest_by_name.get(name)
        days_ago: int | None = None
        is_stale = True
        if last_at is not None:
            last_date = last_at.date() if hasattr(last_at, "date") else last_at
            days_ago = max(0, (today - last_date).days)
            is_stale = days_ago > STALE_PRICE_DAYS
        rows.append({
            "name":          name,
            "last_loaded_at": last_at,
            "days_ago":      days_ago,
            "is_stale":      is_stale,
        })
    return rows


def _get_components_breakdown(db: Session) -> dict[str, Any]:
    """Виджет 5 — компоненты в БД.

    Общее число (с фильтром is_hidden = FALSE) + разбивка по 8 категориям.
    Для миниатюрного bar-chart возвращаем max_count, чтобы шаблон мог
    нормировать ширину полоски без второго прохода.
    """
    by_category: list[dict[str, Any]] = []
    total = 0
    for table, label, icon_key in COMPONENT_CATEGORIES:
        n = _count_table(db, table, where="is_hidden = FALSE")
        total += n
        by_category.append({
            "table":    table,
            "label":    label,
            "icon_key": icon_key,
            "count":    n,
        })
    max_count = max((c["count"] for c in by_category), default=0)
    return {
        "total":       total,
        "categories":  by_category,
        "max_count":   max_count,
        "label":       "Компоненты в БД",
    }


def _read_setting_int(db: Session, key: str, default: int) -> int:
    """Достаёт целочисленный порог из таблицы settings; возвращает default,
    если ключа нет или таблицы нет (для test-БД без миграции 030)."""
    try:
        row = db.execute(
            text("SELECT value FROM settings WHERE key = :k"),
            {"k": key},
        ).first()
    except Exception:
        return default
    if row is None:
        return default
    try:
        return int(str(row.value).strip())
    except (ValueError, TypeError):
        return default


def _get_auctions_overview(db: Session) -> dict[str, Any]:
    """Виджет 6 — аукционы (этап 9a слияния).

    Возвращает три счётчика для главной портала:
      - total_active: тендеры со статусом ∈ {new, in_review, will_bid, submitted}
      - urgent: те же ∩ статус ∈ {new, in_review, will_bid} И
                submit_deadline < NOW() + interval '<deadline_alert_hours> hours'
      - with_primary_above_threshold: тендеры, у которых хотя бы один
                primary-match имеет margin_pct >= margin_threshold_pct

    Все данные берутся из аукционных таблиц (миграция 030). На тестовой
    БД без 030 функция возвращает все нули и not_available=True — шаблон
    в этом случае не рисует виджет.
    """
    deadline_hours = _read_setting_int(db, "deadline_alert_hours", 24)
    margin_threshold = _read_setting_int(db, "margin_threshold_pct", 15)

    out: dict[str, Any] = {
        "total_active":                 0,
        "urgent":                       0,
        "with_primary_above_threshold": 0,
        "margin_threshold_pct":         margin_threshold,
        "deadline_alert_hours":         deadline_hours,
        "label":                        "Аукционы",
        "available":                    False,
    }

    try:
        row = db.execute(
            text(
                """
                SELECT
                    COUNT(*) FILTER (
                        WHERE ts.status IN ('new', 'in_review', 'will_bid', 'submitted')
                    )                                              AS total_active,
                    COUNT(*) FILTER (
                        WHERE ts.status IN ('new', 'in_review', 'will_bid')
                          AND t.submit_deadline IS NOT NULL
                          AND t.submit_deadline < NOW() + (:hours || ' hours')::interval
                    )                                              AS urgent
                  FROM tender_status ts
                  JOIN tenders t ON t.reg_number = ts.tender_id
                """
            ),
            {"hours": str(deadline_hours)},
        ).first()
        if row is not None:
            out["total_active"] = int(row.total_active or 0)
            out["urgent"] = int(row.urgent or 0)

        # Тендеры с хотя бы одним primary-матчем выше порога маржи.
        row2 = db.execute(
            text(
                """
                SELECT COUNT(DISTINCT ti.tender_id) AS n
                  FROM matches m
                  JOIN tender_items ti ON ti.id = m.tender_item_id
                 WHERE m.match_type = 'primary'
                   AND m.margin_pct IS NOT NULL
                   AND m.margin_pct >= :threshold
                """
            ),
            {"threshold": margin_threshold},
        ).first()
        if row2 is not None:
            out["with_primary_above_threshold"] = int(row2.n or 0)

        out["available"] = True
    except Exception:
        # Аукционных таблиц нет — оставляем нули и available=False.
        pass

    return out


def get_dashboard_data(db: Session) -> dict[str, Any]:
    """Главная функция сервиса. Возвращает dict со всеми виджетами.

    Контракт ключей (используется и в шаблоне, и в тестах):
      - active_projects:       {"total", "label"}
      - managers:              {"total", "label"}
      - exchange_rate:         {"rate", "rate_date", "fetched_at", "source", "label"}
      - suppliers_freshness:   list of {"name", "last_loaded_at", "days_ago", "is_stale"}
      - components_breakdown:  {"total", "categories", "max_count", "label"}
      - auctions_overview:     {"total_active", "urgent",
                                "with_primary_above_threshold", "available", ...}

    Каждое значение должно быть «безопасным» для шаблона — никаких
    исключений наружу. На пустой БД получаем нули и Nones, шаблон
    отрисует «—» / «нет данных».
    """
    return {
        "active_projects":      _get_active_projects(db),
        "managers":             _get_managers(db),
        "exchange_rate":        _get_exchange_rate(db),
        "suppliers_freshness":  _get_suppliers_freshness(db),
        "components_breakdown": _get_components_breakdown(db),
        "auctions_overview":    _get_auctions_overview(db),
    }


# ---------- Форматтеры для шаблонов ----------

_RU_MONTHS = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def format_ru_date(d: date | datetime | None) -> str:
    """«27 апреля 2026» — формат для подписи курса. None → пустая строка."""
    if d is None:
        return ""
    if isinstance(d, datetime):
        d = d.date()
    return f"{d.day} {_RU_MONTHS[d.month]} {d.year}"


def format_ru_datetime_short(dt: datetime | None) -> str:
    """«27 апреля 2026, 13:00» — для подписи под курсом ЦБ.

    Время приводится к МСК через zoneinfo. Если zoneinfo недоступен —
    отдаём UTC, чтобы хотя бы что-то показать.
    """
    if dt is None:
        return ""
    try:
        from zoneinfo import ZoneInfo
        msk = ZoneInfo("Europe/Moscow")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local = dt.astimezone(msk)
    except Exception:
        local = dt
    return f"{local.day} {_RU_MONTHS[local.month]} {local.year}, {local.strftime('%H:%M')}"


def format_days_ago(days: int | None) -> str:
    """«сегодня», «вчера», «3 дня назад», «12 дней назад»."""
    if days is None:
        return "нет данных"
    if days == 0:
        return "сегодня"
    if days == 1:
        return "вчера"
    # Простая русская плюрализация для «дней» — без зависимостей.
    last_two = days % 100
    last = days % 10
    if 11 <= last_two <= 14:
        word = "дней"
    elif last == 1:
        word = "день"
    elif 2 <= last <= 4:
        word = "дня"
    else:
        word = "дней"
    return f"{days} {word} назад"
