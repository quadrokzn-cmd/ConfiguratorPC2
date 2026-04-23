# Сервис ручного сопоставления unmapped_supplier_items с компонентами БД.
#
# Работает только с новой таблицей (миграция 009). Все три действия
# админа — merge, confirm_as_new, defer — проходят здесь; веб-роут
# admin_router только декодирует формы и вызывает эти функции.

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.enrichment.base import ALLOWED_TABLES, CATEGORY_TO_TABLE


# Статусы строки unmapped_supplier_items — единый источник истины.
STATUS_PENDING       = "pending"
STATUS_CREATED_NEW   = "created_new"
STATUS_MERGED        = "merged"
STATUS_CONFIRMED_NEW = "confirmed_new"

# Активные статусы — те, что требуют внимания админа.
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_CREATED_NEW)


# ---------------------------------------------------------------------------
# Чтение
# ---------------------------------------------------------------------------

def count_active(session: Session) -> int:
    row = session.execute(
        text(
            "SELECT COUNT(*) AS c FROM unmapped_supplier_items "
            "WHERE status IN ('pending', 'created_new')"
        )
    ).first()
    return int(row.c) if row else 0


@dataclass
class UnmappedRow:
    id: int
    supplier_id: int
    supplier_name: str
    supplier_sku: str
    raw_category: str
    guessed_category: str | None
    brand: str | None
    mpn: str | None
    gtin: str | None
    raw_name: str
    price: float | None
    currency: str | None
    stock: int
    transit: int
    status: str
    notes: str | None
    resolved_component_id: int | None
    created_at: object   # datetime


