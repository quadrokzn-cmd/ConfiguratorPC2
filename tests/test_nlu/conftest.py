# Общие фикстуры для тестов модуля NLU.
#
# Главная задача — отрезать сетевые вызовы:
#   - OPENAI_API_KEY ставим в фиктивное значение, чтобы parser/commentator
#     не падали при попытке создать клиент;
#   - get_usd_rub_rate всегда возвращает фиксированный курс;
#   - реальный OpenAI клиент НИКОГДА не используется — каждый тест,
#     которому нужен парсер/комментатор, явно подсовывает свой fake.

import os
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _stub_env_and_fx(monkeypatch):
    """Гарантия, что ни один тест не обращается к реальному OpenAI и FX."""
    # Чтобы get_client не падал при создании реального OpenAI()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-stub")

    from portal.services.configurator.enrichment.openai_search import fx
    monkeypatch.setattr(fx, "get_usd_rub_rate", lambda **kw: (90.0, "fallback"))

    # На всякий случай — тот же символ, импортированный в configurator.selector
    from portal.services.configurator.engine import selector as cfg_selector
    monkeypatch.setattr(cfg_selector, "get_usd_rub_rate", lambda: (90.0, "fallback"))

    yield


def make_openai_response(content: str, *, prompt_tokens: int = 100,
                         completion_tokens: int = 50) -> MagicMock:
    """Удобная заглушка под структуру ответа openai SDK."""
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


def fake_openai_client(content: str) -> MagicMock:
    """Возвращает поддельный OpenAI-клиент, у которого
    chat.completions.create возвращает заданный JSON-ответ."""
    cli = MagicMock()
    cli.chat.completions.create.return_value = make_openai_response(content)
    return cli
