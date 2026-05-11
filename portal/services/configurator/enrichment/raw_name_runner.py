# Раннер обогащения характеристик по supplier_prices.raw_name (этап 11.6.1).
#
# Отличия от runner.py:
#   - источник имён — все raw_name из supplier_prices, привязанные к компоненту,
#     плюс модель компонента как fallback;
#   - агрегация по компоненту: каждое из имён прогоняется через regex-экстрактор
#     текущей категории, конфликты разрешаются по «самому длинному raw_name»;
#   - в component_field_sources пишется source_detail='from_raw_name'.
#
# Старый runner.py не трогаем — он остаётся источником обогащения «по name
# таблицы компонентов» и обслуживается scripts/enrich_regex.py.

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import text

from app.database import SessionLocal
from portal.services.configurator.enrichment.base import (
    ALLOWED_TABLES,
    CATEGORY_TO_TABLE,
    ExtractedField,
)
from portal.services.configurator.enrichment.persistence import apply_enrichment
from portal.services.configurator.enrichment.regex_sources import case as case_src
from portal.services.configurator.enrichment.regex_sources import cooler as cooler_src
from portal.services.configurator.enrichment.regex_sources import cpu as cpu_src
from portal.services.configurator.enrichment.regex_sources import gpu as gpu_src
from portal.services.configurator.enrichment.regex_sources import motherboard as mobo_src
from portal.services.configurator.enrichment.regex_sources import psu as psu_src
from portal.services.configurator.enrichment.regex_sources import ram as ram_src
from portal.services.configurator.enrichment.regex_sources import storage as storage_src
from portal.services.configurator.enrichment.runner import EXTRACTORS, REQUIRED_FIELDS

logger = logging.getLogger(__name__)


# Нормализованные коды поставщиков (для фильтра --supplier).
ALL_SUPPLIER_CODES = (
    "ocs", "merlion", "treolan", "netlab", "resurs_media", "green_place",
)

# Соответствие code → like-паттерн по suppliers.name. Имена в БД могут быть
# написаны по-разному ("OCS", "OCS Distribution", "ООО «ОЦС Дистрибуция»"),
# поэтому матчим по подстроке без регистра.
_SUPPLIER_NAME_PATTERNS = {
    "ocs":          ["ocs", "оцс"],
    "merlion":      ["merlion", "мерлион"],
    "treolan":      ["treolan", "треолан"],
    "netlab":       ["netlab", "нетлаб"],
    "resurs_media": ["ресурс", "resurs"],
    "green_place":  ["green", "грин"],
}


@dataclass
class CategoryStats:
    """Статистика по одной категории за один прогон."""
    category: str
    candidates: int = 0           # сколько компонентов прошли по фильтрам
    components_updated: int = 0   # сколько получили хотя бы 1 новое поле
    fields_written: int = 0       # сколько полей реально записано
    field_stats: dict[str, int] = field(default_factory=dict)
    conflicts: list[dict] = field(default_factory=list)
    remaining_with_null: int = 0  # сколько компонентов после прогона ещё имеют NULL
    errors: int = 0


@dataclass
class RunReport:
    """Полный отчёт прогона по всем категориям."""
    by_category: dict[str, CategoryStats] = field(default_factory=dict)

    @property
    def total_components_updated(self) -> int:
        return sum(s.components_updated for s in self.by_category.values())

    @property
    def total_fields_written(self) -> int:
        return sum(s.fields_written for s in self.by_category.values())


# -------------------------------------------------------------------------
# Сбор кандидатов
# -------------------------------------------------------------------------


def _resolve_supplier_ids(session, code: str) -> list[int]:
    """Находит supplier_id'ы по коду поставщика (для фильтра --supplier)."""
    patterns = _SUPPLIER_NAME_PATTERNS.get(code)
    if not patterns:
        return []
    rows = session.execute(
        text(
            "SELECT id FROM suppliers WHERE "
            + " OR ".join(f"LOWER(name) LIKE :p{i}" for i in range(len(patterns)))
        ),
        {f"p{i}": f"%{p}%" for i, p in enumerate(patterns)},
    ).all()
    return [r[0] for r in rows]