def list_active(
    session: Session, *,
    supplier_id: int | None = None,
    category: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[UnmappedRow]:
    """Список активных (pending + created_new) строк. Фильтры по
    supplier_id, category, status применяются при наличии."""
    where = ["u.status IN ('pending', 'created_new')"]
    params: dict[str, object] = {}
    if supplier_id is not None:
        where.append("u.supplier_id = :sid")
        params["sid"] = supplier_id
    if category:
        where.append("u.guessed_category = :cat")
        params["cat"] = category
    if status:
        # перезаписываем верхний предикат на конкретный статус
        where[0] = "u.status = :st"
        params["st"] = status

    where_sql = " AND ".join(where)
    params["lim"] = int(limit)
    params["off"] = int(offset)

    rows = session.execute(
        text(
            "SELECT u.id, u.supplier_id, s.name AS supplier_name, "
            "       u.supplier_sku, u.raw_category, u.guessed_category, "
            "       u.brand, u.mpn, u.gtin, u.raw_name, "
            "       u.price, u.currency, u.stock, u.transit, "
            "       u.status, u.notes, u.resolved_component_id, u.created_at "
            "FROM unmapped_supplier_items u "
            "JOIN suppliers s ON s.id = u.supplier_id "
            f"WHERE {where_sql} "
            "ORDER BY u.created_at DESC, u.id DESC "
            "LIMIT :lim OFFSET :off"
        ),
        params,
    ).mappings().all()
    return [UnmappedRow(**dict(r)) for r in rows]


def get_by_id(session: Session, row_id: int) -> UnmappedRow | None:
    row = session.execute(
        text(
            "SELECT u.id, u.supplier_id, s.name AS supplier_name, "
            "       u.supplier_sku, u.raw_category, u.guessed_category, "
            "       u.brand, u.mpn, u.gtin, u.raw_name, "
            "       u.price, u.currency, u.stock, u.transit, "
            "       u.status, u.notes, u.resolved_component_id, u.created_at "
            "FROM unmapped_supplier_items u "
            "JOIN suppliers s ON s.id = u.supplier_id "
            "WHERE u.id = :id"
        ),
        {"id": row_id},
    ).mappings().first()
    return UnmappedRow(**dict(row)) if row else None


def list_suppliers(session: Session) -> list[dict]:
    rows = session.execute(
        text("SELECT id, name FROM suppliers WHERE is_active = TRUE ORDER BY name")
    ).mappings().all()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Действия
# ---------------------------------------------------------------------------

def _table_for(category: str) -> str:
    if category not in CATEGORY_TO_TABLE:
        raise ValueError(f"Неизвестная категория: {category!r}")
    table = CATEGORY_TO_TABLE[category]
    if table not in ALLOWED_TABLES:
        raise RuntimeError(f"Таблица {table} вне whitelist")
    return table


def merge_with_component(
    session: Session, *,
    unmapped_id: int, target_component_id: int, admin_user_id: int,
) -> None:
    """Переносит supplier_prices на target_component_id и, если для
    этой записи был создан скелет-дубликат (status='created_new' или
    resolved_component_id указывает на другой компонент) — удаляет его.

    Три случая:
      а) status='pending' (ambiguous): supplier_prices уже привязан
         к resolved_component_id (первый из кандидатов). Если админ
         выбрал ДРУГОГО кандидата — переносим supplier_prices на него.
      б) status='created_new': supplier_prices привязан к свежесозданному
         скелету. Если target ≠ скелет — переносим supplier_prices и
         удаляем скелет (он не нужен: админ распознал, что это дубликат
         существующего компонента).
      в) target_component_id совпадает с текущим resolved_component_id —
         всё уже правильно привязано, просто меняем статус.
    """
    u = get_by_id(session, unmapped_id)
    if u is None:
        raise ValueError(f"unmapped_supplier_items id={unmapped_id} не найден")
    if u.guessed_category is None:
        raise ValueError(
            f"У записи id={unmapped_id} не задана guessed_category — "
            "нельзя определить таблицу компонента."
        )

    category = u.guessed_category
    table = _table_for(category)

    # Проверяем, что target_component_id существует в нужной таблице.
    exists = session.execute(
        text(f"SELECT id FROM {table} WHERE id = :id"),
        {"id": target_component_id},
    ).first()
    if exists is None:
        raise ValueError(
            f"Компонент id={target_component_id} в таблице {table} не найден."
        )

    current_cid = u.resolved_component_id
    skeleton_to_delete: int | None = None

    # Если был создан скелет (created_new) и админ выбирает другой компонент —
    # скелет нужно удалить после переноса supplier_prices.
    if (
        u.status == STATUS_CREATED_NEW
        and current_cid is not None
        and current_cid != target_component_id
    ):
        skeleton_to_delete = current_cid

    # Перенос supplier_prices (если компонент поменялся).
    if current_cid is not None and current_cid != target_component_id:
        # Сначала подчищаем возможную существующую строку на target
        # (теоретически её нет — supplier_prices уникальна по
        # (supplier_id, category, component_id)), но перестрахуемся:
        # переносим через UPDATE с подменой component_id. Если коллизия —
        # падаем понятной ошибкой.
        session.execute(
            text(
                "UPDATE supplier_prices "
                "SET component_id = :new_cid "
                "WHERE supplier_id = :sid AND category = :cat "
                "  AND component_id = :old_cid"
            ),
            {
                "new_cid": target_component_id,
                "sid":     u.supplier_id,
                "cat":     category,
                "old_cid": current_cid,
            },
        )

    # Удаляем скелет (если нужно). Делаем это ПОСЛЕ переноса
    # supplier_prices, иначе ON DELETE CASCADE снесёт и цену.
    # В наших таблицах FK нет, но порядок всё равно логичнее такой.
    if skeleton_to_delete is not None:
        # Дополнительная проверка: скелет — это тот, что мы создали
        # автоматически (у него обычно manufacturer='unknown'). Но
        # даже если админ назначил другое — у нас статус 'created_new'
        # и resolved_component_id указывает на скелет, так что он точно
        # не используется другими записями unmapped.
        # Проверим, что на этом id нет других supplier_prices:
        other_prices = session.execute(
            text(
                "SELECT COUNT(*) AS c FROM supplier_prices "
                "WHERE category = :cat AND component_id = :id"
            ),
            {"cat": category, "id": skeleton_to_delete},
        ).first()
        if other_prices and int(other_prices.c) == 0:
            session.execute(
                text(f"DELETE FROM {table} WHERE id = :id"),
                {"id": skeleton_to_delete},
            )

    # Обновляем статус unmapped_supplier_items.
    session.execute(
        text(
            "UPDATE unmapped_supplier_items SET "
            "    status                = :st, "
            "    resolved_component_id = :cid, "
            "    resolved_at           = NOW(), "
            "    resolved_by           = :uid "
            "WHERE id = :id"
        ),
        {
            "st":  STATUS_MERGED,
            "cid": target_component_id,
            "uid": admin_user_id,
            "id":  unmapped_id,
        },
    )
    session.commit()


def confirm_as_new(
    session: Session, *, unmapped_id: int, admin_user_id: int,
) -> None:
    """Админ подтверждает, что это отдельный новый товар. Статус
    переходит в 'confirmed_new'; компонент (если был создан скелет)
    остаётся, supplier_prices — тоже."""
    session.execute(
        text(
            "UPDATE unmapped_supplier_items SET "
            "    status      = :st, "
            "    resolved_at = NOW(), "
            "    resolved_by = :uid "
            "WHERE id = :id"
        ),
        {
            "st":  STATUS_CONFIRMED_NEW,
            "uid": admin_user_id,
            "id":  unmapped_id,
        },
    )
    session.commit()


def defer(session: Session, *, unmapped_id: int) -> None:
    """«Разобраться потом» — ничего не меняем, просто noop. Отдельный
    метод оставлен, чтобы роут всегда делал одно и то же: вызвать сервис
    и сделать redirect."""
    _ = session, unmapped_id  # noqa — явное «ничего не делаем»


# ---------------------------------------------------------------------------
# Score подозрительности (этап 7.1, миграция 010)
# ---------------------------------------------------------------------------
#
# Цель — сократить ручной труд в /admin/mapping. Каждая активная запись
# получает число 0..100, показывающее, насколько вероятно, что это
# дубликат уже существующего компонента. Большие значения (>=50)
# требуют внимания админа; маленькие — массово переводятся в
# 'confirmed_new' одной кнопкой.
#
# Формула (см. TЗ этапа 7.1):
#   +30 — brand совпадает с manufacturer существующего компонента
#         той же категории (UPPER-сравнение);
#   +50 — общий токен модели (RTX 4060, i5-12400, Ryzen 5 7600);
#   +40 — Levenshtein distance < 15% длины имени;
#   max 100.


SCORE_SUSPICIOUS_THRESHOLD: int = 50  # >= 50 ⇒ «подозрительный», < 50 ⇒ «вероятно новый»


def _levenshtein(a: str, b: str) -> int:
    """Редакционное расстояние (insert/delete/replace = 1).

    Реализация «две строки» O(n*m) по времени, O(min(n,m)) по памяти.
    Без зависимостей — rapidfuzz и аналоги в requirements не добавляем.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if len(a) < len(b):
        a, b = b, a
    # a — длиннее. previous — текущая строка DP, current — следующая.
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            current[j] = min(
                current[j - 1] + 1,      # insert
                previous[j] + 1,         # delete
                previous[j - 1] + cost,  # replace/match
            )
        previous = current
    return previous[len(b)]


# Токен, похожий на «модельный номер» — 2-5 цифр + опциональный
# буквенный суффикс (F/K/KF/T/XT/Ti/SUPER/...). Он же — самый
# информативный общий токен для оценки «это дубликат».
_MODEL_NUMBER_TOKEN_RE = re.compile(
    r"\b([A-Za-z]{1,4}\d{2,5}[A-Za-z]{0,4}|\d{3,5}[A-Za-z]{0,4})\b"
)


def _model_tokens(text_value: str) -> set[str]:
    """Извлекает «значимые» токены из имени: модельные номера и короткие
    алфа-цифровые коды. Всё приводится к upper-case."""
    if not text_value:
        return set()
    up = text_value.upper()
    tokens = set(_MODEL_NUMBER_TOKEN_RE.findall(up))
    # Дополнительно: «RYZEN 5 7600», «CORE I5-12400» — разобьём на слова
    # и возьмём те, что содержат и буквы, и цифры (хорошие различители).
    for word in re.split(r"[^A-Z0-9]+", up):
        if not word:
            continue
        has_d = any(ch.isdigit() for ch in word)
        has_a = any(ch.isalpha() for ch in word)
        if has_d and has_a and 3 <= len(word) <= 12:
            tokens.add(word)
    return tokens


def _score_against_candidate(
    raw_name: str, brand: str | None, cand: dict,
) -> int:
    """Считает score конкретной пары (unmapped, candidate).
    candidate: dict с ключами model, manufacturer."""
    score = 0

    # 1) Совпадение бренда
    cb = (brand or "").strip().upper()
    cm = (cand.get("manufacturer") or "").strip().upper()
    if cb and cm:
        # Manufacturer часто длиннее ('Intel Corporation' vs 'Intel'),
        # поэтому считаем совпадением, если одно содержит другое.
        if cb == cm or cb in cm or cm in cb:
            score += 30

    # 2) Общий модельный токен
    tokens_a = _model_tokens(raw_name)
    tokens_b = _model_tokens(cand.get("model") or "")
    if tokens_a and tokens_b and (tokens_a & tokens_b):
        score += 50

    # 3) Levenshtein distance < 15% длины
    name_a = (raw_name or "").strip().upper()
    name_b = (cand.get("model") or "").strip().upper()
    if name_a and name_b:
        longer = max(len(name_a), len(name_b))
        if longer > 0:
            dist = _levenshtein(name_a, name_b)
            if dist * 100 < 15 * longer:
                score += 40

    return min(100, score)


def calculate_score(
    session: Session, unmapped: "UnmappedRow",
) -> tuple[int, int | None]:
    """Возвращает (score, best_candidate_component_id).

    Если guessed_category не задана или в БД нет кандидатов — (0, None).
    Выбираем до 50 «ближайших» компонентов по токенам (как делает
    find_candidates для UI), среди них берём максимум score.
    Скелет, созданный для этой же записи (resolved_component_id),
    исключается из сравнения: иначе любая запись получала бы 100 баллов
    сама с собой.
    """
    if not unmapped.guessed_category:
        return 0, None

    table = CATEGORY_TO_TABLE.get(unmapped.guessed_category)
    if table is None or table not in ALLOWED_TABLES:
        return 0, None

    from app.services.price_loaders.candidates import find_candidates

    # 1) Кандидаты по токенам (как в UI /admin/mapping).
    try:
        by_tokens = find_candidates(
            session,
            category=unmapped.guessed_category,
            raw_name=unmapped.raw_name,
            brand=unmapped.brand,
            exclude_id=unmapped.resolved_component_id,
            limit=50,
        )
    except Exception:
        by_tokens = []

    # 2) Кандидаты по бренду — добор, если совпадение по токенам слабое
    # или отсутствует. Без этого шага записи «бренд совпал, но модель
    # экзотическая» получают score=0 вместо 30.
    by_brand: list[dict] = []
    if unmapped.brand:
        try:
            rows = session.execute(
                text(
                    f"SELECT id, model, manufacturer "
                    f"FROM {table} "
                    f"WHERE UPPER(manufacturer) = UPPER(:b) "
                    f"  AND (:exc IS NULL OR id <> :exc) "
                    f"LIMIT 50"
                ),
                {"b": unmapped.brand, "exc": unmapped.resolved_component_id},
            ).mappings().all()
            by_brand = [dict(r) for r in rows]
        except Exception:
            by_brand = []

    # 3) Кандидаты по модельному токену. Нужно отдельно — find_candidates
    # требует AND по всем токенам; если в raw_name есть «шум», значимый
    # модельный номер (12400) теряется. Ищем по каждому такому токену
    # отдельно (OR-семантика).
    by_model: list[dict] = []
    model_tokens = _model_tokens(unmapped.raw_name or "")
    if model_tokens:
        like_parts: list[str] = []
        params: dict[str, object] = {"exc": unmapped.resolved_component_id}
        for i, tok in enumerate(sorted(model_tokens)):
            key = f"t{i}"
            like_parts.append(f"UPPER(model) LIKE :{key}")
            params[key] = f"%{tok}%"
        where_like = " OR ".join(like_parts)
        try:
            rows = session.execute(
                text(
                    f"SELECT id, model, manufacturer FROM {table} "
                    f"WHERE ({where_like}) "
                    f"  AND (:exc IS NULL OR id <> :exc) "
                    f"LIMIT 50"
                ),
                params,
            ).mappings().all()
            by_model = [dict(r) for r in rows]
        except Exception:
            by_model = []

    # Объединение с дедупом по id, токены приоритетнее.
    merged: dict[int, dict] = {}
    for c in by_tokens:
        merged[int(c["id"])] = c
    for c in by_brand:
        merged.setdefault(int(c["id"]), c)
    for c in by_model:
        merged.setdefault(int(c["id"]), c)

    if not merged:
        return 0, None

    best_score = 0
    best_id: int | None = None
    for cid, c in merged.items():
        s = _score_against_candidate(unmapped.raw_name, unmapped.brand, c)
        if s > best_score:
            best_score = s
            best_id = cid

    # Если ни один кандидат не набрал очков — пусть будет первый из
    # «по токенам» (чтобы в UI был живой указатель на «похоже на что-то»).
    if best_id is None and by_tokens:
        best_id = int(by_tokens[0]["id"])

    return best_score, best_id


def ensure_score(session: Session, unmapped_id: int) -> None:
    """Считает score, если ранее не считался, и записывает в БД.

    Идемпотентна: повторный вызов для уже просчитанной записи — noop.
    При пересчёте вручную (recalculate_unmapped_scores.py) используется
    более прямая логика — поля пересчитываются всегда.
    """
    row = get_by_id(session, unmapped_id)
    if row is None:
        return
    existing = session.execute(
        text(
            "SELECT best_candidate_calculated_at "
            "FROM unmapped_supplier_items WHERE id = :id"
        ),
        {"id": unmapped_id},
    ).first()
    if existing and existing.best_candidate_calculated_at is not None:
        return

    score, best_id = calculate_score(session, row)
    session.execute(
        text(
            "UPDATE unmapped_supplier_items SET "
            "    best_candidate_score         = :s, "
            "    best_candidate_component_id  = :cid, "
            "    best_candidate_calculated_at = NOW() "
            "WHERE id = :id"
        ),
        {"s": score, "cid": best_id, "id": unmapped_id},
    )
    session.commit()


def recalculate_score(session: Session, unmapped_id: int) -> tuple[int, int | None]:
    """Безусловный пересчёт score для одной записи. Возвращает (score, best_id).
    Используется в scripts/recalculate_unmapped_scores.py."""
    row = get_by_id(session, unmapped_id)
    if row is None:
        return 0, None
    score, best_id = calculate_score(session, row)
    session.execute(
        text(
            "UPDATE unmapped_supplier_items SET "
            "    best_candidate_score         = :s, "
            "    best_candidate_component_id  = :cid, "
            "    best_candidate_calculated_at = NOW() "
            "WHERE id = :id"
        ),
        {"s": score, "cid": best_id, "id": unmapped_id},
    )
    return score, best_id


# ---------------------------------------------------------------------------
# Чтение с учётом score (этап 7.1)
# ---------------------------------------------------------------------------

# Возможные значения для фильтра score в /admin/mapping.
SCORE_FILTER_SUSPICIOUS = "suspicious"   # score >= SCORE_SUSPICIOUS_THRESHOLD
SCORE_FILTER_NEW        = "new"          # score < SCORE_SUSPICIOUS_THRESHOLD (или NULL)
SCORE_FILTER_ALL        = "all"          # без фильтра по score


@dataclass
class UnmappedRowWithScore(UnmappedRow):
    best_candidate_score: int | None = None
    best_candidate_component_id: int | None = None
    best_candidate_model: str | None = None


def list_active_with_score(
    session: Session, *,
    supplier_id: int | None = None,
    category: str | None = None,
    score_filter: str = SCORE_FILTER_SUSPICIOUS,
    limit: int = 50,
    offset: int = 0,
) -> list[UnmappedRowWithScore]:
    """Активные записи + поля score + модель лучшего кандидата.

    Сортировка: score DESC NULLS LAST → сверху самые подозрительные.
    LEFT JOIN на все 8 таблиц компонентов для best_candidate_component_id
    не делаем — вместо этого отдельно подгружаем модели по id.
    """
    where = ["u.status IN ('pending', 'created_new')"]
    params: dict[str, object] = {}
    if supplier_id is not None:
        where.append("u.supplier_id = :sid")
        params["sid"] = supplier_id
    if category:
        where.append("u.guessed_category = :cat")
        params["cat"] = category

    if score_filter == SCORE_FILTER_SUSPICIOUS:
        where.append("u.best_candidate_score >= :thr")
        params["thr"] = SCORE_SUSPICIOUS_THRESHOLD
    elif score_filter == SCORE_FILTER_NEW:
        where.append(
            "(u.best_candidate_score IS NULL OR u.best_candidate_score < :thr)"
        )
        params["thr"] = SCORE_SUSPICIOUS_THRESHOLD
    # SCORE_FILTER_ALL — ничего не добавляем.

    where_sql = " AND ".join(where)
    params["lim"] = int(limit)
    params["off"] = int(offset)

    rows = session.execute(
        text(
            "SELECT u.id, u.supplier_id, s.name AS supplier_name, "
            "       u.supplier_sku, u.raw_category, u.guessed_category, "
            "       u.brand, u.mpn, u.gtin, u.raw_name, "
            "       u.price, u.currency, u.stock, u.transit, "
            "       u.status, u.notes, u.resolved_component_id, u.created_at, "
            "       u.best_candidate_score, u.best_candidate_component_id "
            "FROM unmapped_supplier_items u "
            "JOIN suppliers s ON s.id = u.supplier_id "
            f"WHERE {where_sql} "
            "ORDER BY u.best_candidate_score DESC NULLS LAST, "
            "         u.created_at DESC, u.id DESC "
            "LIMIT :lim OFFSET :off"
        ),
        params,
    ).mappings().all()

    # Модели кандидатов подгружаем одним SELECT'ом на категорию.
    by_cat: dict[str, set[int]] = {}
    for r in rows:
        cid = r["best_candidate_component_id"]
        cat = r["guessed_category"]
        if cid is not None and cat in CATEGORY_TO_TABLE:
            by_cat.setdefault(cat, set()).add(int(cid))

    models_by_cat: dict[str, dict[int, str]] = {}
    for cat, ids in by_cat.items():
        table = CATEGORY_TO_TABLE[cat]
        if table not in ALLOWED_TABLES:
            continue
        q = session.execute(
            text(f"SELECT id, model FROM {table} WHERE id = ANY(:ids)"),
            {"ids": list(ids)},
        ).all()
        models_by_cat[cat] = {int(x.id): x.model for x in q}

    result: list[UnmappedRowWithScore] = []
    for r in rows:
        d = dict(r)
        cat = d.pop("guessed_category", None)
        best_cid = d.pop("best_candidate_component_id", None)
        best_model = None
        if best_cid is not None and cat is not None:
            best_model = models_by_cat.get(cat, {}).get(int(best_cid))
        # Вернём guessed_category и best_candidate_component_id обратно.
        d["guessed_category"] = cat
        d["best_candidate_component_id"] = best_cid
        d["best_candidate_model"] = best_model
        result.append(UnmappedRowWithScore(**d))
    return result


def list_ids_missing_score(
    session: Session, *,
    supplier_id: int | None = None,
    category: str | None = None,
    limit: int = 50,
) -> list[int]:
    """id активных записей, у которых score ещё не посчитан.

    Нужно для «ленивого добора» в /admin/mapping: если админ открыл
    страницу до прогона recalculate_unmapped_scores.py, на лету
    досчитаем хотя бы видимый кусок.
    """
    where = [
        "status IN ('pending', 'created_new')",
        "best_candidate_calculated_at IS NULL",
    ]
    params: dict[str, object] = {"lim": int(limit)}
    if supplier_id is not None:
        where.append("supplier_id = :sid")
        params["sid"] = supplier_id
    if category:
        where.append("guessed_category = :cat")
        params["cat"] = category
    where_sql = " AND ".join(where)
    rows = session.execute(
        text(
            f"SELECT id FROM unmapped_supplier_items "
            f"WHERE {where_sql} "
            "ORDER BY id LIMIT :lim"
        ),
        params,
    ).all()
    return [int(r.id) for r in rows]


def count_by_score(session: Session) -> dict[str, int]:
    """Количество активных записей в разрезах score для шапки UI.

    Возвращает dict с ключами 'suspicious', 'new', 'total'.
    """
    row = session.execute(
        text(
            "SELECT "
            "  COUNT(*) FILTER (WHERE best_candidate_score >= :thr) AS suspicious, "
            "  COUNT(*) FILTER (WHERE best_candidate_score IS NULL "
            "                   OR best_candidate_score < :thr) AS new_cnt, "
            "  COUNT(*) AS total "
            "FROM unmapped_supplier_items "
            "WHERE status IN ('pending', 'created_new')"
        ),
        {"thr": SCORE_SUSPICIOUS_THRESHOLD},
    ).first()
    if row is None:
        return {"suspicious": 0, "new": 0, "total": 0}
    return {
        "suspicious": int(row.suspicious or 0),
        "new":        int(row.new_cnt or 0),
        "total":      int(row.total or 0),
    }


def bulk_confirm_new(
    session: Session, *,
    admin_user_id: int,
    max_score: int = SCORE_SUSPICIOUS_THRESHOLD - 1,
    supplier_id: int | None = None,
    category: str | None = None,
) -> int:
    """Массово переводит 'created_new' с низким score в 'confirmed_new'.

    max_score — включительно: 0..max_score. По умолчанию 49 (= всё,
    что НИЖЕ порога «подозрительных»).
    Возвращает число обновлённых записей.
    Фильтры supplier/category — на случай, если админ хочет провести
    массовое действие только в разрезе одного поставщика/категории.
    """
    where = [
        "status = 'created_new'",
        "(best_candidate_score IS NULL OR best_candidate_score <= :ms)",
    ]
    params: dict[str, object] = {
        "st":  STATUS_CONFIRMED_NEW,
        "uid": admin_user_id,
        "ms":  int(max_score),
    }
    if supplier_id is not None:
        where.append("supplier_id = :sid")
        params["sid"] = supplier_id
    if category is not None:
        where.append("guessed_category = :cat")
        params["cat"] = category

    where_sql = " AND ".join(where)
    result = session.execute(
        text(
            "UPDATE unmapped_supplier_items SET "
            "    status      = :st, "
            "    resolved_at = NOW(), "
            "    resolved_by = :uid "
            f"WHERE {where_sql}"
        ),
        params,
    )
    session.commit()
    return int(result.rowcount or 0)
