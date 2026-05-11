# OpenAI-комментатор: BuildResult → краткий комментарий + список проверок.
#
# Вызывается ТОЛЬКО если подбор удался (есть хотя бы один вариант).
# При любой ошибке вызова/парсинга — возвращает пустой комментарий
# и пустой список проверок (для менеджера это безопасный дефолт).

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from openai import APIError, OpenAI, RateLimitError

from portal.services.configurator.engine.schema import BuildResult, ComponentChoice
from portal.services.configurator.nlu.parser import (
    DEFAULT_MODEL,
    _PRICE_INPUT_PER_1M,
    _PRICE_OUTPUT_PER_1M,
    get_client,
    get_model_name,
)

logger = logging.getLogger(__name__)


_MAX_RETRIES = 2
_RETRY_DELAYS = [1.0, 3.0]

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "commentator_system.txt"


@dataclass
class CommentOutcome:
    comment: str = ""
    checks: list[str] | None = None
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None
    raw_content: str = ""
    elapsed_sec: float = 0.0


def load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


# --- Подготовка краткого описания результата для модели ------------------

def _component_to_dict(c: ComponentChoice) -> dict:
    return {
        "category":     c.category,
        "model":        c.model,
        "manufacturer": c.manufacturer,
        "price_usd":    round(c.chosen.price_usd, 2),
        "quantity":     c.quantity,
        "in_transit":   c.chosen.in_transit,
    }


def build_user_prompt(
    result: BuildResult,
    *,
    budget_usd: float | None,
) -> str:
    """Формирует компактный JSON-снимок результата для комментатора."""
    payload = {
        "budget_usd": round(budget_usd, 2) if budget_usd is not None else None,
        "usd_rub_rate": round(result.usd_rub_rate, 2),
        "variants": [
            {
                "manufacturer": v.manufacturer,
                "total_usd":    round(v.total_usd, 2),
                "total_rub":    round(v.total_rub, 2),
                "used_transit": v.used_transit,
                "components":   [_component_to_dict(c) for c in v.components],
            }
            for v in result.variants
        ],
    }
    return (
        "Результат подбора:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n\n"
        "Верни JSON {\"comment\": \"...\", \"checks\": [...]} строго по правилам."
    )


def _calc_cost_usd(tokens_in: int, tokens_out: int) -> float:
    return (
        (tokens_in / 1_000_000) * _PRICE_INPUT_PER_1M
        + (tokens_out / 1_000_000) * _PRICE_OUTPUT_PER_1M
    )


# --- Главная функция ----------------------------------------------------

def comment(
    result: BuildResult,
    *,
    budget_usd: float | None = None,
    client: OpenAI | None = None,
    model: str | None = None,
) -> CommentOutcome:
    """Возвращает короткий комментарий и список проверок для менеджера.

    При любой ошибке возвращает пустой комментарий и пустой список (это
    безопасно: формат текста и без AI-комментария остаётся читаемым).
    """
    if not result.variants:
        return CommentOutcome(checks=[])

    cli = client or get_client()
    mdl = model or get_model_name()
    system_prompt = load_system_prompt()
    user_prompt = build_user_prompt(result, budget_usd=budget_usd)

    started = time.time()
    last_exc: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            response = cli.chat.completions.create(
                model=mdl,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0,
            )
            content = response.choices[0].message.content or ""
            usage = getattr(response, "usage", None)
            t_in = getattr(usage, "prompt_tokens", 0) if usage else 0
            t_out = getattr(usage, "completion_tokens", 0) if usage else 0
            cost = _calc_cost_usd(t_in, t_out)
            elapsed = time.time() - started

            try:
                payload = json.loads(content)
            except json.JSONDecodeError as exc:
                logger.warning("Комментатор: невалидный JSON: %s", exc)
                return CommentOutcome(
                    checks=[], cost_usd=cost,
                    tokens_in=t_in, tokens_out=t_out,
                    error=f"bad_json:{exc}",
                    raw_content=content, elapsed_sec=elapsed,
                )

            text_comment = payload.get("comment", "")
            if not isinstance(text_comment, str):
                text_comment = ""
            checks_raw = payload.get("checks", [])
            checks: list[str] = []
            if isinstance(checks_raw, list):
                for item in checks_raw:
                    if isinstance(item, str) and item.strip():
                        checks.append(item.strip())

            return CommentOutcome(
                comment=text_comment.strip(),
                checks=checks,
                cost_usd=cost,
                tokens_in=t_in, tokens_out=t_out,
                raw_content=content, elapsed_sec=elapsed,
            )

        except RateLimitError as exc:
            last_exc = exc
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            logger.warning(
                "Комментатор: RateLimit (попытка %d/%d), ждём %.1fs",
                attempt + 1, _MAX_RETRIES, delay,
            )
            time.sleep(delay)
        except APIError as exc:
            last_exc = exc
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            logger.warning(
                "Комментатор: APIError (попытка %d/%d): %s; ждём %.1fs",
                attempt + 1, _MAX_RETRIES, exc, delay,
            )
            time.sleep(delay)
        except Exception as exc:
            logger.exception("Комментатор: непредвиденная ошибка")
            return CommentOutcome(
                checks=[], error=f"unexpected:{exc}",
                elapsed_sec=time.time() - started,
            )

    return CommentOutcome(
        checks=[],
        error=f"retries_exhausted:{last_exc}",
        elapsed_sec=time.time() - started,
    )