def _collect_components(
    session,
    *,
    category: str,
    supplier_ids: list[int] | None,
    component_id: int | None,
) -> list[dict]:
    """Возвращает список компонентов-кандидатов с их raw_name'ами.

    Каждый элемент — dict:
      {
        "id": 12,
        "model": "...",
        "raw_names": ["raw_name_1", ...],  # отсортированы по убыванию длины
        <required_fields_текущие_значения>: ...,
      }

    Берутся только компоненты, у которых хотя бы одно обязательное поле NULL.
    Если supplier_ids указан — берутся только те, у которых есть raw_name
    от этого/этих поставщиков.
    """
    table = CATEGORY_TO_TABLE[category]
    assert table in ALLOWED_TABLES, f"Недопустимая таблица: {table}"

    required = REQUIRED_FIELDS[category]
    cols = ", ".join(["c.id AS id", "c.model AS model"] + [f"c.{f} AS {f}" for f in required])
    where_null = " OR ".join(f"c.{f} IS NULL" for f in required)

    # JOIN с supplier_prices: нужны компоненты, у которых есть хотя бы один
    # supplier_prices с raw_name (raw_name появился в миграции 022).
    # При --supplier — фильтр по supplier_id'ам; иначе все.
    sp_filter = ""
    params: dict = {"category": category}
    if supplier_ids:
        sp_filter = " AND sp.supplier_id = ANY(:supplier_ids)"
        params["supplier_ids"] = supplier_ids
    if component_id is not None:
        sp_filter += " AND c.id = :component_id"
        params["component_id"] = component_id

    sql = f"""
        SELECT DISTINCT {cols}
          FROM {table} c
          JOIN supplier_prices sp
            ON sp.category = :category AND sp.component_id = c.id
         WHERE ({where_null})
           AND sp.raw_name IS NOT NULL
           {sp_filter}
         ORDER BY c.id
    """
    component_rows = session.execute(text(sql), params).mappings().all()

    if not component_rows:
        return []

    # Собираем все raw_name'ы для этих компонентов одним запросом.
    ids = [r["id"] for r in component_rows]
    raw_filter = ""
    raw_params: dict = {"category": category, "ids": ids}
    if supplier_ids:
        raw_filter = " AND supplier_id = ANY(:supplier_ids)"
        raw_params["supplier_ids"] = supplier_ids

    raw_rows = session.execute(
        text(
            f"""
            SELECT component_id, raw_name
              FROM supplier_prices
             WHERE category = :category
               AND component_id = ANY(:ids)
               AND raw_name IS NOT NULL
               {raw_filter}
            """
        ),
        raw_params,
    ).mappings().all()

    raw_by_component: dict[int, list[str]] = {}
    for r in raw_rows:
        raw_by_component.setdefault(r["component_id"], []).append(r["raw_name"])

    result: list[dict] = []
    for row in component_rows:
        d = dict(row)
        names = raw_by_component.get(d["id"], [])
        # сортировка по убыванию длины — длиннее обычно информативнее
        names = sorted(set(n for n in names if n and n.strip()), key=len, reverse=True)
        d["raw_names"] = names
        result.append(d)
    return result


# -------------------------------------------------------------------------
# Извлечение и агрегация
# -------------------------------------------------------------------------


