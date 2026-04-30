# Derived-правила обогащения характеристик компонентов (этап 11.6.2.0).
#
# Это набор правил, которые НЕ требуют веб-поиска и НЕ являются простой
# regex-экстракцией из текста (то живёт в regex_sources/). Здесь —
# логические выводы вида «если поле X у компонента Y известно, то поле Z
# вычисляется из X», а также пометка полей как «not_applicable» для
# случаев, когда поле бессмысленно для конкретного компонента.
#
# После 11.6.1 на проде осталось 4660+ NULL по обязательным полям. Часть
# из них логически закрывается без AI:
#
#   Правило 1: cases.has_psu_included = FALSE по маркерам «без БП» в
#              raw_name (расширенный набор маркеров поверх case.py).
#   Правило 2: cases.included_psu_watts помечается not_applicable_no_psu
#              в component_field_sources, если has_psu_included=FALSE.
#              Само поле остаётся NULL — оно бессмысленно для корпусов
#              без БП. Это нужно, чтобы AI-обогащение этих корпусов не
#              трогало.
#   Правило 4: gpus.needs_extra_power = (tdp_watts > 75) — стандарт PCIe
#              даёт 75 Вт, выше нужна доп. линия питания.
#   Правило 5: storages.storage_type = 'SSD', если interface = 'NVMe'.
#              NVMe — разновидность SSD, надёжный derived.
#
# Правила 3 (psu power_watts), 6 (storage form_factor), 7 (storage
# interface) уже покрыты regex_sources/psu.py и regex_sources/storage.py
# в полном объёме — здесь не дублируем.
#
# Все правила пишут в component_field_sources с source='derived' и
# конкретным source_detail для трассировки.

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import text

