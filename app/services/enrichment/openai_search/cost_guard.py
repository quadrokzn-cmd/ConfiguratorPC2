# Защита от нежелательных трат при запуске OpenAI Web Search.
#
# Три уровня контроля, все настраиваются из .env:
#   - AUTO_ENRICH_LIMIT (дефолт 20): если кандидатов ≤ лимита, запуск
#     без вопросов;
#   - AUTO_ENRICH_LIMIT < N ≤ AUTO_ENRICH_MAX: интерактивный [да/нет]
#     с показом количества и оценочной стоимости в рублях;
#   - N > AUTO_ENRICH_MAX: жёсткий отказ, подсказка использовать --ids.

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from app.services.enrichment.openai_search import fx

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Переменные окружения и дефолты
# -----------------------------------------------------------------------------
_ENV_LIMIT = "OPENAI_ENRICH_AUTO_LIMIT"
_ENV_MAX = "OPENAI_ENRICH_MAX"
_ENV_COST_PER_CALL = "OPENAI_ENRICH_COST_PER_CALL_USD"

_DEFAULT_LIMIT = 20
_DEFAULT_MAX = 200
# Средняя стоимость одного запроса с web search (gpt-4o-mini-search-preview):
#   ~1500 input * $0.15/M + ~300 output * $0.60/M + $0.027 (web search) ~ $0.0275
_DEFAULT_COST_PER_CALL_USD = 0.0275


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else default
    except ValueError:
        logger.warning("Плохое значение %s=%r, используем дефолт %d", name, raw, default)
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        v = float(raw)
        return v if v > 0 else default
    except ValueError:
        logger.warning("Плохое значение %s=%r, используем дефолт %.4f", name, raw, default)
        return default


@dataclass
class CostEstimate:
    candidates:        int     # сколько позиций планируется обогатить
    cost_per_call_usd: float
    total_usd:         float
    usd_rub_rate:      float
    fx_source:         str
    total_rub:         float
    auto_limit:        int
    hard_max:          int

    def short_summary(self) -> str:
        return (
            f"Кандидатов: {self.candidates}; "
            f"ориентировочно {self.total_usd:.2f} USD ~ {self.total_rub:.0f} ₽ "
            f"(курс {self.usd_rub_rate:.2f} из {self.fx_source})"
        )


def estimate(candidates: int) -> CostEstimate:
    auto_limit = _env_int(_ENV_LIMIT, _DEFAULT_LIMIT)
    hard_max   = _env_int(_ENV_MAX,   _DEFAULT_MAX)
    cpc        = _env_float(_ENV_COST_PER_CALL, _DEFAULT_COST_PER_CALL_USD)

    rate, source = fx.get_usd_rub_rate()
    total_usd = candidates * cpc
    total_rub = total_usd * rate

    return CostEstimate(
        candidates=candidates,
        cost_per_call_usd=cpc,
        total_usd=total_usd,
        usd_rub_rate=rate,
        fx_source=source,
        total_rub=total_rub,
        auto_limit=auto_limit,
        hard_max=hard_max,
    )


def confirm(
    est: CostEstimate,
    *,
    non_interactive: bool,
    prompt_fn = input,
) -> tuple[bool, str]:
    """Решает, разрешён ли запуск. Возвращает (ok, причина).

    non_interactive=True: запуск разрешён только если candidates <= auto_limit.
    Иначе работа в интерактиве: запрос y/n у пользователя при превышении лимита.
    """
    if est.candidates == 0:
        return True, "no_candidates"

    if est.candidates > est.hard_max:
        return False, (
            f"превышен жёсткий потолок OPENAI_ENRICH_MAX={est.hard_max} "
            f"(кандидатов {est.candidates}). Используйте --ids для точечного запуска."
        )

    if est.candidates <= est.auto_limit:
        return True, "within_auto_limit"

    if non_interactive:
        return False, (
            f"кандидатов {est.candidates} > AUTO_LIMIT {est.auto_limit}, "
            "автоматический запуск запрещён; запустите CLI вручную."
        )

    print()
    print("=" * 72)
    print("Подтверждение запуска OpenAI Web Search")
    print("=" * 72)
    print(f"Кандидатов:               {est.candidates}")
    print(f"Стоимость за 1 запрос:    ~{est.cost_per_call_usd:.4f} USD")
    print(f"Оценка общей стоимости:   ~{est.total_usd:.2f} USD ~ {est.total_rub:.0f} ₽")
    print(f"Курс USD/RUB:             {est.usd_rub_rate:.2f}  (источник: {est.fx_source})")
    print(f"AUTO_LIMIT / MAX:         {est.auto_limit} / {est.hard_max}")
    print("-" * 72)

    ans = prompt_fn("Продолжить? [да/нет]: ").strip().lower()
    if ans in {"да", "yes", "y"}:
        return True, "user_confirmed"
    return False, "user_declined"