def _aggregate_extractions(
    extract_fn,
    *,
    raw_names: list[str],
    model: str | None,
    required: list[str],
    null_fields: set[str],
) -> tuple[dict[str, ExtractedField], list[dict]]:
    """Прогоняет экстрактор по каждому источнику текста и агрегирует результат.

    Возвращает:
      - dict {field_name: ExtractedField} — выбранное значение для каждого NULL-поля;
      - list of conflicts: [{field, value_a, source_a, value_b, source_b}, ...]
        — записи о найденных РАЗНЫХ значениях из разных raw_name (для отчёта).

    Алгоритм:
      1. Прогоняем экстрактор по каждому raw_name (отсортированы по убыванию
         длины) и по model.
      2. Для каждого NULL-поля собираем все полученные значения вместе с
         «откуда взято» (raw_name или 'model:').
      3. Если значений 0 — поле остаётся пустым.
         Если все одинаковые (ExtractedField.value сравнивается) — берём первое.
         Если есть расхождение — берём значение из САМОГО ДЛИННОГО источника
         (raw_names[0] идёт первым, model — самый последний); фиксируем
         конфликт в списке.
    """
    # sources: список кортежей (label, text), сначала длинные raw_names, потом model.
    sources: list[tuple[str, str]] = [(name, name) for name in raw_names]
    if model and (not raw_names or model not in raw_names):
        sources.append((f"model:{model}", model))

    # Прогоняем экстрактор по каждому источнику.
    per_source: list[tuple[str, dict[str, ExtractedField]]] = []
    for label, text_src in sources:
        try:
            extracted = extract_fn(text_src)
        except Exception as exc:  # экстрактор не должен падать, но защитимся
            logger.warning("Экстрактор упал на %r: %s", label, exc)
            continue
        # оставляем только обязательные и не-None
        cleaned = {
            k: v for k, v in extracted.items()
            if k in required and v.value is not None
        }
        per_source.append((label, cleaned))

    chosen: dict[str, ExtractedField] = {}
    conflicts: list[dict] = []

    for fname in null_fields:
        # ((label, ef), ...) — отбрасываем те источники, в которых поле не извлеклось.
        candidates = [(lbl, ext[fname]) for lbl, ext in per_source if fname in ext]
        if not candidates:
            continue

        # Если все значения совпадают — конфликта нет, берём первое.
        # Сравнение значений: для list/tuple/scalar Python `==` корректно.
        first_value = candidates[0][1].value
        all_same = all(_values_equal(c[1].value, first_value) for c in candidates)
        if all_same:
            chosen[fname] = candidates[0][1]
            continue

        # Конфликт: берём значение от первого источника (самый длинный raw_name)
        # и записываем расхождение.
        chosen[fname] = candidates[0][1]
        # Фиксируем не уникальные (label, value)-пары, чтобы было понятно,
        # какие именно raw_name дали разные значения.
        seen_values = []
        for lbl, ef in candidates:
            v = ef.value
            if not any(_values_equal(v, sv) for _, sv in seen_values):
                seen_values.append((lbl, v))
        conflicts.append({
            "field":   fname,
            "values":  seen_values,   # [(label, value), ...]
        })

    return chosen, conflicts


def _values_equal(a, b) -> bool:
    """Безопасное сравнение значений из ExtractedField. Списки сравниваются
    как множества (порядок сокетов в supported_sockets может различаться)."""
    if isinstance(a, list) and isinstance(b, list):
        return sorted(map(str, a)) == sorted(map(str, b))
    return a == b


# -------------------------------------------------------------------------
# Основная функция
# -------------------------------------------------------------------------


_ALL_CATEGORIES = ("cpu", "motherboard", "ram", "gpu", "storage", "case", "psu", "cooler")


def run(
    *,
    categories: list[str] | None = None,
    supplier: str | None = None,
    component_id: int | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
) -> RunReport:
    """Прогоняет regex-обогащение по supplier_prices.raw_name.

    - categories:   список категорий ('cpu','ram',...). None ⇒ все 8.
    - supplier:     код поставщика ('ocs', 'merlion', ..., 'all'/None).
    - component_id: ограничить ОДНИМ компонентом (для отладки).
    - dry_run:      не писать в БД.
    - batch_size:   размер «коммитов» при больших прогонах.

    Возвращает RunReport.
    """
    cats = list(categories) if categories else list(_ALL_CATEGORIES)
    report = RunReport()

    session = SessionLocal()
    try:
        supplier_ids: list[int] | None = None
        if supplier and supplier != "all":
            supplier_ids = _resolve_supplier_ids(session, supplier)
            if not supplier_ids:
                logger.warning(
                    "Не нашли supplier_id для кода %r — прогон даст 0 кандидатов.",
                    supplier,
                )

        for cat in cats:
            if cat not in EXTRACTORS:
                logger.warning("Категория %r не реализована — пропуск.", cat)
                continue
            stats = _run_one_category(
                session,
                category=cat,
                supplier_ids=supplier_ids,
                component_id=component_id,
                dry_run=dry_run,
                batch_size=batch_size,
            )
            report.by_category[cat] = stats

    finally:
        session.close()

    return report


