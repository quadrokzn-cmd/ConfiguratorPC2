# Точка входа модуля NLU: process_query(text) -> FinalResponse.
#
# Поток:
#   1. Берём актуальный курс USD/RUB через fx (с суточным кэшем).
#   2. ПАРСЕР: текст → ParsedRequest (+ логируем вызов в api_usage_log).
#   3. Если запрос пустой — формируем уточняющие вопросы и выходим.
#   4. FUZZY-LOOKUP: для каждого ModelMention ищем компонент в БД.
#   5. Из ParsedRequest + профиля + найденных моделей собираем BuildRequest.
#   6. Вызываем build_config(req).
#   7. Если получился хотя бы один вариант — КОММЕНТАТОР пишет краткую
#      приписку (+ логируем второй вызов в api_usage_log).
#   8. Форматируем финальный текст и возвращаем FinalResponse.

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from shared.db import SessionLocal
from portal.services.configurator.engine import build_config
from portal.services.configurator.engine.schema import BuildRequest, BuildResult
from portal.services.configurator.enrichment.openai_search.fx import get_usd_rub_rate
from portal.services.configurator.nlu import (
    commentator as commentator_mod,
    formatter,
    fuzzy_lookup,
    parser as parser_mod,
    request_builder,
)
from portal.services.configurator.nlu.profiles import PROFILE_LABELS
from portal.services.configurator.nlu.schema import (
    FinalResponse,
    ParsedRequest,
    ResolvedMention,
)

logger = logging.getLogger(__name__)


_PROVIDER = "openai"


# --- Логирование вызовов в api_usage_log ---------------------------------

def _log_api_call(
    session,
    *,
    model: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    usd_rub_rate: float,
    status: str,
    error_msg: str | None,
    run_id: str | None = None,
) -> None:
    """Пишет одну запись в api_usage_log. category и component_id = NULL —
    это NLU-вызов, не привязан к конкретному компоненту."""
    try:
        session.execute(
            text(
                "INSERT INTO api_usage_log "
                "    (provider, model, category, component_id, tokens_in, tokens_out, "
                "     web_searches, cost_usd, cost_rub, usd_rub_rate, status, error_msg, run_id) "
                "VALUES "
                "    (:provider, :model, NULL, NULL, :tin, :tout, "
                "     0, :cu, :cr, :rate, :status, :err, :run_id)"
            ),
            {
                "provider": _PROVIDER,
                "model":    model,
                "tin":      int(tokens_in),
                "tout":     int(tokens_out),
                "cu":       round(float(cost_usd), 6),
                "cr":       round(float(cost_usd) * float(usd_rub_rate), 2),
                "rate":     round(float(usd_rub_rate), 4),
                "status":   status,
                "err":      error_msg,
                "run_id":   run_id,
            },
        )
        session.commit()
    except Exception as exc:
        # Логирование вызова — не блокирующее: если не удалось — просто
        # фиксируем в обычном logger и идём дальше.
        logger.warning("Не удалось записать api_usage_log: %s", exc)
        try:
            session.rollback()
        except Exception:
            pass


# --- Подготовка человеческой интерпретации запроса -----------------------

def _human_interpretation(parsed: ParsedRequest, req: BuildRequest) -> str:
    """Если парсер вернул raw_summary — берём его. Иначе строим сами
    из развёрнутого BuildRequest."""
    if parsed.raw_summary:
        return parsed.raw_summary

    bits: list[str] = ["Понял запрос:"]
    if parsed.purpose:
        bits.append(PROFILE_LABELS.get(parsed.purpose, parsed.purpose) + " ПК")
    else:
        bits.append("ПК")

    if req.budget_usd is not None:
        bits.append(f"до ${req.budget_usd:.0f}")
    if parsed.cpu_manufacturer:
        bits.append(f"({parsed.cpu_manufacturer.upper()})")

    chunks: list[str] = []
    if req.cpu.min_cores:
        chunks.append(f"CPU ≥{req.cpu.min_cores}C")
    if req.ram.min_gb:
        chunks.append(f"RAM {req.ram.min_gb} ГБ")
    if req.gpu.required:
        if req.gpu.min_vram_gb:
            chunks.append(f"GPU ≥{req.gpu.min_vram_gb} ГБ VRAM")
        else:
            chunks.append("дискретная GPU")
    if req.storage.min_gb:
        chunks.append(f"{req.storage.preferred_type or 'диск'} {req.storage.min_gb} ГБ")

    text_parts = " ".join(bits)
    if chunks:
        text_parts += ", " + ", ".join(chunks)
    return text_parts + "."