from app.database import SessionLocal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Правило 1: маркеры «без БП» в raw_name
# ---------------------------------------------------------------------------
#
# Расширенный набор поверх case.py. Тот ловит только «без блока питания»
# и «w/o PSU». Здесь добавляем сокращения и пробельные варианты, которые
# на проде остались NULL после 11.6.1.
NO_PSU_MARKER_RE = re.compile(
    r"без\s*блок"           # без блока (питания/БП)
    r"|без\s*бп"            # без БП, безбп, без  бп
    r"|без\s*пит"           # без питания (без слова «блок»)
    r"|no\s*psu"            # no psu, nopsu
    r"|no\s*ps\b"           # no ps
    r"|w\s*/?\s*o\s*psu",   # w/o psu, w o psu, wopsu
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Структуры отчёта
# ---------------------------------------------------------------------------


@dataclass
class RuleStats:
    """Статистика по одному правилу за прогон."""
    rule_id: str
    description: str
    candidates: int = 0           # компонентов рассмотрено (прошли SELECT)
    fields_written: int = 0       # фактических UPDATE'ов
    not_applicable_marked: int = 0  # пометок «поле не применимо» в CFS
    errors: int = 0


@dataclass
class RuleReport:
    by_rule: dict[str, RuleStats] = field(default_factory=dict)

    @property
    def total_fields_written(self) -> int:
        return sum(s.fields_written for s in self.by_rule.values())

    @property
    def total_not_applicable_marked(self) -> int:
        return sum(s.not_applicable_marked for s in self.by_rule.values())


# ---------------------------------------------------------------------------
# Помощники записи в component_field_sources
# ---------------------------------------------------------------------------


_CFS_UPSERT_SQL = text(
    "INSERT INTO component_field_sources "
    "    (category, component_id, field_name, source, confidence, "
    "     source_url, source_detail, updated_at) "
    "VALUES "
    "    (:category, :component_id, :field_name, 'derived', :confidence, "
    "     NULL, :source_detail, NOW()) "
    "ON CONFLICT (category, component_id, field_name) DO UPDATE SET "
    "    source        = EXCLUDED.source, "
    "    confidence    = EXCLUDED.confidence, "
    "    source_url    = EXCLUDED.source_url, "
    "    source_detail = EXCLUDED.source_detail, "
    "    updated_at    = NOW()"
)


def _upsert_cfs(
    session,
    *,
    category: str,
    component_id: int,
    field_name: str,
    source_detail: str,
    confidence: float = 1.0,
) -> None:
    session.execute(
        _CFS_UPSERT_SQL,
        {
            "category":      category,
            "component_id":  component_id,
            "field_name":    field_name,
            "confidence":    confidence,
            "source_detail": source_detail,
        },
    )


# ---------------------------------------------------------------------------
# Правило 1: cases.has_psu_included = FALSE по маркерам в raw_name
# ---------------------------------------------------------------------------


def _has_no_psu_marker(raw_names) -> bool:
    if not raw_names:
        return False
    for r in raw_names:
        if r and NO_PSU_MARKER_RE.search(r):
            return True
    return False


def _run_rule_1(session, *, dry_run: bool, batch_size: int) -> RuleStats:
    stats = RuleStats(
        rule_id="1",
        description="cases.has_psu_included = FALSE по маркеру «без БП» в raw_name",
    )

    rows = session.execute(text(
        "SELECT c.id AS id, "
        "       array_agg(DISTINCT sp.raw_name) AS raw_names "
        "  FROM cases c "
        "  JOIN supplier_prices sp "
        "    ON sp.category = 'case' AND sp.component_id = c.id "
        " WHERE c.has_psu_included IS NULL "
        "   AND sp.raw_name IS NOT NULL "
        " GROUP BY c.id "
        " ORDER BY c.id"
    )).mappings().all()

    counter = 0
    for row in rows:
        stats.candidates += 1
        if not _has_no_psu_marker(row["raw_names"]):
            continue

        if dry_run:
            stats.fields_written += 1
            continue

        sp = session.begin_nested()
        try:
            session.execute(
                text("UPDATE cases SET has_psu_included = FALSE WHERE id = :id"),
                {"id": row["id"]},
            )
            _upsert_cfs(
                session,
                category="case",
                component_id=row["id"],
                field_name="has_psu_included",
                source_detail="from_no_psu_marker",
            )
            sp.commit()
            stats.fields_written += 1
        except Exception as exc:
            sp.rollback()
            logger.error("rule_1 id=%d: %s", row["id"], exc)
            stats.errors += 1

        counter += 1
        if counter % batch_size == 0:
            session.commit()

    if not dry_run:
        session.commit()

    return stats


# ---------------------------------------------------------------------------
# Правило 2: included_psu_watts not_applicable для cases без БП
# ---------------------------------------------------------------------------
#
# Архитектурное решение: значение в cases.included_psu_watts остаётся
# NULL — поле бессмысленно для корпусов без БП. Но в CFS появляется
# отметка с source_detail='not_applicable_no_psu'. AI-этап
# (11.6.2.1/11.6.2.2) фильтрует записи с такой отметкой и не тратит
# квоту на «честно неприменимые» поля.


def _run_rule_2(session, *, dry_run: bool, batch_size: int) -> RuleStats:
    stats = RuleStats(
        rule_id="2",
        description=(
            "cases.included_psu_watts помечается not_applicable_no_psu, "
            "если has_psu_included=FALSE"
        ),
    )

    candidate_ids = [r[0] for r in session.execute(text(
        "SELECT c.id "
        "  FROM cases c "
        " WHERE c.has_psu_included = FALSE "
        "   AND c.included_psu_watts IS NULL "
        " ORDER BY c.id"
    )).all()]

    if not candidate_ids:
        return stats

    # Уже помеченные (любой источник) — пропускаем, чтобы прогон был
    # идемпотентным и не дёргал updated_at каждый раз.
    already_marked = {
        r[0] for r in session.execute(text(
            "SELECT component_id FROM component_field_sources "
            " WHERE category = 'case' "
            "   AND field_name = 'included_psu_watts' "
            "   AND component_id = ANY(:ids)"
        ), {"ids": candidate_ids}).all()
    }

    counter = 0
    for comp_id in candidate_ids:
        stats.candidates += 1
        if comp_id in already_marked:
            continue

        if dry_run:
            stats.not_applicable_marked += 1
            continue

        sp = session.begin_nested()
        try:
            _upsert_cfs(
                session,
                category="case",
                component_id=comp_id,
                field_name="included_psu_watts",
                source_detail="not_applicable_no_psu",
            )
            sp.commit()
            stats.not_applicable_marked += 1
        except Exception as exc:
            sp.rollback()
            logger.error("rule_2 id=%d: %s", comp_id, exc)
            stats.errors += 1

        counter += 1
        if counter % batch_size == 0:
            session.commit()

    if not dry_run:
        session.commit()

    return stats


# ---------------------------------------------------------------------------
# Правило 4: gpus.needs_extra_power из tdp_watts
# ---------------------------------------------------------------------------
#
# PCIe-слот выдаёт 75 Вт. GPU c TDP > 75 Вт нужна дополнительная линия
# питания — стандартное правило индустрии. Применяем только когда
# tdp_watts известен; если он NULL — правило неприменимо и поле уйдёт
# в AI-этап.


def _run_rule_4(session, *, dry_run: bool, batch_size: int) -> RuleStats:
    stats = RuleStats(
        rule_id="4",
        description="gpus.needs_extra_power = (tdp_watts > 75)",
    )

    rows = session.execute(text(
        "SELECT id, tdp_watts FROM gpus "
        " WHERE tdp_watts IS NOT NULL "
        "   AND needs_extra_power IS NULL "
        " ORDER BY id"
    )).all()

    counter = 0
    for row in rows:
        stats.candidates += 1
        comp_id, tdp = row[0], row[1]
        needs = bool(tdp > 75)

        if dry_run:
            stats.fields_written += 1
            continue

        sp = session.begin_nested()
        try:
            session.execute(
                text("UPDATE gpus SET needs_extra_power = :v WHERE id = :id"),
                {"v": needs, "id": comp_id},
            )
            _upsert_cfs(
                session,
                category="gpu",
                component_id=comp_id,
                field_name="needs_extra_power",
                source_detail="from_tdp_watts",
            )
            sp.commit()
            stats.fields_written += 1
        except Exception as exc:
            sp.rollback()
            logger.error("rule_4 id=%d: %s", comp_id, exc)
            stats.errors += 1

        counter += 1
        if counter % batch_size == 0:
            session.commit()

    if not dry_run:
        session.commit()

    return stats


# ---------------------------------------------------------------------------
# Правило 5: storages.storage_type = 'SSD', если interface = 'NVMe'
# ---------------------------------------------------------------------------
#
# NVMe — это разновидность SSD; HDD по NVMe не существует в потребительском
# сегменте. Правило применяется, когда regex (storage.py) уже выставил
# interface='NVMe', но storage_type остался NULL.


def _run_rule_5(session, *, dry_run: bool, batch_size: int) -> RuleStats:
    stats = RuleStats(
        rule_id="5",
        description="storages.storage_type = 'SSD', если interface = 'NVMe'",
    )

    rows = session.execute(text(
        "SELECT id FROM storages "
        " WHERE interface = 'NVMe' "
        "   AND storage_type IS NULL "
        " ORDER BY id"
    )).all()

    counter = 0
    for row in rows:
        stats.candidates += 1
        comp_id = row[0]

        if dry_run:
            stats.fields_written += 1
            continue

        sp = session.begin_nested()
        try:
            session.execute(
                text("UPDATE storages SET storage_type = 'SSD' WHERE id = :id"),
                {"id": comp_id},
            )
            _upsert_cfs(
                session,
                category="storage",
                component_id=comp_id,
                field_name="storage_type",
                source_detail="from_nvme_interface",
            )
            sp.commit()
            stats.fields_written += 1
        except Exception as exc:
            sp.rollback()
            logger.error("rule_5 id=%d: %s", comp_id, exc)
            stats.errors += 1

        counter += 1
        if counter % batch_size == 0:
            session.commit()

    if not dry_run:
        session.commit()

    return stats


# ---------------------------------------------------------------------------
# Реестр правил
# ---------------------------------------------------------------------------


# (категория, функция). Категория используется в CLI-фильтре --category.
_RULES: dict[str, tuple[str, Callable[..., RuleStats]]] = {
    "1": ("case",    _run_rule_1),
    "2": ("case",    _run_rule_2),
    "4": ("gpu",     _run_rule_4),
    "5": ("storage", _run_rule_5),
}


def all_rule_ids() -> list[str]:
    return list(_RULES.keys())


def rules_for_category(category: str) -> list[str]:
    return [rid for rid, (cat, _) in _RULES.items() if cat == category]


def run(
    *,
    rules: list[str] | None = None,
    dry_run: bool = False,
    batch_size: int = 500,
) -> RuleReport:
    """Прогоняет derived-правила.

    - rules:      список идентификаторов правил из {'1','2','4','5'} или
                  None для всех. Порядок имеет значение: правило 2 опирается
                  на cases.has_psu_included = FALSE, поэтому 1 должно
                  выполниться раньше.
    - dry_run:    не пишет в БД, только считает.
    - batch_size: коммитим транзакцию каждые N записей при больших прогонах.
    """
    if rules is None:
        ids = all_rule_ids()
    else:
        ids = list(rules)

    report = RuleReport()
    session = SessionLocal()
    try:
        for rid in ids:
            entry = _RULES.get(rid)
            if entry is None:
                logger.warning("Правило %r не реализовано — пропуск.", rid)
                continue
            _, fn = entry
            stats = fn(session, dry_run=dry_run, batch_size=batch_size)
            report.by_rule[rid] = stats
    finally:
        session.close()

    return report


# ---------------------------------------------------------------------------
# Форматирование отчёта
# ---------------------------------------------------------------------------


def format_report(report: RuleReport, *, dry_run: bool) -> str:
    lines: list[str] = []
    lines.append("=" * 78)
    title = "Derived-правила обогащения (этап 11.6.2.0)"
    if dry_run:
        title += "   [DRY-RUN]"
    lines.append(title)
    lines.append("=" * 78)

    for rid in sorted(report.by_rule.keys()):
        s = report.by_rule[rid]
        lines.append("")
        lines.append(f"--- Правило {rid}: {s.description} ---")
        lines.append(f"  Кандидатов:                 {s.candidates}")
        lines.append(f"  Полей записано:             {s.fields_written}")
        lines.append(f"  Помечено not_applicable:    {s.not_applicable_marked}")
        lines.append(f"  Ошибок:                     {s.errors}")

    lines.append("")
    lines.append("--- ИТОГО ---")
    lines.append(f"  Полей записано:           {report.total_fields_written}")
    lines.append(f"  Помечено not_applicable:  {report.total_not_applicable_marked}")
    return "\n".join(lines)