def _run_one_category(
    session,
    *,
    category: str,
    supplier_ids: list[int] | None,
    component_id: int | None,
    dry_run: bool,
    batch_size: int,
) -> CategoryStats:
    extract_fn = EXTRACTORS[category]
    required = REQUIRED_FIELDS[category]
    stats = CategoryStats(category=category, field_stats={f: 0 for f in required})

    components = _collect_components(
        session,
        category=category,
        supplier_ids=supplier_ids,
        component_id=component_id,
    )
    stats.candidates = len(components)

    counter = 0
    for comp in components:
        null_fields = {f for f in required if comp.get(f) is None}
        if not null_fields:
            continue

        chosen, conflicts = _aggregate_extractions(
            extract_fn,
            raw_names=comp["raw_names"],
            model=comp.get("model"),
            required=required,
            null_fields=null_fields,
        )

        # Запоминаем конфликты для отчёта (не более 50 на категорию,
        # чтобы не съесть память).
        for c in conflicts:
            if len(stats.conflicts) < 50:
                stats.conflicts.append({
                    "component_id": comp["id"],
                    **c,
                })

        if not chosen:
            # Ничего нового не извлекли — компонент остаётся с NULL.
            stats.remaining_with_null += 1
            continue

        if dry_run:
            # Считаем как «было бы записано».
            stats.components_updated += 1
            for f, ef in chosen.items():
                if comp.get(f) is None and ef.value is not None:
                    stats.fields_written += 1
                    stats.field_stats[f] += 1
            # Сколько полей всё ещё останутся NULL после dry-run?
            after_null = null_fields - {f for f, ef in chosen.items() if ef.value is not None}
            if after_null:
                stats.remaining_with_null += 1
            continue

        # Боевой режим: каждый компонент — savepoint.
        savepoint = session.begin_nested()
        try:
            written = apply_enrichment(
                session,
                category,
                comp["id"],
                chosen,
                comp,
                source_detail="from_raw_name",
            )
            savepoint.commit()
        except Exception as exc:
            savepoint.rollback()
            logger.error(
                "category=%s id=%d: ошибка записи — %s", category, comp["id"], exc,
            )
            stats.errors += 1
            continue

        if written:
            stats.components_updated += 1
            stats.fields_written += len(written)
            for f in written:
                stats.field_stats[f] += 1

        # После записи: если в этом компоненте ещё есть NULL обязательные —
        # считаем его как «остался кандидатом для 11.6.2».
        after_null = null_fields - set(written)
        if after_null:
            stats.remaining_with_null += 1

        counter += 1
        if counter % batch_size == 0:
            session.commit()

    if not dry_run:
        session.commit()

    return stats


# -------------------------------------------------------------------------
# Форматирование отчёта
# -------------------------------------------------------------------------


def format_report(report: RunReport, *, dry_run: bool) -> str:
    """Человекочитаемый отчёт прогона: по категориям + сводка."""
    lines: list[str] = []
    lines.append("=" * 78)
    title = "Regex-обогащение по supplier_prices.raw_name"
    if dry_run:
        title += "   [DRY-RUN]"
    lines.append(title)
    lines.append("=" * 78)

    for cat in _ALL_CATEGORIES:
        stats = report.by_category.get(cat)
        if stats is None:
            continue
        lines.append("")
        lines.append(f"--- {cat.upper()} ---")
        lines.append(f"  Кандидатов:                {stats.candidates}")
        lines.append(f"  Компонентов с записью:     {stats.components_updated}")
        lines.append(f"  Полей записано:            {stats.fields_written}")
        lines.append(f"  Ошибок:                    {stats.errors}")
        lines.append(f"  Осталось с NULL после:     {stats.remaining_with_null}")
        # По полям
        non_zero = [(f, n) for f, n in stats.field_stats.items() if n > 0]
        if non_zero:
            lines.append("  По полям:")
            for f, n in non_zero:
                lines.append(f"    {f:28} {n}")
        # Конфликты — первые 10
        if stats.conflicts:
            lines.append(f"  Конфликтов между raw_name: {len(stats.conflicts)} "
                         f"(показываю первые {min(10, len(stats.conflicts))}):")
            for c in stats.conflicts[:10]:
                vals = ", ".join(f"{v!r}" for _, v in c["values"])
                lines.append(
                    f"    id={c['component_id']:>6}  {c['field']:20} → {vals}"
                )

    lines.append("")
    lines.append("--- ИТОГО ---")
    lines.append(f"  Компонентов обновлено: {report.total_components_updated}")
    lines.append(f"  Полей записано:        {report.total_fields_written}")
    return "\n".join(lines)
