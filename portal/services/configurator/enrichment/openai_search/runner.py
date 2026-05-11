# Основной прогон обогащения через OpenAI Web Search.
#
# Режимы:
#   - new_only:   обогатить только SKU, у которых пока нет ни одной записи
#                 в component_field_sources (новые, только что загруженные);
#   - targeted:   --category + --ids (список конкретных компонентов);
#   - retry:      повторить для позиций с source='openai_no_data'.
#
# Поток для каждого компонента:
#   1) определить список полей to_fill (пустые поля из TARGET_FIELDS и которые
#      не помечены как openai_no_data, если это не retry);
#   2) применить skip_rules — соответствующие поля сразу записываются как
#      source='null_by_rule' (без вызова API);
#   3) если to_fill пуст — идти дальше;
#   4) вызвать client.search_for_component;
#   5) валидировать каждое возвращённое поле через claude_code.validators
#      (это даст строгую проверку URL по whitelist и типов);
#   6) записать валидные поля через apply_enrichment (source='openai_ws');
#      поля, где OpenAI явно вернул null — как 'openai_no_data';
#   7) залогировать вызов в api_usage_log.

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from shared.db import SessionLocal
from portal.services.configurator.enrichment.base import CATEGORY_TO_TABLE, ExtractedField
from portal.services.configurator.enrichment.claude_code.schema import TARGET_FIELDS
from portal.services.configurator.enrichment.claude_code.validators import (
    ValidationError,
    validate_field,
)
from portal.services.configurator.enrichment.openai_search.client import (
    SearchResult,
    get_client,
    get_model_name,
    search_for_component,
)
from portal.services.configurator.enrichment.openai_search.cost_guard import confirm, estimate
from portal.services.configurator.enrichment.openai_search.schema import (
    DEFAULT_CONFIDENCE,
    PROVIDER_NAME,
    SOURCE_NULL_BY_RULE,
    SOURCE_OPENAI,
    SOURCE_OPENAI_NO_DATA,
)
from portal.services.configurator.enrichment.openai_search.skip_rules import should_skip
from portal.services.configurator.enrichment.persistence import apply_enrichment

logger = logging.getLogger(__name__)


# --- Отбор кандидатов ---------------------------------------------------------

def _fetch_row(session, category: str, cid: int) -> dict | None:
    table = CATEGORY_TO_TABLE[category]
    fields = TARGET_FIELDS.get(category, []) + ["manufacturer", "model", "sku"]
    cols = ", ".join(["id"] + fields)
    row = session.execute(
        text(f"SELECT {cols} FROM {table} WHERE id = :id"),
        {"id": cid},
    ).mappings().first()
    return dict(row) if row else None


def _pick_to_fill(
    category: str,
    row: dict,
    existing_sources: dict[str, str],
    *,
    retry: bool,
) -> tuple[list[str], list[tuple[str, str]]]:
    """Возвращает (to_fill, skip_by_rule).

    to_fill      — список полей, которые пойдут в OpenAI.
    skip_by_rule — список (field, reason) для записи source='null_by_rule'.
    """
    target_fields = TARGET_FIELDS.get(category, [])
    # для case добавим включённый во второй прогон psu_watts
    if category == "case":
        target_fields = target_fields + ["included_psu_watts"]

    to_fill: list[str] = []
    skip_by_rule: list[tuple[str, str]] = []

    for f in target_fields:
        # Поле уже заполнено — не трогаем
        if row.get(f) is not None:
            continue

        existing_src = existing_sources.get(f)

        # skip_rules срабатывает первым: если поле «правильный NULL»,
        # фиксируем это без вызова API.
        reason = should_skip(category, f, row)
        if reason is not None:
            # уже помечено как null_by_rule — не перезаписываем
            if existing_src != SOURCE_NULL_BY_RULE:
                skip_by_rule.append((f, reason))
            continue

        # Если поле уже помечено openai_no_data — не пробуем, кроме retry
        if existing_src == SOURCE_OPENAI_NO_DATA and not retry:
            continue

        to_fill.append(f)

    return to_fill, skip_by_rule


def _load_existing_sources(session, category: str, cid: int) -> dict[str, str]:
    rows = session.execute(
        text(
            "SELECT field_name, source FROM component_field_sources "
            "WHERE category = :c AND component_id = :id"
        ),
        {"c": category, "id": cid},
    ).all()
    return {r.field_name: r.source for r in rows}


# --- Запись поля с произвольным source (для null_by_rule / openai_no_data) ---

