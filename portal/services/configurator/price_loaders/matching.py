# Сопоставление PriceRow с существующими компонентами БД.
#
# Алгоритм (см. раздел «Логика сопоставления» в ТЗ этапа 7):
#
#   1. «Существующая запись»: ищем в supplier_prices строку с тем же
#      (supplier_id, supplier_sku). Если есть — это повторная загрузка
#      того же товара, component_id берём оттуда.
#
#   2. Match по MPN: SELECT id FROM <table> WHERE sku = row.mpn
#      - 0 результатов → шаг 3;
#      - ровно 1      → source='mpn';
#      - несколько    → source='ambiguous_mpn', выбираем минимальный id
#                        (чтобы при повторной загрузке был тот же выбор);
#                        одновременно запоминаем список кандидатов, чтобы
#                        orchestrator мог положить его в notes.
#
#   3. Match по GTIN: SELECT id FROM <table> WHERE gtin = row.gtin
#      - 0  → source='no_match';
#      - 1  → source='gtin';
#      - >1 → source='ambiguous_gtin' (логика как в шаге 2).
#
# Special case Intel CPU из Treolan:
#   Специальной ветки не требуется — логика шагов 2/3 уже его покрывает:
#   MPN='SRMBG' не найдёт sku='CM8071512400F' (шаг 2 → 0),
#   а GTIN найдёт (если backfill_gtin был прогнан).

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from portal.services.configurator.enrichment.base import ALLOWED_TABLES, CATEGORY_TO_TABLE
from portal.services.configurator.price_loaders.models import PriceRow


# ---------------------------------------------------------------------------
# Коды источников сопоставления. Используются orchestrator'ом для решения,
# что писать в supplier_prices и нужно ли заводить запись в unmapped.
# ---------------------------------------------------------------------------

EXISTING      = "existing"        # supplier_prices уже содержит эту строку
MATCH_MPN     = "mpn"             # ровно один компонент по MPN
MATCH_GTIN    = "gtin"            # ровно один компонент по GTIN
AMBIG_MPN     = "ambiguous_mpn"   # >1 компонента по MPN
AMBIG_GTIN    = "ambiguous_gtin"  # >1 компонента по GTIN
NO_MATCH      = "no_match"        # ни по MPN, ни по GTIN


@dataclass
class MatchResult:
    source: str                                # см. константы выше
    component_id: int | None = None            # id выбранного компонента (для no_match — None)
    # Для ambiguous: все кандидаты, чтобы положить в notes/unmapped.
    ambiguous_ids: list[int] = field(default_factory=list)


def _table_for(our_category: str) -> str:
    if our_category not in CATEGORY_TO_TABLE:
        raise ValueError(f"Неизвестная категория: {our_category!r}")
    table = CATEGORY_TO_TABLE[our_category]
    if table not in ALLOWED_TABLES:
        raise RuntimeError(f"Таблица {table} вне whitelist (защита от инъекций)")
    return table


def _find_existing_supplier_price(
    session: Session, supplier_id: int, supplier_sku: str,
) -> int | None:
    """Возвращает component_id из supplier_prices, если строка уже была
    загружена этим же поставщиком под тем же supplier_sku."""
    if not supplier_sku:
        return None
    row = session.execute(
        text(
            "SELECT component_id FROM supplier_prices "
            "WHERE supplier_id = :sid AND supplier_sku = :ssku "
            "LIMIT 1"
        ),
        {"sid": supplier_id, "ssku": supplier_sku},
    ).first()
    return int(row.component_id) if row else None


def _search_by_column(
    session: Session, table: str, column: str, value: str,
) -> list[int]:
    """Возвращает список id компонентов, у которых <column> = <value>.
    Сортировка по id — даёт детерминированный выбор при ambiguous."""
    # Имена table и column берутся только из whitelist ниже — инъекция
    # невозможна.
    assert column in {"sku", "mpn", "gtin"}, f"Недопустимая колонка: {column}"
    rows = session.execute(
        text(f"SELECT id FROM {table} WHERE {column} = :val ORDER BY id"),
        {"val": value},
    ).all()
    return [int(r.id) for r in rows]


# Этап 6 слияния: в ПК-таблицах (cpus/...) MPN хранится в колонке `sku`
# (там это просто текстовый идентификатор без UNIQUE-constraint). В
# printers_mfu MPN живёт в отдельной колонке `mpn`, а `sku` — это
# canonical-ключ вида `brand:mpn`. Поэтому для матчинга по MPN нужна
# таблично-зависимая колонка.
_MPN_COLUMN_BY_TABLE = {
    "printers_mfu": "mpn",
}


def _mpn_column_for(table: str) -> str:
    return _MPN_COLUMN_BY_TABLE.get(table, "sku")


def resolve(
    session: Session,
    row: PriceRow,
    *,
    supplier_id: int,
) -> MatchResult:
    """Главная функция сопоставления. Не пишет в БД — возвращает решение,
    а orchestrator его применяет."""
    if row.our_category is None:
        # Orchestrator такие строки не передаёт сюда, но на всякий случай:
        return MatchResult(source=NO_MATCH)

    # Шаг 1: повторная загрузка.
    existing_id = _find_existing_supplier_price(session, supplier_id, row.supplier_sku)
    if existing_id is not None:
        return MatchResult(source=EXISTING, component_id=existing_id)

    table = _table_for(row.our_category)

    # Шаг 2: по MPN. Колонка-носитель MPN зависит от таблицы:
    # ПК-таблицы (cpus/...) — `sku`, printers_mfu — выделенный `mpn`.
    if row.mpn:
        ids = _search_by_column(session, table, _mpn_column_for(table), row.mpn)
        if len(ids) == 1:
            return MatchResult(source=MATCH_MPN, component_id=ids[0])
        if len(ids) > 1:
            return MatchResult(
                source=AMBIG_MPN,
                component_id=ids[0],     # детерминированный выбор — min(id)
                ambiguous_ids=ids,
            )

    # Шаг 3: по GTIN.
    if row.gtin:
        ids = _search_by_column(session, table, "gtin", row.gtin)
        if len(ids) == 1:
            return MatchResult(source=MATCH_GTIN, component_id=ids[0])
        if len(ids) > 1:
            return MatchResult(
                source=AMBIG_GTIN,
                component_id=ids[0],
                ambiguous_ids=ids,
            )

    # Ни то, ни другое.
    return MatchResult(source=NO_MATCH)
