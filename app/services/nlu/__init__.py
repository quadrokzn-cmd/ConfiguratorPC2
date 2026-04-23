# Модуль NLU (этап 4): свободный текст менеджера → BuildRequest → ответ.
#
# Точка входа: pipeline.process_query(text) -> FinalResponse.
#
# Состав:
#   - schema.py          — dataclass-ы ParsedRequest, ModelMention, FinalResponse;
#   - profiles.py        — дефолтные профили OFFICE / HOME / GAMING / WORKSTATION;
#   - parser.py          — OpenAI-вызов: текст → ParsedRequest;
#   - fuzzy_lookup.py    — поиск моделей компонентов по тексту в БД (ILIKE);
#   - request_builder.py — ParsedRequest + profile + resolved → BuildRequest;
#   - commentator.py     — OpenAI-вызов: BuildResult → краткий комментарий;
#   - formatter.py       — финальный текст для менеджера;
#   - pipeline.py        — process_query(text) — оркестрация всего модуля.

from app.services.nlu.pipeline import process_query
from app.services.nlu.schema import (
    FinalResponse,
    ModelMention,
    ParsedRequest,
    ResolvedMention,
)

__all__ = [
    "FinalResponse",
    "ModelMention",
    "ParsedRequest",
    "ResolvedMention",
    "process_query",
]
