"""Generic helper'ы для merge `attrs_jsonb` и `attrs_source`.

Backlog #10 (2026-05-12): importer из `enrichment/auctions/done/` ранее
делал полную перезапись `attrs_jsonb` и `attrs_source` через
`UPDATE = CAST(:attrs AS JSONB)` / `attrs_source='claude_code'`. Это
теряло данные двух типов:

1. **n/a-затирка**. Если done прислал `n/a`, а в БД уже было реальное
   значение (от `regex_name` или `manual`), оно стиралось.
2. **Потеря тегов источников**. `attrs_source = 'claude_code+regex_name'`
   после повторного импорта становился `'claude_code'`.

Функции ниже — pure, не зависят ни от БД, ни от схемы аукционов. Их
можно переиспользовать для любых будущих per-key-merge сценариев, где
есть "слабые" (n/a) и "сильные" значения, плюс `+`-разделённые
теги источников.

Семантика (под importer-сценарий — done побеждает не-n/a-значением):

- `merge_attrs(existing, incoming)`: для каждого ключа из `incoming`
  - если `incoming[k] == "n/a"` — НЕ затирает существующее не-`n/a`;
    заполняет только отсутствующее/`None`-значение как `n/a`;
  - если `incoming[k] != "n/a"` — обновляет ключ в `existing`
    (claude_code-источник считается авторитетнее для конкретных
    значений; n/a — это "не нашли", и оно слабее любого конкретного).

- `merge_source(existing, incoming)`: union через `+`-разделитель.
  - порядок появления сохраняется, дубликаты убираются;
  - `manual` НЕ имеет специального статуса в этой generic-функции —
    он живёт как обычный тег и сохраняется при объединении.
"""

from __future__ import annotations

NA = "n/a"


def merge_attrs(existing: dict, incoming: dict) -> dict:
    """Per-key merge атрибутов.

    Правила (см. модульный docstring):
    - `incoming[k] == NA`: не затирает не-NA в `existing`;
       если ключа в `existing` нет — записывает `NA`.
    - `incoming[k] != NA`: всегда обновляет `existing[k]`.

    Возвращает новый dict; вход не модифицируется.
    """
    new_attrs = dict(existing)
    for key, incoming_value in incoming.items():
        existing_value = new_attrs.get(key)
        if incoming_value == NA:
            if existing_value is None:
                new_attrs[key] = NA
            # иначе: не трогаем не-NA-значение (n/a-protection)
            continue
        new_attrs[key] = incoming_value
    return new_attrs


def merge_source(existing: str | None, incoming: str) -> str:
    """Union `+`-разделённых тегов источников.

    Примеры:
        merge_source(None, "claude_code")              -> "claude_code"
        merge_source("", "claude_code")                -> "claude_code"
        merge_source("regex_name", "claude_code")      -> "regex_name+claude_code"
        merge_source("claude_code+regex_name", "claude_code")
                                                       -> "claude_code+regex_name"
        merge_source("manual", "claude_code")          -> "manual+claude_code"
        merge_source("manual+regex_name", "claude_code")
                                                       -> "manual+regex_name+claude_code"

    Порядок появления сохраняется, дубликаты убираются.
    """
    if not existing:
        return incoming
    parts = existing.split("+")
    if incoming in parts:
        return existing
    parts.append(incoming)
    return "+".join(parts)