# --- Главная точка входа -------------------------------------------------

def process_query(text_query: str) -> FinalResponse:
    """Главная функция модуля. Принимает свободный текст менеджера,
    возвращает FinalResponse со всем необходимым для UI/CLI/API."""
    # 1. Курс USD/RUB
    usd_rub, fx_source = get_usd_rub_rate()

    # 2. Парсер
    parse_result = parser_mod.parse(text_query, usd_rub_rate=usd_rub)
    parsed = parse_result.parsed
    parser_status = "error" if parse_result.parse_error else (
        "ok" if not parsed.is_empty else "no_data"
    )

    total_cost_usd = parse_result.cost_usd

    session = SessionLocal()
    try:
        # Лог вызова парсера
        if parse_result.tokens_in or parse_result.tokens_out or parse_result.parse_error:
            _log_api_call(
                session,
                model=parser_mod.get_model_name(),
                tokens_in=parse_result.tokens_in,
                tokens_out=parse_result.tokens_out,
                cost_usd=parse_result.cost_usd,
                usd_rub_rate=usd_rub,
                status=parser_status,
                error_msg=parse_result.parse_error,
            )

        # 3. Пустой запрос — короткая ветка
        if parsed.is_empty:
            formatted = formatter.format_empty(parsed.clarifying_questions)
            return FinalResponse(
                kind="empty",
                interpretation="Запрос слишком общий.",
                formatted_text=formatted,
                parsed=parsed,
                clarifying_questions=parsed.clarifying_questions,
                cost_usd=total_cost_usd,
            )

        # 4. Fuzzy-поиск моделей
        resolved: list[ResolvedMention] = []
        warnings: list[str] = []
        for mention in parsed.model_mentions:
            try:
                rm = fuzzy_lookup.find(session, mention)
            except Exception as exc:
                logger.exception("Fuzzy-поиск упал: %s", exc)
                rm = ResolvedMention(
                    mention=mention,
                    note=f"Внутренняя ошибка поиска модели «{mention.query}».",
                )
            resolved.append(rm)
            if rm.note:
                warnings.append(rm.note)

        # 5. Сборка BuildRequest
        req = request_builder.build(parsed, resolved=resolved)

        # 6. Подбор
        result: BuildResult = build_config(req)

        # 7. Комментатор — только если есть варианты
        comment_text = ""
        checks: list[str] = []
        if result.variants:
            try:
                co = commentator_mod.comment(
                    result, budget_usd=req.budget_usd,
                )
            except Exception as exc:
                logger.exception("Комментатор упал: %s", exc)
                co = commentator_mod.CommentOutcome(checks=[], error=str(exc))

            comment_text = co.comment or ""
            checks = co.checks or []
            total_cost_usd += co.cost_usd

            # Лог вызова комментатора
            if co.tokens_in or co.tokens_out or co.error:
                comm_status = "error" if co.error else "ok"
                _log_api_call(
                    session,
                    model=commentator_mod.get_model_name(),
                    tokens_in=co.tokens_in,
                    tokens_out=co.tokens_out,
                    cost_usd=co.cost_usd,
                    usd_rub_rate=usd_rub,
                    status=comm_status,
                    error_msg=co.error,
                )
    finally:
        session.close()

    # 8. Форматирование
    interpretation = _human_interpretation(parsed, req)
    formatted = formatter.format_response(
        interpretation=interpretation,
        result=result,
        comment=comment_text,
        checks=checks,
        warnings=warnings,
    )

    return FinalResponse(
        kind=result.status,
        interpretation=interpretation,
        formatted_text=formatted,
        build_request=req,
        build_result=result,
        parsed=parsed,
        resolved=resolved,
        warnings=warnings,
        cost_usd=total_cost_usd,
    )