def _write_source_stub(
    session,
    category: str,
    cid: int,
    field_name: str,
    source: str,
    *,
    confidence: float = 1.0,
) -> None:
    """Пишет запись в component_field_sources БЕЗ изменения поля компонента.

    Используется для 'null_by_rule' (зафиксировать, что NULL — это норма)
    и 'openai_no_data' (зафиксировать, что пробовали и не нашли).
    """
    session.execute(
        text(
            "INSERT INTO component_field_sources "
            "    (category, component_id, field_name, source, confidence, source_url, updated_at) "
            "VALUES "
            "    (:c, :id, :f, :s, :conf, NULL, NOW()) "
            "ON CONFLICT (category, component_id, field_name) DO UPDATE SET "
            "    source = EXCLUDED.source, "
            "    confidence = EXCLUDED.confidence, "
            "    source_url = NULL, "
            "    updated_at = NOW()"
        ),
        {
            "c": category, "id": cid, "f": field_name,
            "s": source, "conf": confidence,
        },
    )


# --- Запись в api_usage_log --------------------------------------------------

def _log_usage(
    session,
    *,
    run_id: str,
    model: str,
    category: str,
    component_id: int,
    result: SearchResult,
    usd_rub_rate: float,
) -> None:
    cost_rub = result.cost_usd * usd_rub_rate
    session.execute(
        text(
            "INSERT INTO api_usage_log "
            "    (provider, model, category, component_id, tokens_in, tokens_out, "
            "     web_searches, cost_usd, cost_rub, usd_rub_rate, status, error_msg, run_id) "
            "VALUES "
            "    (:provider, :model, :category, :cid, :tin, :tout, "
            "     :ws, :cu, :cr, :rate, :status, :err, :run_id)"
        ),
        {
            "provider": PROVIDER_NAME,
            "model":    model,
            "category": category,
            "cid":      component_id,
            "tin":      result.tokens_in,
            "tout":     result.tokens_out,
            "ws":       result.web_searches,
            "cu":       round(result.cost_usd, 6),
            "cr":       round(cost_rub, 2),
            "rate":     round(usd_rub_rate, 4),
            "status":   result.status,
            "err":      result.error_msg,
            "run_id":   run_id,
        },
    )


# --- Обработка одного компонента ---------------------------------------------

@dataclass
class RunStats:
    run_id:              str
    model:               str
    candidates_raw:      int = 0    # сколько изначально отобрано
    candidates_after:    int = 0    # после отсева skip_rules и no_data
    processed:           int = 0    # реально отправлено в OpenAI
    dry_run:             bool = False
    fields_written:      int = 0
    fields_rejected:     int = 0
    fields_no_data:      int = 0
    fields_null_by_rule: int = 0
    total_cost_usd:      float = 0.0
    total_cost_rub:      float = 0.0
    errors:              list[str] = field(default_factory=list)


def _process_component(
    session,
    category: str,
    cid: int,
    *,
    run_id: str,
    model: str,
    usd_rub_rate: float,
    stats: RunStats,
    client,
    dry_run: bool,
    retry: bool,
) -> None:
    row = _fetch_row(session, category, cid)
    if row is None:
        stats.errors.append(f"not_found:{category}#{cid}")
        return

    existing = _load_existing_sources(session, category, cid)
    to_fill, skip_by_rule = _pick_to_fill(category, row, existing, retry=retry)

    # 1) null_by_rule — записываем сразу
    for f, reason in skip_by_rule:
        stats.fields_null_by_rule += 1
        if not dry_run:
            _write_source_stub(
                session, category, cid, f, source="null_by_rule", confidence=1.0,
            )

    if not to_fill:
        return

    stats.processed += 1

    # 2) dry-run: не делаем API-вызов, но показываем, что планировали
    if dry_run:
        for f in to_fill:
            logger.info("[dry-run] %s#%d: OpenAI был бы вызван для %s", category, cid, f)
        return

    # 3) реальный вызов
    result = search_for_component(category, row, to_fill, client=client, model=model)

    # 4) лог в api_usage_log (в ЛЮБОМ статусе)
    savepoint = session.begin_nested()
    try:
        _log_usage(
            session,
            run_id=run_id, model=model,
            category=category, component_id=cid,
            result=result, usd_rub_rate=usd_rub_rate,
        )
        savepoint.commit()
    except Exception as exc:
        savepoint.rollback()
        stats.errors.append(f"log_failed:{category}#{cid}:{exc}")

    stats.total_cost_usd += result.cost_usd
    stats.total_cost_rub += result.cost_usd * usd_rub_rate

    if result.status == "error":
        stats.errors.append(f"api_error:{category}#{cid}:{result.error_msg}")
        return

    # 5) валидация + запись
    ef_to_write: dict[str, ExtractedField] = {}
    for f, payload in result.fields.items():
        if f not in to_fill:
            continue  # OpenAI вернул не тот ключ — игнор
        if not isinstance(payload, dict):
            continue
        if payload.get("value") is None:
            # отдельное поле «не нашёл» → помечаем 'openai_no_data'
            stats.fields_no_data += 1
            savepoint = session.begin_nested()
            try:
                _write_source_stub(
                    session, category, cid, f,
                    source=SOURCE_OPENAI_NO_DATA, confidence=0.0,
                )
                savepoint.commit()
            except Exception as exc:
                savepoint.rollback()
                stats.errors.append(f"stub_failed:{category}#{cid}.{f}:{exc}")
            continue

        try:
            vf = validate_field(category, f, payload)
        except ValidationError as exc:
            stats.fields_rejected += 1
            stats.errors.append(
                f"rejected:{category}#{cid}.{f}:{exc}"
            )
            continue

        ef_to_write[f] = ExtractedField(
            value=vf.value,
            source=SOURCE_OPENAI,
            confidence=DEFAULT_CONFIDENCE,
            source_url=vf.source_url,
        )

    # 6) запись валидных полей через apply_enrichment (он пишет только в NULL)
    if ef_to_write:
        savepoint = session.begin_nested()
        try:
            written = apply_enrichment(
                session, category, cid, ef_to_write, row,
            )
            savepoint.commit()
            stats.fields_written += len(written)
        except Exception as exc:
            savepoint.rollback()
            stats.errors.append(f"db_write:{category}#{cid}:{exc}")


