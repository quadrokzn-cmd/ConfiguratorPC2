# OpenAI-парсер: свободный текст менеджера → ParsedRequest.
#
# Используется обычная Chat Completions без web search. Модель —
# gpt-4o-mini (по умолчанию), параметризуется через переменную окружения
# OPENAI_NLU_MODEL.
#
# Ключевые моменты:
#   - ответ модели — strict JSON (response_format={"type":"json_object"});
#   - валидация структуры: при любой ошибке (нет ключей, не те типы,
#     невалидный JSON) → fallback на «пустой запрос» с дефолтными
#     уточняющими вопросами; в этом случае всё равно возвращаем
#     ParseOutcome.cost_usd по фактическим токенам, а ошибку кладём
#     в parse_error;
#   - актуальный курс USD/RUB передаётся в user-сообщение, чтобы парсер
#     корректно конвертировал бюджет в USD;
#   - sample-промпт читается из prompts/parser_system.txt — легко править.

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from openai import APIError, OpenAI, RateLimitError

from portal.services.configurator.nlu.schema import CATEGORIES, PURPOSES, ModelMention, ParsedRequest

logger = logging.getLogger(__name__)


# --- Конфигурация --------------------------------------------------------

DEFAULT_MODEL = "gpt-4o-mini"
_MAX_RETRIES = 3
_RETRY_DELAYS = [1.0, 3.0, 7.0]

# Цены gpt-4o-mini (USD за 1M токенов).
_PRICE_INPUT_PER_1M = 0.15
_PRICE_OUTPUT_PER_1M = 0.60

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "parser_system.txt"


_DEFAULT_CLARIFYING_QUESTIONS: list[str] = [
    "Для каких задач будет использоваться ПК? (офис, игры, работа с графикой и т.п.)",
    "Какой примерный бюджет?",
]


# --- Результат парсинга --------------------------------------------------

@dataclass
class ParseOutcome:
    """Что вернул парсер.

    parsed:        всегда заполнен (в случае ошибки — fallback на is_empty=True).
    cost_usd:      фактическая стоимость вызова в USD (по токенам).
    tokens_in/out: для журнала.
    parse_error:   None, если всё ок; иначе текст ошибки (для api_usage_log).
    raw_content:   сырой ответ модели (на случай дебага).
    elapsed_sec:   длительность вызова.
    """
    parsed: ParsedRequest
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    parse_error: str | None = None
    raw_content: str = ""
    elapsed_sec: float = 0.0


# --- Клиент --------------------------------------------------------------

def get_client() -> OpenAI:
    """Возвращает клиент OpenAI, читая ключ из OPENAI_API_KEY."""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key or api_key.startswith("sk-..."):
        raise RuntimeError(
            "Не задан OPENAI_API_KEY в .env — модуль NLU запустить нельзя."
        )
    return OpenAI(api_key=api_key)


def get_model_name() -> str:
    return os.getenv("OPENAI_NLU_MODEL", DEFAULT_MODEL)


# --- Чтение системного промпта ------------------------------------------

def load_system_prompt() -> str:
    return _PROMPT_PATH.read_text(encoding="utf-8")


# --- Построение user-сообщения ------------------------------------------

def build_user_prompt(text: str, usd_rub_rate: float) -> str:
    """Формирует user-сообщение для парсера.

    В нём — оригинальный текст менеджера и актуальный курс USD/RUB,
    чтобы парсер мог корректно конвертировать рубли в доллары.
    """
    return (
        f"Текущий курс ЦБ РФ: 1 USD = {usd_rub_rate:.2f} RUB. "
        f"Используй его для конвертации бюджета из рублей в USD.\n\n"
        f"Текст заявки менеджера:\n«{text.strip()}»\n\n"
        f"Верни JSON по описанной выше схеме."
    )


# --- Валидация ответа модели --------------------------------------------

class ParseValidationError(ValueError):
    pass


_OVERRIDE_INT_KEYS = (
    "cpu_min_cores", "cpu_min_threads", "ram_min_gb",
    "ram_min_freq_mhz", "storage_min_gb", "gpu_min_vram_gb",
)
_OVERRIDE_FLOAT_KEYS = ("cpu_min_base_ghz",)
_OVERRIDE_STR_KEYS = ("ram_memory_type", "storage_type")
_OVERRIDE_BOOL_KEYS = ("gpu_required",)

# Multi-storage NLU (backlog #7): ключ overrides.storages — массив словарей
# вида {"min_gb": <int|null>, "type": "SSD"|"HDD"|null}. Если задан и непустой —
# заменяет одиночные storage_min_gb/storage_type. Если отсутствует или []
# — работает старый одиночный путь.
_STORAGES_KEY = "storages"
_VALID_STORAGE_TYPES = ("SSD", "HDD")


