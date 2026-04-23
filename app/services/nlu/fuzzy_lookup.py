# Fuzzy-поиск моделей компонентов в БД по тексту от менеджера.
#
# Подход (без внешних библиотек, чистый SQL ILIKE):
#   1. Нормализуем запрос: верхний регистр, чистка лишних слов
#      (GEFORCE, NVIDIA, AMD, INTEL, CORE), замена дефисов/слэшей на пробелы.
#   2. Получаем список значимых токенов (например, ['RYZEN','5','7600']).
#   3. Сначала пробуем точное совпадение по SKU.
#   4. Затем — ILIKE по ВСЕМ токенам сразу: model должна содержать каждый токен.
#      Если найдено несколько вариантов — выбираем самый дешёвый (мин цена
#      из supplier_prices.stock_qty>0).
#   5. Если ничего не нашлось — пробуем последний значимый токен (обычно
#      это «номер модели»: 7600, 4060, 13400F). Если нашли — помечаем как
#      substitute (аналог).
#   6. Если и это не дало ничего — возвращаем ResolvedMention с found_id=None
#      и note «модель не найдена».
#
# Замечание про подбор по характеристикам:
#   В этой задаче решено НЕ подбирать «семейный» аналог автоматически
#   (риск выдать странную замену). Вместо этого pipeline просто оставит
#   соответствующий fixed=None и добавит warning, чтобы менеджер видел,
#   что конкретная модель не зафиксирована.

from __future__ import annotations

import logging
import re
from typing import Iterable

from sqlalchemy import text

from app.services.enrichment.base import ALLOWED_TABLES, CATEGORY_TO_TABLE
from app.services.nlu.schema import ModelMention, ResolvedMention

logger = logging.getLogger(__name__)


# --- Нормализация --------------------------------------------------------

# Слова, которые встречаются и в запросе, и в каталоге, но мешают сужать
# поиск (они слишком общие). Удаляем перед токенизацией.
_STOP_WORDS: frozenset[str] = frozenset({
    "GEFORCE", "RADEON", "NVIDIA", "AMD", "INTEL",
    "CORE",                                 # "Core i5" → "I5"
    "ПРОЦЕССОР", "ВИДЕОКАРТА", "VGA", "CPU", "GPU", "APU",
    "MB", "MOTHERBOARD",
    "ОЗУ", "ПАМЯТЬ", "RAM",
    "НАКОПИТЕЛЬ", "SSD", "HDD",            # для категории storage оставим в отдельных токенах ниже
    "БОКСОВЫЙ", "BOX", "OEM",
    "КУЛЕР", "COOLER",
})

# Слова-замены: левая часть → правая (нормализация синонимов).
_SUBSTITUTIONS: dict[str, str] = {
    "I3-": "I3 ",
    "I5-": "I5 ",
    "I7-": "I7 ",
    "I9-": "I9 ",
    # частые сокращения брендов оставляем как есть; модельные номера
    # обрабатываются токенизацией.
}

# Категории, у которых SSD/HDD — значимые токены (для storage не убираем).
_KEEP_SSD_HDD_FOR: frozenset[str] = frozenset({"storage"})


def _apply_substitutions(s: str) -> str:
    for src, dst in _SUBSTITUTIONS.items():
        s = s.replace(src, dst)
    return s


_NON_ALNUM_RE = re.compile(r"[^A-Z0-9А-ЯЁ]+", re.UNICODE)


def normalize_query(query: str, *, category: str = "") -> list[str]:
    """Превращает «Ryzen 5 7600» → ['RYZEN','5','7600']; «GeForce RTX 4060»
    → ['RTX','4060']; «i5-13400F» → ['I5','13400F']."""
    if not query:
        return []
    s = query.upper().strip()
    s = _apply_substitutions(s)
    # Заменяем всё, кроме букв (лат+кир) и цифр, на пробел
    s = _NON_ALNUM_RE.sub(" ", s)
    raw_tokens = [t for t in s.split() if t]

    stop = _STOP_WORDS
    if category in _KEEP_SSD_HDD_FOR:
        stop = stop - {"SSD", "HDD"}

    return [t for t in raw_tokens if t not in stop]


def pick_model_number(tokens: Iterable[str]) -> str | None:
    """Возвращает «номер модели» — последний токен, в котором есть цифры.
    Используется как fallback-условие при поиске аналога."""
    last_with_digit: str | None = None
    for t in tokens:
        if any(ch.isdigit() for ch in t):
            last_with_digit = t
    return last_with_digit