# --- Публичные точки входа для CLI --------------------------------------------

def _all_categories() -> list[str]:
    return list(TARGET_FIELDS.keys())


def _list_new_sku_candidates(session) -> list[tuple[str, int]]:
    """Компоненты, у которых в component_field_sources нет ни одной записи.

    Это «новые» SKU: price_loader создал строку (только model/manufacturer/sku),
    ни regex, ни claude_code ещё не трогали. Порядок — по категориям, по id.
    """
    out: list[tuple[str, int]] = []
    for cat in _all_categories():
        table = CATEGORY_TO_TABLE[cat]
        rows = session.execute(
            text(
                f"SELECT c.id FROM {table} c "
                f"WHERE NOT EXISTS ( "
                f"    SELECT 1 FROM component_field_sources s "
                f"    WHERE s.category = :cat AND s.component_id = c.id) "
                f"ORDER BY c.id"
            ),
            {"cat": cat},
        ).all()
        for r in rows:
            out.append((cat, r.id))
    return out


def _list_no_data_candidates(session) -> list[tuple[str, int]]:
    """Компоненты, у которых есть хотя бы одно поле со source='openai_no_data'."""
    rows = session.execute(
        text(
            "SELECT DISTINCT category, component_id "
            "FROM component_field_sources "
            "WHERE source = :s ORDER BY category, component_id"
        ),
        {"s": SOURCE_OPENAI_NO_DATA},
    ).all()
    return [(r.category, r.component_id) for r in rows]


def _list_targeted(session, category: str, ids: list[int]) -> list[tuple[str, int]]:
    return [(category, int(i)) for i in ids]


