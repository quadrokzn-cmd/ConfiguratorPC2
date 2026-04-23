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


# Номер модели в строке: 4-5 цифр + опциональный буквенный суффикс (F/K/KF/T/...).
# Пример: "12400" → ("12400", ""); "13400F" → ("13400", "F"); "9700K" → ("9700", "K").
# Ищем ПОСЛЕДНЕЕ совпадение в строке: в каталожных названиях часто
# встречается кусок SKU после человеческого имени, и нам нужно брать
# именно модель, которая обычно стоит позже. Для запросов менеджера
# это тоже работает — там номер модели один.
_MODEL_NUMBER_RE = re.compile(r"\b(\d{4,5})([A-Z]{0,4})\b")


def extract_model_number(text_upper: str) -> tuple[str, str] | None:
    """Достаёт (base_number, suffix) из строки; возвращает None, если номер
    модели не найден. На вход подавать уже в верхнем регистре."""
    if not text_upper:
        return None
    matches = list(_MODEL_NUMBER_RE.finditer(text_upper))
    if not matches:
        return None
    last = matches[-1]
    return last.group(1), last.group(2) or ""


def _rank_row(row: dict, req_base: str, req_suffix: str) -> int:
    """Ранжирует кандидата по совпадению номера модели с запросом.
    Меньше = лучше:
        0 — точное совпадение base + suffix (Core i5-12400 при запросе 12400),
        1 — совпал base, suffix различается (Core i5-12400F при запросе 12400),
        2 — номер модели не нашёлся или base другой.
    Дальнейшая сортировка — по min_price (уже сделана в БД), поэтому
    достаточно использовать этот ранг как первичный ключ."""
    mn = extract_model_number((row.get("model") or "").upper())
    if mn is None:
        return 2
    r_base, r_suffix = mn
    if r_base == req_base and r_suffix == req_suffix:
        return 0
    if r_base == req_base:
        return 1
    return 2


def rerank_by_exact_match(
    rows: list[dict], *, query_upper: str,
) -> list[dict]:
    """Переупорядочивает список кандидатов так, чтобы сверху оказались
    точные совпадения по номеру модели (base+suffix). Порядок по цене
    внутри одного ранга сохраняется — он уже задан БД."""
    mn = extract_model_number(query_upper)
    if mn is None:
        # В запросе номера модели нет — ничего не меняем.
        return rows
    req_base, req_suffix = mn
    # stable sort по ключу (rank, original_index)
    indexed = list(enumerate(rows))
    indexed.sort(key=lambda pair: (_rank_row(pair[1], req_base, req_suffix), pair[0]))
    return [r for _, r in indexed]


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
        # Приоритет точному совпадению по номеру модели: если менеджер
        # написал «12400», а в списке есть и «12400», и «12400F» — берём
        # первый, даже если F-версия дешевле.
        rows = rerank_by_exact_match(rows, query_upper=mention.query.upper())
        best = rows[0]
        # Если best по base совпадает с запросом, но по суффиксу нет —
        # это de-facto аналог (например, запрошен 13400, но есть только
        # 13400F). Помечаем как substitute, чтобы менеджер видел.
        req_mn = extract_model_number(mention.query.upper())
        best_mn = extract_model_number((best.get("model") or "").upper())
        is_substitute = bool(
            req_mn and best_mn
            and req_mn[0] == best_mn[0]
            and req_mn[1] != best_mn[1]
        )
        note = None
        if is_substitute:
            note = (
                f"Запрошенная модель «{mention.query}» точно не найдена; "
                f"подобран близкий вариант: {best.get('model')}."
            )
        return ResolvedMention(
            mention=mention,
            found_id=int(best["id"]),
            found_model=best.get("model"),
            found_sku=best.get("sku"),
            is_substitute=is_substitute,
            note=note,
        )

    # 3. Fallback: только «номер модели»
    num = pick_model_number(tokens)
    if num and num not in tokens[:1]:
        # Чтобы не делать тот же поиск повторно: только если номер не был
        # единственным токеном.
        rows = _search_by_tokens(session, mention.category, [num])
        if rows:
            rows = rerank_by_exact_match(rows, query_upper=mention.query.upper())
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