# --- Поиск в БД ----------------------------------------------------------

def _table_for(category: str) -> str:
    if category not in CATEGORY_TO_TABLE:
        raise ValueError(f"Неизвестная категория: {category}")
    table = CATEGORY_TO_TABLE[category]
    if table not in ALLOWED_TABLES:
        raise RuntimeError(f"Таблица {table} вне whitelist (защита от инъекций)")
    return table


def _search_by_sku(session, table: str, sku: str) -> dict | None:
    row = session.execute(
        text(f"SELECT id, model, sku FROM {table} WHERE UPPER(sku) = UPPER(:q) LIMIT 1"),
        {"q": sku},
    ).mappings().first()
    return dict(row) if row else None


def _search_by_tokens(
    session, category: str, tokens: list[str], *, limit: int = 10,
) -> list[dict]:
    """Возвращает строки, в model которых встречаются ВСЕ токены.
    Сортировка — по минимальной цене у поставщиков с stock>0 (NULL в конец).
    """
    if not tokens:
        return []
    table = _table_for(category)
    where_parts: list[str] = []
    params: dict[str, str] = {}
    for i, tok in enumerate(tokens):
        key = f"tok{i}"
        where_parts.append(f"UPPER(c.model) LIKE :{key}")
        params[key] = f"%{tok}%"
    params["cat"] = category
    where = " AND ".join(where_parts)
    sql = (
        f"SELECT c.id, c.model, c.sku, "
        f"       MIN(sp.price) FILTER (WHERE sp.stock_qty > 0) AS min_price "
        f"FROM {table} c "
        f"LEFT JOIN supplier_prices sp "
        f"  ON sp.category = :cat AND sp.component_id = c.id "
        f"WHERE {where} "
        f"GROUP BY c.id, c.model, c.sku "
        f"ORDER BY min_price NULLS LAST, c.id "
        f"LIMIT {int(limit)}"
    )
    rows = session.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


# --- Главная функция ----------------------------------------------------

def find(session, mention: ModelMention) -> ResolvedMention:
    """Ищет модель в БД по тексту mention.query.

    Стратегия (см. модульный docstring):
      1) точное совпадение по SKU;
      2) ILIKE по всем токенам;
      3) ILIKE по «номеру модели» (substitute);
      4) ничего не найдено.
    """
    tokens = normalize_query(mention.query, category=mention.category)

    if not tokens:
        return ResolvedMention(
            mention=mention,
            note=f"Не удалось разобрать упоминание «{mention.query}» — слишком общее.",
        )

    table = _table_for(mention.category)

    # 1. Точный SKU (если запрос целиком похож на SKU — длинный буквенно-цифровой)
    if len(mention.query) >= 5 and re.fullmatch(r"[A-Za-z0-9\-]+", mention.query):
        row = _search_by_sku(session, table, mention.query)
        if row:
            return ResolvedMention(
                mention=mention,
                found_id=int(row["id"]),
                found_model=row.get("model"),
                found_sku=row.get("sku"),
            )

    # 2. ILIKE по всем токенам
    rows = _search_by_tokens(session, mention.category, tokens)
    if rows:
        best = rows[0]
        return ResolvedMention(
            mention=mention,
            found_id=int(best["id"]),
            found_model=best.get("model"),
            found_sku=best.get("sku"),
        )

    # 3. Fallback: только «номер модели»
    num = pick_model_number(tokens)
    if num and num not in tokens[:1]:
        # Чтобы не делать тот же поиск повторно: только если номер не был
        # единственным токеном.
        rows = _search_by_tokens(session, mention.category, [num])
        if rows:
            best = rows[0]
            return ResolvedMention(
                mention=mention,
                found_id=int(best["id"]),
                found_model=best.get("model"),
                found_sku=best.get("sku"),
                is_substitute=True,
                note=(
                    f"Запрошенная модель «{mention.query}» точно не найдена; "
                    f"подобран близкий вариант: {best.get('model')}."
                ),
            )

    # 4. Ничего не нашли
    return ResolvedMention(
        mention=mention,
        note=(
            f"Модель «{mention.query}» в каталоге не найдена. "
            f"Подбор пройдёт без её фиксации, по характеристикам."
        ),
    )
