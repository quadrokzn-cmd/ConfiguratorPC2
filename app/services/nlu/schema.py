# Dataclass-ы модуля NLU.
#
# ParsedRequest    — сырое понимание текста (то, что вернул OpenAI-парсер).
# ModelMention     — упоминание конкретной модели компонента в запросе.
# ResolvedMention  — результат fuzzy-поиска ModelMention в БД.
# FinalResponse    — финальный пакет всего pipeline (для менеджера и для API).

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.configurator.schema import BuildRequest, BuildResult


# Допустимые назначения (purpose). Используются как ключи в profiles.PROFILES.
PURPOSES: tuple[str, ...] = ("office", "home", "gaming", "workstation")

# Допустимые категории моделей в ModelMention.
CATEGORIES: tuple[str, ...] = (
    "cpu", "motherboard", "ram", "gpu",
    "storage", "case", "psu", "cooler",
)


@dataclass
class ModelMention:
    """Упоминание модели компонента в свободном тексте.

    query — точная строка, как её сказал менеджер ("Ryzen 5 7600", "RTX 4060",
    "ASUS PRIME B650M-A"). Не нормализуется, fuzzy_lookup сделает это сам.
    """
    category: str
    query: str


@dataclass
class ParsedRequest:
    """Результат работы OpenAI-парсера. Это сырое понимание текста ДО разворота
    в BuildRequest и ДО fuzzy-поиска моделей.

    Если is_empty=True — все остальные поля пустые/None, заполнены только
    clarifying_questions и raw_summary.
    """
    is_empty: bool
    purpose: str | None = None              # 'office' | 'home' | 'gaming' | 'workstation'
    budget_usd: float | None = None
    cpu_manufacturer: str | None = None     # 'intel' | 'amd'
    overrides: dict[str, Any] = field(default_factory=dict)
    model_mentions: list[ModelMention] = field(default_factory=list)
    clarifying_questions: list[str] = field(default_factory=list)
    raw_summary: str = ""


@dataclass
class ResolvedMention:
    """Результат fuzzy-поиска ModelMention в БД."""
    mention: ModelMention
    found_id: int | None = None
    found_model: str | None = None
    found_sku: str | None = None
    is_substitute: bool = False
    note: str | None = None     # сопроводительное предупреждение для менеджера


@dataclass
class FinalResponse:
    """Финальный пакет, отдаваемый pipeline.process_query.

    kind:
      - 'empty'   — запрос пустой, есть только clarifying_questions;
      - 'ok'      — подбор удался (≥1 вариант);
      - 'partial' — подбор удался, но есть отказ по одному из производителей;
      - 'failed'  — подбор не удался (refusal_reason заполнен).
    """
    kind: str
    interpretation: str
    formatted_text: str
    build_request: BuildRequest | None = None
    build_result: BuildResult | None = None
    parsed: ParsedRequest | None = None
    resolved: list[ResolvedMention] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    clarifying_questions: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
