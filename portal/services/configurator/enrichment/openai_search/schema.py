# Константы и целевые поля для OpenAI Web Search.
#
# Переиспользуем целевой набор полей из 2.5Б (claude_code/schema.py):
# набор полей, типы, валидаторы и белый список доменов уже выверены.
# Валидация и отклонение на неподходящих доменах происходит ровно там же.

from __future__ import annotations

from portal.services.configurator.enrichment.claude_code.schema import (  # noqa: F401 — реэкспорт
    ALL_CATEGORIES,
    CASE_PSU_WATTS_FIELD,
    OFFICIAL_DOMAINS,
    TARGET_FIELDS,
)

# -----------------------------------------------------------------------------
# Идентификаторы источников, которые записываются в component_field_sources.
# -----------------------------------------------------------------------------
# Успешное значение от OpenAI Web Search, прошедшее валидацию.
SOURCE_OPENAI = "openai_ws"

# OpenAI не нашёл значение — больше не пробуем, пока не будет --retry.
SOURCE_OPENAI_NO_DATA = "openai_no_data"

# Поле корректно пустое по правилу skip_rules.py — не запрашивать.
SOURCE_NULL_BY_RULE = "null_by_rule"

# Уверенность значения от OpenAI (ниже, чем 0.90 у claude_code, — т.к. это
# автоматический search без человеческого контроля над выбором источника).
DEFAULT_CONFIDENCE = 0.80

# Имя провайдера для записи в api_usage_log.provider.
PROVIDER_NAME = "openai"

# Дефолтная модель (переопределяется через OPENAI_SEARCH_MODEL в .env).
# Актуально на 2026-04: $0.15/M input, $0.60/M output + $0.027 за web_search вызов.
DEFAULT_MODEL = "gpt-4o-mini-search-preview"
