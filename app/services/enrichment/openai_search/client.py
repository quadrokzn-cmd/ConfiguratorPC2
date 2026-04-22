# Обёртка над OpenAI API для обогащения одного компонента.
#
# Ключевые моменты:
#   - используем Chat Completions с web_search_options (нативный web search);
#   - промпт формулирует задачу строго: найти ТОЛЬКО поля из to_fill, вернуть
#     JSON в формате, совместимом с валидаторами claude_code (те же ключи
#     value / source_url);
#   - валидация URL и значений — тем же validate_field, что и в 2.5Б;
#   - учитываем токены из usage, считаем стоимость и кладём всё в api_usage_log
#     (делает runner.py — клиент возвращает «сырой» результат вызова).

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from openai import APIError, OpenAI, RateLimitError

from app.services.enrichment.claude_code.schema import OFFICIAL_DOMAINS
from app.services.enrichment.openai_search.schema import DEFAULT_MODEL

logger = logging.getLogger(__name__)


# --- Конфигурация вызова ------------------------------------------------------

_MAX_RETRIES = 3
_RETRY_DELAYS = [2.0, 5.0, 12.0]  # экспоненциальный backoff, секунды

# Цены (USD за 1M токенов) — совпадают с дефолтами в cost_guard.
_PRICE_INPUT_PER_1M  = 0.15
_PRICE_OUTPUT_PER_1M = 0.60