def _validate_storages(raw: Any) -> list[dict[str, Any]]:
    """Валидирует overrides.storages. Возвращает чистый список словарей
    {min_gb: int | None, preferred_type: 'SSD' | 'HDD' | None}.

    Битые элементы (не dict, отрицательные значения, неизвестные типы)
    отфильтровываются — это анти-fallback-стратегия, чтобы одиночный
    кривой элемент не валил весь подбор. Если после фильтрации пусто —
    возвращаем []."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ParseValidationError("overrides.storages: ожидался массив")

    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            # битый элемент — игнорируем, не падаем
            continue
        entry: dict[str, Any] = {}
        v = item.get("min_gb")
        if v is not None:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ParseValidationError(
                    f"overrides.storages[{i}].min_gb: ожидалось число"
                )
            if v < 0:
                raise ParseValidationError(
                    f"overrides.storages[{i}].min_gb: отрицательное значение"
                )
            entry["min_gb"] = int(v)
        t = item.get("type")
        if t is not None:
            if not isinstance(t, str):
                raise ParseValidationError(
                    f"overrides.storages[{i}].type: ожидалась строка"
                )
            t = t.strip()
            if t in _VALID_STORAGE_TYPES:
                entry["preferred_type"] = t
        if entry:
            out.append(entry)

    return out


def _validate_overrides(raw: Any) -> dict[str, Any]:
    """Возвращает чистый словарь overrides — только валидные значения."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ParseValidationError("overrides должен быть объектом")

    out: dict[str, Any] = {}

    for k in _OVERRIDE_INT_KEYS:
        v = raw.get(k)
        if v is None:
            continue
        if isinstance(v, bool):  # bool — подкласс int, отделяем
            raise ParseValidationError(f"overrides.{k}: ожидалось число")
        if not isinstance(v, (int, float)):
            raise ParseValidationError(f"overrides.{k}: ожидалось число")
        if v < 0:
            raise ParseValidationError(f"overrides.{k}: отрицательное значение")
        out[k] = int(v)

    for k in _OVERRIDE_FLOAT_KEYS:
        v = raw.get(k)
        if v is None:
            continue
        if isinstance(v, bool):
            raise ParseValidationError(f"overrides.{k}: ожидалось число")
        if not isinstance(v, (int, float)):
            raise ParseValidationError(f"overrides.{k}: ожидалось число")
        if v < 0:
            raise ParseValidationError(f"overrides.{k}: отрицательное значение")
        out[k] = float(v)

    for k in _OVERRIDE_STR_KEYS:
        v = raw.get(k)
        if v is None:
            continue
        if not isinstance(v, str):
            raise ParseValidationError(f"overrides.{k}: ожидалась строка")
        out[k] = v.strip()

    for k in _OVERRIDE_BOOL_KEYS:
        v = raw.get(k)
        if v is None:
            continue
        if not isinstance(v, bool):
            raise ParseValidationError(f"overrides.{k}: ожидался bool")
        out[k] = v

    # Дополнительная санация значений
    if out.get("ram_memory_type") not in (None, "DDR4", "DDR5"):
        # Если модель вернула что-то странное — игнорируем поле, не падаем.
        out.pop("ram_memory_type", None)
    if out.get("storage_type") not in (None, "SSD", "HDD"):
        out.pop("storage_type", None)

    # Multi-storage (backlog #7): отдельная валидация storages-массива.
    storages = _validate_storages(raw.get(_STORAGES_KEY))
    if storages:
        out[_STORAGES_KEY] = storages

    return out


def _validate_mentions(raw: Any) -> list[ModelMention]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ParseValidationError("model_mentions должен быть массивом")
    mentions: list[ModelMention] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ParseValidationError(f"model_mentions[{i}]: ожидался объект")
        cat = item.get("category")
        q = item.get("query")
        if not isinstance(cat, str) or cat not in CATEGORIES:
            raise ParseValidationError(
                f"model_mentions[{i}].category: должно быть одно из {CATEGORIES}"
            )
        if not isinstance(q, str) or not q.strip():
            raise ParseValidationError(
                f"model_mentions[{i}].query: ожидалась непустая строка"
            )
        mentions.append(ModelMention(category=cat, query=q.strip()))
    return mentions