def run(
    *,
    mode: str,                         # 'new_only' / 'targeted' / 'retry'
    category: str | None = None,
    ids: list[int] | None = None,
    dry_run: bool = False,
    non_interactive: bool = False,
    prompt_fn = input,
) -> RunStats:
    """Главная точка входа."""
    run_id = uuid.uuid4().hex[:16]
    model = get_model_name()
    stats = RunStats(run_id=run_id, model=model, dry_run=dry_run)

    session = SessionLocal()
    try:
        if mode == "new_only":
            candidates = _list_new_sku_candidates(session)
        elif mode == "retry":
            candidates = _list_no_data_candidates(session)
        elif mode == "targeted":
            if not category or not ids:
                raise ValueError("Для mode='targeted' требуются category и ids")
            candidates = _list_targeted(session, category, ids)
        else:
            raise ValueError(f"Неизвестный режим: {mode!r}")

        stats.candidates_raw = len(candidates)

        # Предварительная фильтрация: удаляем компоненты, у которых
        # после применения skip_rules НЕЧЕГО отправлять. Считаем настоящую
        # нагрузку на OpenAI.
        real_queue: list[tuple[str, int, list[str], list[tuple[str, str]]]] = []
        for cat, cid in candidates:
            row = _fetch_row(session, cat, cid)
            if row is None:
                continue
            existing = _load_existing_sources(session, cat, cid)
            to_fill, skips = _pick_to_fill(
                cat, row, existing, retry=(mode == "retry"),
            )
            if to_fill or skips:
                real_queue.append((cat, cid, to_fill, skips))

        stats.candidates_after = sum(1 for _, _, tf, _ in real_queue if tf)

        # --- cost_guard ---
        est = estimate(stats.candidates_after)
        usd_rub_rate = est.usd_rub_rate

        ok, reason = confirm(est, non_interactive=non_interactive, prompt_fn=prompt_fn)
        logger.info(
            "cost_guard: %s (candidates=%d, ~%.2f USD / %.0f ₽, reason=%s)",
            "OK" if ok else "DENY",
            est.candidates, est.total_usd, est.total_rub, reason,
        )
        if not ok:
            stats.errors.append(f"cost_guard_denied:{reason}")
            return stats

        # --- API-клиент (не создаём в dry-run, чтобы не требовать ключ) ---
        client = None if dry_run else get_client()

        # --- основной цикл ---
        for cat, cid, to_fill, skips in real_queue:
            # skip_by_rule записи применяем ВСЕГДА — они не требуют API
            for f, _reason in skips:
                stats.fields_null_by_rule += 1
                if not dry_run:
                    _write_source_stub(
                        session, cat, cid, f,
                        source=SOURCE_NULL_BY_RULE, confidence=1.0,
                    )
            if not to_fill:
                continue
            stats.processed += 1

            if dry_run:
                for f in to_fill:
                    logger.info(
                        "[dry-run] %s#%d: был бы OpenAI-запрос для %s", cat, cid, f,
                    )
                continue

            row = _fetch_row(session, cat, cid)
            result = search_for_component(
                cat, row, to_fill, client=client, model=model,
            )

            # лог в api_usage_log
            savepoint = session.begin_nested()
            try:
                _log_usage(
                    session, run_id=run_id, model=model,
                    category=cat, component_id=cid,
                    result=result, usd_rub_rate=usd_rub_rate,
                )
                savepoint.commit()
            except Exception as exc:
                savepoint.rollback()
                stats.errors.append(f"log_failed:{cat}#{cid}:{exc}")

            stats.total_cost_usd += result.cost_usd
            stats.total_cost_rub += result.cost_usd * usd_rub_rate

            if result.status == "error":
                stats.errors.append(f"api_error:{cat}#{cid}:{result.error_msg}")
                continue

            ef_to_write: dict[str, ExtractedField] = {}
            for f, payload in result.fields.items():
                if f not in to_fill or not isinstance(payload, dict):
                    continue
                if payload.get("value") is None:
                    stats.fields_no_data += 1
                    sp = session.begin_nested()
                    try:
                        _write_source_stub(
                            session, cat, cid, f,
                            source=SOURCE_OPENAI_NO_DATA, confidence=0.0,
                        )
                        sp.commit()
                    except Exception as exc:
                        sp.rollback()
                        stats.errors.append(f"stub_failed:{cat}#{cid}.{f}:{exc}")
                    continue
                try:
                    vf = validate_field(cat, f, payload)
                except ValidationError as exc:
                    stats.fields_rejected += 1
                    stats.errors.append(f"rejected:{cat}#{cid}.{f}:{exc}")
                    continue
                ef_to_write[f] = ExtractedField(
                    value=vf.value,
                    source=SOURCE_OPENAI,
                    confidence=DEFAULT_CONFIDENCE,
                    source_url=vf.source_url,
                )

            if ef_to_write:
                sp = session.begin_nested()
                try:
                    written = apply_enrichment(
                        session, cat, cid, ef_to_write, row,
                    )
                    sp.commit()
                    stats.fields_written += len(written)
                except Exception as exc:
                    sp.rollback()
                    stats.errors.append(f"db_write:{cat}#{cid}:{exc}")

            # коммит после каждого компонента — лог не должен теряться
            session.commit()

        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    return stats


def format_report(stats: RunStats) -> str:
    lines: list[str] = []
    lines.append("=" * 72)
    title = f"OpenAI Web Search — run_id={stats.run_id}, model={stats.model}"
    if stats.dry_run:
        title += "   [DRY-RUN]"
    lines.append(title)
    lines.append("=" * 72)
    lines.append(f"Кандидатов найдено (raw):     {stats.candidates_raw}")
    lines.append(f"Из них реально к API:         {stats.candidates_after}")
    lines.append(f"Обработано компонентов:       {stats.processed}")
    lines.append(f"Полей записано (openai_ws):   {stats.fields_written}")
    lines.append(f"Полей no_data:                {stats.fields_no_data}")
    lines.append(f"Полей отклонено валидацией:   {stats.fields_rejected}")
    lines.append(f"Полей помечено null_by_rule:  {stats.fields_null_by_rule}")
    lines.append(f"Итоговая стоимость:           {stats.total_cost_usd:.4f} USD "
                 f"~ {stats.total_cost_rub:.2f} ₽")

    if stats.errors:
        lines.append("")
        lines.append(f"События с диагностикой: {len(stats.errors)} (первые 20):")
        for e in stats.errors[:20]:
            lines.append(f"  - {e}")
        if len(stats.errors) > 20:
            lines.append(f"  … ещё {len(stats.errors) - 20}")
    return "\n".join(lines)