def get_client() -> OpenAI:
    """Возвращает клиент OpenAI, читая ключ из OPENAI_API_KEY."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or api_key.startswith("sk-..."):
        raise RuntimeError(
            "Не задан OPENAI_API_KEY в .env — модуль запустить нельзя."
        )
    return OpenAI(api_key=api_key)


def get_model_name() -> str:
    return os.getenv("OPENAI_SEARCH_MODEL", DEFAULT_MODEL)


# --- Построение промпта -------------------------------------------------------

_SYSTEM_PROMPT = (
    "Ты — помощник сервиса-конфигуратора ПК. Твоя задача — найти ТОЧНЫЕ "
    "характеристики конкретной модели компонента на ОФИЦИАЛЬНОЙ странице "
    "производителя и вернуть строго JSON. Никакой прозы, никаких markdown-"
    "бэктиков, никаких комментариев — ТОЛЬКО JSON-объект с полем `fields`.\n\n"
    "Жёсткие правила:\n"
    "1) Источник — ТОЛЬКО официальный сайт производителя "
    "(разрешённые домены перечислены в поле whitelist_domains запроса).\n"
    "2) Запрещены маркетплейсы (DNS, Citilink, Ozon, Amazon, Newegg), "
    "агрегаторы (3DNews, TechPowerUp, TomsHardware), форумы, Wikipedia.\n"
    "3) Для КАЖДОГО значения укажи source_url — конкретную страницу продукта "
    "на разрешённом домене (https://...).\n"
    "4) Если значение не найдено на оф. сайте — value: null и reason.\n"
    "5) Не выдумывай и не экстраполируй значения «по аналогии»."
)


def build_user_prompt(
    category: str,
    row: dict[str, Any],
    to_fill: list[str],
) -> str:
    """Формирует user-сообщение для одного компонента."""
    domains = sorted(OFFICIAL_DOMAINS)
    payload = {
        "category":      category,
        "id":            row.get("id"),
        "manufacturer":  row.get("manufacturer"),
        "model":         row.get("model"),
        "sku":           row.get("sku"),
        "to_fill":       to_fill,
        "whitelist_domains": domains,
        "output_schema": {
            "fields": {
                "<имя_поля_из_to_fill>": {
                    "value":      "<значение нужного типа, либо null>",
                    "source_url": "https://...конкретная_страница_продукта",
                    "reason":     "<обязательно при value=null>",
                }
            }
        },
    }
    return (
        "Входные данные:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Верни JSON вида {\"fields\": {...}}. "
        "Только поля из to_fill. Для каждого — value и source_url."
    )


# --- Результат одного вызова --------------------------------------------------

@dataclass
class SearchResult:
    status:       str                 # 'ok' / 'no_data' / 'error'
    fields:       dict[str, dict]     # {fname: {"value":..., "source_url":..., "reason":...}}
    tokens_in:    int = 0
    tokens_out:   int = 0
    web_searches: int = 0
    cost_usd:     float = 0.0
    error_msg:    str | None = None
    raw_content:  str = ""
    elapsed_sec:  float = 0.0


_JSON_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _parse_model_response(content: str) -> dict:
    """Извлекает JSON из ответа. Переносит ошибки в status='error' наверху."""
    s = content.strip()
    # Срезаем markdown-обёртку, если модель всё же её выставила
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    # В редких случаях перед/после JSON попадает пояснительная проза —
    # вынимаем первый сбалансированный {...} regex'ом.
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        m = _JSON_RE.search(s)
        if not m:
            raise
        return json.loads(m.group(0))


def _calc_cost_usd(tokens_in: int, tokens_out: int, web_searches: int) -> float:
    from app.services.enrichment.openai_search.cost_guard import (
        _env_float, _DEFAULT_COST_PER_CALL_USD,
        _ENV_COST_PER_CALL,
    )
    # Стоимость web_search = разница между «ценой за вызов» и голыми токенами.
    # Считаем упрощённо: токены по фикс-тарифу + per_call на каждый web search.
    per_call_full = _env_float(_ENV_COST_PER_CALL, _DEFAULT_COST_PER_CALL_USD)
    # «Чистый» токен-компонент из per_call_full — пренебрежимо малая часть,
    # поэтому считаем web_search = per_call_full - (типовые токены).
    # Для точности учитываем фактические токены + per_call * web_searches
    # (минус ~$0.0004 типовых токенов — но это шум уровня 1.5%).
    cost_tokens = (tokens_in / 1_000_000) * _PRICE_INPUT_PER_1M + \
                  (tokens_out / 1_000_000) * _PRICE_OUTPUT_PER_1M
    # Если web_searches неизвестно, считаем 1.
    ws = max(1, web_searches)
    # per_call_full уже включает в себя средние токены, так что просто:
    # стоимость = per_call_full * ws (это и есть «1 компонент = 1 вызов»)
    # даст консервативную верхнюю оценку.
    return per_call_full * ws + cost_tokens  # суммируем, чтобы не занижать


def _count_web_searches(response: Any) -> int:
    """Пытается оценить число web-поисков по структуре ответа.

    На практике API возвращает tool_calls либо annotations с url_citation —
    одного вызова достаточно для нашей стоимостной оценки, но постараемся
    посчитать честно.
    """
    count = 0
    try:
        msg = response.choices[0].message
        tc = getattr(msg, "tool_calls", None) or []
        for call in tc:
            if getattr(call, "type", "") == "web_search":
                count += 1
        ann = getattr(msg, "annotations", None) or []
        # Аннотации url_citation относятся к одному web-вызову каждая —
        # это завышенная оценка, поэтому используем её только если tool_calls
        # отсутствуют.
        if count == 0 and ann:
            count = 1
    except Exception:
        count = 0
    return count or 1  # минимум один вызов, если поиск произошёл


def search_for_component(
    category: str,
    row: dict[str, Any],
    to_fill: list[str],
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> SearchResult:
    """Вызывает OpenAI с web_search_options для одного компонента.

    Возвращает SearchResult. Ошибки транспорта ретраим до _MAX_RETRIES.
    """
    if not to_fill:
        return SearchResult(status="no_data", fields={}, error_msg="empty_to_fill")

    cli = client or get_client()
    mdl = model or get_model_name()
    user_prompt = build_user_prompt(category, row, to_fill)

    last_exc: Exception | None = None
    started = time.time()

    for attempt in range(_MAX_RETRIES):
        try:
            response = cli.chat.completions.create(
                model=mdl,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                web_search_options={},   # поведение по умолчанию
            )
            content = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            t_in = getattr(usage, "prompt_tokens", 0) if usage else 0
            t_out = getattr(usage, "completion_tokens", 0) if usage else 0
            ws_count = _count_web_searches(response)
            cost = _calc_cost_usd(t_in, t_out, ws_count)
            elapsed = time.time() - started

            try:
                parsed = _parse_model_response(content)
            except Exception as exc:
                return SearchResult(
                    status="error",
                    fields={},
                    tokens_in=t_in, tokens_out=t_out,
                    web_searches=ws_count, cost_usd=cost,
                    error_msg=f"bad_json:{exc}",
                    raw_content=content, elapsed_sec=elapsed,
                )

            fields = parsed.get("fields") or {}
            if not isinstance(fields, dict):
                return SearchResult(
                    status="error", fields={},
                    tokens_in=t_in, tokens_out=t_out,
                    web_searches=ws_count, cost_usd=cost,
                    error_msg="bad_fields_shape", raw_content=content,
                    elapsed_sec=elapsed,
                )
            # all nulls? — это «ничего не найдено», не ошибка
            has_any_value = any(
                isinstance(v, dict) and v.get("value") is not None
                for v in fields.values()
            )
            status = "ok" if has_any_value else "no_data"
            return SearchResult(
                status=status, fields=fields,
                tokens_in=t_in, tokens_out=t_out,
                web_searches=ws_count, cost_usd=cost,
                raw_content=content, elapsed_sec=elapsed,
            )

        except RateLimitError as exc:
            last_exc = exc
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            logger.warning("RateLimit (attempt %d/%d), ждём %.1fs", attempt + 1, _MAX_RETRIES, delay)
            time.sleep(delay)
        except APIError as exc:
            last_exc = exc
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            logger.warning("APIError (attempt %d/%d): %s; ждём %.1fs", attempt + 1, _MAX_RETRIES, exc, delay)
            time.sleep(delay)
        except Exception as exc:
            # Непонятная ошибка — не ретраим, возвращаем сразу.
            return SearchResult(
                status="error", fields={},
                error_msg=f"unexpected:{exc}",
                elapsed_sec=time.time() - started,
            )

    return SearchResult(
        status="error", fields={},
        error_msg=f"retries_exhausted:{last_exc}",
        elapsed_sec=time.time() - started,
    )