def _validate_clarifying(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ParseValidationError("clarifying_questions должен быть массивом")
    out: list[str] = []
    for i, q in enumerate(raw):
        if not isinstance(q, str):
            raise ParseValidationError(f"clarifying_questions[{i}]: не строка")
        q = q.strip()
        if q:
            out.append(q)
    return out


def validate_response(payload: Any) -> ParsedRequest:
    """Преобразует распарсенный JSON-объект в ParsedRequest.

    Кидает ParseValidationError при любой структурной ошибке.
    """
    if not isinstance(payload, dict):
        raise ParseValidationError("ответ модели не объект")

    is_empty_raw = payload.get("is_empty")
    if not isinstance(is_empty_raw, bool):
        raise ParseValidationError("is_empty: ожидался bool")

    purpose = payload.get("purpose")
    if purpose is not None:
        if not isinstance(purpose, str) or purpose not in PURPOSES:
            raise ParseValidationError(
                f"purpose: должно быть null или одно из {PURPOSES}"
            )

    budget = payload.get("budget_usd")
    if budget is not None:
        if isinstance(budget, bool) or not isinstance(budget, (int, float)):
            raise ParseValidationError("budget_usd: ожидалось число или null")
        if budget < 0:
            raise ParseValidationError("budget_usd: отрицательное значение")
        budget = float(budget)

    mfr = payload.get("cpu_manufacturer")
    if mfr is not None:
        if not isinstance(mfr, str) or mfr not in ("intel", "amd"):
            raise ParseValidationError(
                "cpu_manufacturer: должно быть null, 'intel' или 'amd'"
            )

    overrides = _validate_overrides(payload.get("overrides"))
    mentions = _validate_mentions(payload.get("model_mentions"))
    clarifying = _validate_clarifying(payload.get("clarifying_questions"))

    raw_summary = payload.get("raw_summary") or ""
    if not isinstance(raw_summary, str):
        raise ParseValidationError("raw_summary: ожидалась строка")

    return ParsedRequest(
        is_empty=is_empty_raw,
        purpose=purpose,
        budget_usd=budget,
        cpu_manufacturer=mfr,
        overrides=overrides,
        model_mentions=mentions,
        clarifying_questions=clarifying,
        raw_summary=raw_summary.strip(),
    )


# --- Стоимость -----------------------------------------------------------

def _calc_cost_usd(tokens_in: int, tokens_out: int) -> float:
    return (
        (tokens_in / 1_000_000) * _PRICE_INPUT_PER_1M
        + (tokens_out / 1_000_000) * _PRICE_OUTPUT_PER_1M
    )


# --- Fallback на пустой запрос ------------------------------------------

def fallback_empty_parsed() -> ParsedRequest:
    return ParsedRequest(
        is_empty=True,
        clarifying_questions=list(_DEFAULT_CLARIFYING_QUESTIONS),
        raw_summary="",
    )


# --- Главная функция парсера --------------------------------------------

def parse(
    text: str,
    usd_rub_rate: float,
    *,
    client: OpenAI | None = None,
    model: str | None = None,
) -> ParseOutcome:
    """Парсит свободный текст в ParsedRequest.

    При любой ошибке валидации/JSON/транспорта возвращает ParseOutcome
    с fallback-ParsedRequest (is_empty=True), чтобы pipeline мог отдать
    менеджеру уточняющие вопросы.
    """
    if not text or not text.strip():
        # Пустой текст — даже OpenAI не дёргаем
        return ParseOutcome(parsed=fallback_empty_parsed())

    cli = client or get_client()
    mdl = model or get_model_name()
    system_prompt = load_system_prompt()
    user_prompt = build_user_prompt(text, usd_rub_rate)

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
                logger.warning("Парсер: невалидный JSON от модели: %s", exc)
                return ParseOutcome(
                    parsed=fallback_empty_parsed(),
                    cost_usd=cost, tokens_in=t_in, tokens_out=t_out,
                    parse_error=f"bad_json:{exc}",
                    raw_content=content, elapsed_sec=elapsed,
                )

            try:
                parsed = validate_response(payload)
            except ParseValidationError as exc:
                logger.warning("Парсер: ответ модели не прошёл валидацию: %s", exc)
                return ParseOutcome(
                    parsed=fallback_empty_parsed(),
                    cost_usd=cost, tokens_in=t_in, tokens_out=t_out,
                    parse_error=f"bad_shape:{exc}",
                    raw_content=content, elapsed_sec=elapsed,
                )

            return ParseOutcome(
                parsed=parsed,
                cost_usd=cost, tokens_in=t_in, tokens_out=t_out,
                raw_content=content, elapsed_sec=elapsed,
            )

        except RateLimitError as exc:
            last_exc = exc
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            logger.warning(
                "Парсер: RateLimit (попытка %d/%d), ждём %.1fs",
                attempt + 1, _MAX_RETRIES, delay,
            )
            time.sleep(delay)
        except APIError as exc:
            last_exc = exc
            delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
            logger.warning(
                "Парсер: APIError (попытка %d/%d): %s; ждём %.1fs",
                attempt + 1, _MAX_RETRIES, exc, delay,
            )
            time.sleep(delay)
        except Exception as exc:
            logger.exception("Парсер: непредвиденная ошибка")
            return ParseOutcome(
                parsed=fallback_empty_parsed(),
                parse_error=f"unexpected:{exc}",
                elapsed_sec=time.time() - started,
            )

    return ParseOutcome(
        parsed=fallback_empty_parsed(),
        parse_error=f"retries_exhausted:{last_exc}",
        elapsed_sec=time.time() - started,
    )
