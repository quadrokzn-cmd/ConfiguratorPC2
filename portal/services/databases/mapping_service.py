# Сервис ручного сопоставления unmapped_supplier_items с компонентами
# БД для раздела «Базы данных» → «Очередь маппинга».
#
# Изначально создан на этапе 7 как app/services/mapping_service.py
# для /admin/mapping в конфигураторе. На этапе UI-2 Пути B (2026-05-11)
# переехал сюда вместе со страницами /databases/mapping.
#
# Работает только с новой таблицей (миграция 009). Все три действия
# админа — merge, confirm_as_new, defer — проходят здесь; веб-роут
# portal.routers.databases.mapping только декодирует формы и вызывает
# эти функции.

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from portal.services.configurator.enrichment.base import ALLOWED_TABLES, CATEGORY_TO_TABLE


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

    Edge-cases (этап 7.6):
      - idempotent: если запись уже merged — noop (не создаём дубликатов);
      - target — чужой скелет (привязан к другой созданной-новой unmapped)
        → ValueError: нельзя объединять позицию с «тенью» другой записи;
      - на target уже есть supplier_prices от того же поставщика+категории
        → удаляем конфликтующую строку перед переносом, иначе UNIQUE
        (supplier_id, category, component_id) падает в IntegrityError.
    """
    u = get_by_id(session, unmapped_id)
    if u is None:
        raise ValueError(f"unmapped_supplier_items id={unmapped_id} не найден")
    if u.guessed_category is None:
        raise ValueError(
            f"У записи id={unmapped_id} не задана guessed_category — "
            "нельзя определить таблицу компонента."
        )

    # Идемпотентность: повторный merge на уже merged-записи — noop.
    # Это защита от двойного нажатия админом и от повторных вызовов со
    # старой страницы.
    if u.status == STATUS_MERGED:
        return

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

    # Запрет объединения с «чужим» скелетом (тем, что привязан к другой
    # unmapped-записи со статусом created_new). UI их уже отфильтровывает
    # через _EXCLUDE_SKELETONS_SQL, но POST надо валидировать отдельно —
    # админ мог прислать произвольный target_component_id руками.
    foreign_skel = session.execute(
        text(
            "SELECT id FROM unmapped_supplier_items "
            "WHERE status = 'created_new' "
            "  AND resolved_component_id = :tid "
            "  AND id <> :uid"
        ),
        {"tid": target_component_id, "uid": unmapped_id},
    ).first()
    if foreign_skel is not None:
        raise ValueError(
            "Нельзя объединить со скелетом другой записи (id компонента "
            f"{target_component_id} закреплён за unmapped #{foreign_skel.id})."
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
        # На target уже может быть строка supplier_prices для того же
        # (supplier_id, category) — например, если тот же прайс грузили
        # дважды, или если админ уже переносил сюда другую unmapped-запись.
        # UNIQUE (supplier_id, category, component_id) сломает UPDATE.
        # Стратегия: удаляем конфликтующую строку (новая цена из переносимой
        # записи — актуальнее), и только потом делаем UPDATE.
        session.execute(
            text(
                "DELETE FROM supplier_prices "
                "WHERE supplier_id = :sid AND category = :cat "
                "  AND component_id = :new_cid"
            ),
            {
                "sid":     u.supplier_id,
                "cat":     category,
                "new_cid": target_component_id,
            },
        )
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


# Стоп-слова для токенизации: совпадение по таким словам не означает,
# что компоненты — дубликаты, это просто общая принадлежность к типу
# продукта/интерфейсу. Без стоп-списка любые два SSD AGi с одинаковым
# «SATA», «DDR4» или «1TB» получали +50 и ошибочно ранжировались как
# подозрительные дубликаты.
_MODEL_TOKEN_STOPWORDS: frozenset[str] = frozenset({
    # типы памяти и интерфейсы
    "DDR2", "DDR3", "DDR4", "DDR5",
    "SATA", "SAS", "NVME", "PCIE", "PCI", "USB", "HDMI", "DP",
    "M2", "RGB", "LED", "ATX", "ITX",
    # единицы измерения
    "GB", "TB", "MB", "KB", "GHZ", "MHZ", "KHZ",
    "W", "MM", "CM", "V",
    # общие типы компонентов
    "SSD", "HDD", "NVME", "RAM", "CPU", "GPU", "PSU",
})


# Цифровая часть значимого токена должна быть не менее 3 символов
# подряд — иначе любое «M2», «V2», «G4» проходит как «модель». Вместе
# со стоп-списком это отсекает шум и оставляет действительно
# различающие комбинации: B760, 7600X, MZ77E500B, CM8071512400F.
_MIN_DIGITS_IN_TOKEN: int = 3


_DIGIT_RUN_RE = re.compile(r"\d{%d,}" % _MIN_DIGITS_IN_TOKEN)


# Токены вида «512GB», «1TB», «3200MHZ» — это единицы измерения,
# а не модельные номера. Они часто общие между разными моделями
# (2 разных SSD по 1 TB) и давали ложные +50 очков.
_SIZE_SUFFIX_RE = re.compile(r"(GB|TB|MB|KB|GHZ|MHZ|KHZ|HZ|WT|MM)$")


def _model_tokens(text_value: str) -> set[str]:
    """Извлекает «значимые» токены из имени: модельные номера и короткие
    алфа-цифровые коды. Всё приводится к upper-case.

    Правила (ужесточены на этапе 7.2):
      - длина токена ≥ 4 символов;
      - содержит цифры (чисто буквенные «SATA» не различают модели);
      - цифровая часть подряд ≥ 3 символов (чтобы «B760», «12400»,
        «7600X» проходили, а «V2», «M2», «G4» — нет);
      - токен не оканчивается на единицу измерения (512GB, 3200MHZ);
      - токен не входит в стоп-список (DDR4, SATA, SSD, ATX, ...).
    """
    if not text_value:
        return set()
    up = text_value.upper()
    raw: set[str] = set()
    for word in re.split(r"[^A-Z0-9]+", up):
        if word:
            raw.add(word)

    result: set[str] = set()
    for tok in raw:
        if len(tok) < 4:
            continue
        if tok in _MODEL_TOKEN_STOPWORDS:
            continue
        if _SIZE_SUFFIX_RE.search(tok):
            continue
        has_d = any(ch.isdigit() for ch in tok)
        if not has_d:
            continue
        if not _DIGIT_RUN_RE.search(tok):
            continue
        result.add(tok)
    return result


# Ужесточённые пороги редакционного расстояния (этап 7.2):
# требуется одновременно и абсолютный лимит (≤ 5 правок), и
# относительный (< 20 % от короткой строки). Второе — ключевое:
# для 20-символьного названия 5 правок (25 %) раньше проходили,
# хотя уже означают «другой размер/суффикс», а не дубликат.
_LEVENSHTEIN_MAX_ABS: int = 5
_LEVENSHTEIN_MAX_RATIO_PCT: int = 20


# Если у записи не совпал ни один значимый модельный токен с кандидатом
# И Levenshtein больше _LEVENSHTEIN_MAX_ABS — итоговый score ограничен
# сверху этим значением. Это лечит самую частую ошибку live-проверки:
# «одинаковая категория + совпавший бренд, но разные модели»
# раньше получали +30 и оседали в подозрительных; теперь они остаются
# 30 и ниже, не попадая в фильтр suspicious.
_SCORE_CAP_NO_MATCH: int = 30


# ---------------------------------------------------------------------------
# Проверка несовпадения объёма/ёмкости (этап 7.3)
# ---------------------------------------------------------------------------
#
# На этапе 7.2 токены-размерности (1TB, 512GB, 3200MHZ, 450W) были
# исключены из «значимых» — они давали ложные +50 за совпадение общего
# объёма у разных моделей. Но та же калибровка отключила и обратную
# проверку: «в A и B разные объёмы — это разные модели».
#
# Эта функция извлекает из имени пары (число, единица) в трёх группах:
#   size   (KB/MB/GB/TB)  — нормализация к GB, десятичная (1TB = 1000GB);
#   freq   (HZ/KHZ/MHZ/GHZ) — нормализация к MHz (1GHz = 1000MHz);
#   power  (W/WT)          — без нормализации.
# Если в обоих именах нашлась одна и та же группа, но значения не
# пересекаются (даже с допуском 5 % — на случай 1TB ↔ 1024GB), то
# score ограничивается капом _SCORE_CAP_NO_MATCH (30).


# Negative lookahead'ы после unit отсекают «не-размерные» контексты:
#   - «540 MB/s», «6 GB/s»  — скорость канала, а не объём/мощность;
#   - «500 MB per second», «3200 MHz Read», «540 MB чтение» — то же самое
#     через пробел и ключевое слово.
# Текст перед matchem уже .upper(), поэтому русские «чтение/запись» после
# upper() превращаются в «ЧТЕНИЕ/ЗАПИСЬ» и тоже попадают под фильтр.
_CAPACITY_RE = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(TB|GB|MB|KB|WT|W|GHZ|MHZ|KHZ|HZ)\b"
    r"(?!\s*/)"
    r"(?!\s+(?:PER|READ|WRITE|ЧТЕНИЕ|ЗАПИСЬ)\b)"
)

# Группа единицы → «что именно сравниваем».
_CAPACITY_UNIT_GROUP: dict[str, str] = {
    "KB": "size", "MB": "size", "GB": "size", "TB": "size",
    "HZ": "freq", "KHZ": "freq", "MHZ": "freq", "GHZ": "freq",
    "W":  "power", "WT": "power",
}

# Коэффициент нормализации к «базовой» единице группы:
#   size  → GB   (десятично: 1TB = 1000GB)
#   freq  → MHz  (десятично: 1GHz = 1000MHz)
#   power → W    (WT трактуем как синоним W)
_CAPACITY_UNIT_FACTOR: dict[str, float] = {
    "KB": 1.0 / 1_000_000, "MB": 1.0 / 1000, "GB": 1.0, "TB": 1000.0,
    "HZ": 1.0 / 1_000_000, "KHZ": 1.0 / 1000, "MHZ": 1.0, "GHZ": 1000.0,
    "W":  1.0, "WT": 1.0,
}


# Категории, в которых объём/мощность/частота действительно различают
# модели: SSD 1TB ≠ SSD 512GB, RAM 16GB ≠ RAM 8GB, БП 450W ≠ БП 650W.
# Для cpu/motherboard/case упоминание «DDR5 6400MHz» или «MicroATX» в
# имени платы — это общие спецификации, а не различитель модели, поэтому
# проверку в этих категориях не включаем.
_CATEGORIES_WITH_CAPACITY: frozenset[str] = frozenset({
    "storage", "ram", "gpu", "psu", "cooler",
})


# Допуск при сравнении нормализованных значений — ~5 %. Нужен, чтобы
# «1TB» (=1000GB) и «1024GB» считались одинаковым объёмом: маркетологи
# пишут что одно, что другое.
_CAPACITY_TOLERANCE_PCT: float = 5.0


def _extract_capacities(text_value: str) -> dict[str, set[float]]:
    """Возвращает нормализованные значения по группам size/freq/power."""
    if not text_value:
        return {}
    result: dict[str, set[float]] = {}
    up = text_value.upper()
    for m in _CAPACITY_RE.finditer(up):
        num_str, unit = m.group(1), m.group(2)
        group = _CAPACITY_UNIT_GROUP.get(unit)
        if not group:
            continue
        try:
            num = float(num_str.replace(",", "."))
        except ValueError:
            continue
        value = num * _CAPACITY_UNIT_FACTOR[unit]
        result.setdefault(group, set()).add(value)
    return result


def _capacity_value_close(va: float, vb: float) -> bool:
    """True, если два значения совпадают с допуском ±5 %.
    Нужен, чтобы «1TB» (=1000 GB) и «1024 GB» считались одним объёмом."""
    if va == vb:
        return True
    avg = (abs(va) + abs(vb)) / 2
    if avg == 0:
        return False
    return abs(va - vb) * 100.0 <= _CAPACITY_TOLERANCE_PCT * avg


def _capacity_mismatch(name_a: str, name_b: str) -> bool:
    """True, если в обоих именах нашлась хотя бы одна общая группа
    (size/freq/power) и их МАКСИМАЛЬНЫЕ значения различаются больше
    чем на 5 %.

    Сравниваем именно максимумы, а не произвольные пересечения: в именах
    часто встречаются «шумовые» числа меньшего порядка (скорость чтения
    540 MB/s, воздушный поток 50 CFM). Реальный объём накопителя или
    мощность БП — это максимальное число среди меток группы: для SSD на
    SATA-III скорость ≤ 600 MB/s, а сам объём обычно от 128 GB = 128 000 MB,
    то есть на два порядка выше. Поэтому max(size) — это и есть объём.

    При этом контексты скорости («MB/s», «MB Read», «MB чтение», «MB per ...»)
    исключены ещё на уровне регулярки — это дополнительная защита.
    """
    caps_a = _extract_capacities(name_a)
    caps_b = _extract_capacities(name_b)
    for group, vals_a in caps_a.items():
        vals_b = caps_b.get(group)
        if not vals_b:
            # В одном есть метка, в другом нет — не штрафуем: возможно,
            # второе имя просто сокращённое.
            continue
        if not _capacity_value_close(max(vals_a), max(vals_b)):
            return True
    return False


# ---------------------------------------------------------------------------
# MPN как главный сигнал (этап 7.5)
# ---------------------------------------------------------------------------
#
# До 7.5 алгоритм скорил по brand + tokens + capacity + Levenshtein. На
# реальных данных это давало score=100 у 1274 пар с разными MPN, потому
# что «Crucial BX500 500GB» и «BX500 240GB» пишут одинаково с точностью
# до числа, а Levenshtein считал их «почти идентичными».
#
# С этапа 7.5: если у unmapped-записи есть MPN, а у кандидата — sku
# (наш MPN в таблицах компонентов), MPN решает всё:
#   идентичны                         → 100  «MPN идентичен»
#   совпадают после снятия упаковочного суффикса → 80  «MPN совпадает с точностью до суффикса»
#   разные                             → 20   «MPN различается»
# Только если MPN нет хотя бы у одной из сторон, работает прежний
# «fuzzy»-алгоритм — но с потолком 70, чтобы без MPN мы никогда не
# выставляли 100 (иначе такая пара в списке подозрительных окажется
# «точнее», чем реально совпавший MPN).


# «Упаковочные» суффиксы, не меняющие сам товар. Один и тот же SSD в
# разной фасовке (розница/OEM/tray) имеет MPN с разным «хвостом».
_MPN_SUFFIX_PARENS_RE = re.compile(r"\s*\([^)]*\)\s*$")
_MPN_SUFFIX_SLASH_DASH_RE = re.compile(
    r"[/\\\-](?:OEM|BOX|RTL|TRAY|BULK|CSM|RL|R|[1-9])$"
)
_MPN_SUFFIX_SPACE_RE = re.compile(
    r"\s+(?:OEM|BOX|RTL|TRAY|BULK|CSM)$"
)


def _strip_mpn_suffix(mpn: str) -> str:
    """Убирает «упаковочные» суффиксы с конца MPN, приводит к UPPER.

    Примеры:
      CT500BX500SSD1-RTL       → CT500BX500SSD1
      CT500BX500SSD1 (OEM)     → CT500BX500SSD1
      CM8071512400F/R          → CM8071512400F
      CM8071512400F BOX        → CM8071512400F
      PM-500ATX-1              → PM-500ATX

    Цикл нужен, чтобы комбинации вида «(RTL) OEM» снимались за два
    прохода: сначала хвостовой «OEM», потом скобки.
    """
    s = (mpn or "").strip().upper()
    if not s:
        return ""
    prev: str | None = None
    while s != prev:
        prev = s
        s = _MPN_SUFFIX_PARENS_RE.sub("", s)
        s = _MPN_SUFFIX_SLASH_DASH_RE.sub("", s)
        s = _MPN_SUFFIX_SPACE_RE.sub("", s)
        s = s.rstrip()
    return s


_SCORE_MPN_EXACT:     int = 100
_SCORE_MPN_SUFFIX:    int = 80
_SCORE_MPN_DIFFERENT: int = 20
# Потолок fallback'а (когда MPN нет хотя бы у одной стороны): без MPN
# 100 %-й уверенности в дубликате нет. Оставляем запас 30 очков под
# случаи, где MPN реально совпадёт.
_SCORE_FALLBACK_CAP:  int = 70


def _score_breakdown_fallback(
    raw_name: str, brand: str | None, cand: dict,
    *, category: str | None = None,
) -> tuple[int, str]:
    """«Fuzzy»-алгоритм: brand + tokens + capacity + Levenshtein.
    Используется, когда MPN не доступен у одной из сторон.
    """
    score = 0
    parts: list[str] = []

    # 1) Совпадение бренда (+30). Manufacturer часто длиннее
    # ('Intel Corporation' vs 'Intel') — считаем совпадением, если
    # одно содержит другое.
    cb = (brand or "").strip().upper()
    cm = (cand.get("manufacturer") or "").strip().upper()
    brand_match = False
    if cb and cm and (cb == cm or cb in cm or cm in cb):
        score += 30
        brand_match = True
        parts.append("бренд")

    # 2) Общий модельный токен (+50) — с ужесточённой токенизацией.
    tokens_a = _model_tokens(raw_name)
    tokens_b = _model_tokens(cand.get("model") or "")
    token_match = bool(tokens_a and tokens_b and (tokens_a & tokens_b))
    if token_match:
        score += 50
        parts.append("модельный токен")

    # 3) Похожесть имён по Levenshtein (+40): абсолютный лимит и
    # относительный одновременно.
    name_a = (raw_name or "").strip().upper()
    name_b = (cand.get("model") or "").strip().upper()
    lev_match = False
    if name_a and name_b:
        lev_dist = _levenshtein(name_a, name_b)
        shorter = min(len(name_a), len(name_b))
        if (
            lev_dist <= _LEVENSHTEIN_MAX_ABS
            and shorter > 0
            and lev_dist * 100 < _LEVENSHTEIN_MAX_RATIO_PCT * shorter
        ):
            score += 40
            lev_match = True
            parts.append("похожесть имён")

    # 4) Кап для «пустых» совпадений: ни общего токена, ни близости имён.
    # Такие пары не должны перешагивать порог подозрительных: они почти
    # всегда «тот же бренд, но другая модель».
    if not token_match and not lev_match:
        score = min(score, _SCORE_CAP_NO_MATCH)

    # 5) Несовпадение объёма/характеристик — только для категорий, где
    # размерность действительно различает модели.
    capacity_mismatched = False
    if category in _CATEGORIES_WITH_CAPACITY:
        if _capacity_mismatch(raw_name or "", cand.get("model") or ""):
            capacity_mismatched = True
            score = min(score, _SCORE_CAP_NO_MATCH)
            parts = [p for p in parts if p == "бренд"]
            parts.append("несовпадение объёма/характеристик")

    score = min(100, score)

    if not parts:
        reason = "нет совпадений"
    elif brand_match and len(parts) == 1 and not capacity_mismatched:
        reason = "только бренд"
    else:
        reason = " + ".join(parts)

    return score, reason


def _score_breakdown(
    raw_name: str, brand: str | None, cand: dict,
    *, category: str | None = None, mpn: str | None = None,
) -> tuple[int, str]:
    """Считает (score, reason) для пары (unmapped, candidate).

    Если у unmapped есть `mpn`, а у кандидата — `sku` (наш MPN), MPN
    решает всё: идентичные → 100, с точностью до упаковочного суффикса
    → 80, разные → 20.

    Если MPN нет у одной из сторон — fallback на brand+tokens+capacity+
    Levenshtein с потолком 70.
    """
    # Этап 1: MPN — если он есть у обоих, он решает.
    row_mpn  = (mpn or "").strip().upper()
    cand_mpn = (cand.get("sku") or "").strip().upper()
    if row_mpn and cand_mpn:
        if row_mpn == cand_mpn:
            return _SCORE_MPN_EXACT, "MPN идентичен"
        r_norm = _strip_mpn_suffix(row_mpn)
        c_norm = _strip_mpn_suffix(cand_mpn)
        if r_norm and r_norm == c_norm:
            return _SCORE_MPN_SUFFIX, "MPN совпадает с точностью до суффикса"
        return _SCORE_MPN_DIFFERENT, "MPN различается"

    # Этап 2: fallback — МRN нет, работаем по старой логике, но с капом 70.
    fb_score, fb_reason = _score_breakdown_fallback(
        raw_name, brand, cand, category=category,
    )
    return min(fb_score, _SCORE_FALLBACK_CAP), f"без MPN: {fb_reason}"


def _score_against_candidate(
    raw_name: str, brand: str | None, cand: dict,
    *, category: str | None = None, mpn: str | None = None,
) -> int:
    """Считает score конкретной пары (unmapped, candidate).
    candidate: dict с ключами model, manufacturer, sku.

    Тонкая обёртка над _score_breakdown — возвращает только число.
    Оставлена для обратной совместимости и простых проверок.
    """
    score, _ = _score_breakdown(raw_name, brand, cand, category=category, mpn=mpn)
    return score


# Сколько кандидатов «собираем» перед ранжированием по score. Нужно
# побольше, чем отдаём наружу — чтобы в top-10 попали лучшие, а не
# случайные. 50 исторически достаточно и не создаёт нагрузки (LIMIT
# в каждом из трёх запросов + dedup).
_RANKED_GATHER_LIMIT: int = 50


# Фильтр-скелет — такой же, как в candidates.py. Дублируем константу
# здесь, чтобы mapping_service не тянул импорт через модуль price_loaders
# и его поведение оставалось самодостаточным.
_EXCLUDE_SKELETONS_SQL = (
    "id NOT IN ("
    "SELECT resolved_component_id FROM unmapped_supplier_items "
    "WHERE status = 'created_new' AND resolved_component_id IS NOT NULL"
    ")"
)


def calculate_candidates_ranked(
    session: Session, unmapped: "UnmappedRow", limit: int = 10,
) -> list[dict]:
    """Топ-N кандидатов для записи unmapped_supplier_items с их score.

    Единый источник правды для админского UI: и список /admin/mapping,
    и детальная /admin/mapping/{id} работают через эту функцию —
    поэтому в них не может быть рассинхрона.

    Возвращает список dict'ов, отсортированный по score DESC, min_price ASC.
    Каждый dict содержит ключи: id, model, sku, manufacturer, gtin,
    min_price, score, reason.

    Внутри — три параллельных пути поиска (токены, бренд, модельный
    токен) с дедупом по id и общим ранжированием через _score_breakdown.
    Скелеты (компоненты, созданные при загрузке Merlion/Treolan и
    привязанные к status='created_new') полностью исключены из
    кандидатов: иначе unmapped-записи ссылались бы друг на друга.
    """
    if not unmapped.guessed_category:
        return []

    table = CATEGORY_TO_TABLE.get(unmapped.guessed_category)
    if table is None or table not in ALLOWED_TABLES:
        return []

    from portal.services.configurator.price_loaders.candidates import find_candidates

    # 1) Кандидаты по токенам (та же функция, что на странице детали
    # и при загрузке прайсов). Уже содержит фильтр скелетов и рерканкинг.
    try:
        by_tokens = find_candidates(
            session,
            category=unmapped.guessed_category,
            raw_name=unmapped.raw_name,
            brand=unmapped.brand,
            exclude_id=unmapped.resolved_component_id,
            limit=_RANKED_GATHER_LIMIT,
        )
    except Exception:
        by_tokens = []

    # 2) Добор по бренду: закрывает кейс «совпадение по токенам пустое,
    # но бренд известен» — иначе компонент одного бренда вообще не
    # попадёт в кандидаты.
    by_brand: list[dict] = []
    if unmapped.brand:
        try:
            rows = session.execute(
                text(
                    f"SELECT id, model, sku, manufacturer, gtin "
                    f"FROM {table} "
                    f"WHERE UPPER(manufacturer) = UPPER(:b) "
                    f"  AND (:exc IS NULL OR id <> :exc) "
                    f"  AND {_EXCLUDE_SKELETONS_SQL} "
                    f"LIMIT {_RANKED_GATHER_LIMIT}"
                ),
                {"b": unmapped.brand, "exc": unmapped.resolved_component_id},
            ).mappings().all()
            by_brand = [dict(r) for r in rows]
        except Exception:
            by_brand = []

    # 3) Добор по модельному токену (OR-семантика): находит компоненты
    # с совпавшим «12400» даже если нормализация токенов в find_candidates
    # потеряла этот токен среди «шума».
    by_model: list[dict] = []
    tokens = _model_tokens(unmapped.raw_name or "")
    if tokens:
        like_parts: list[str] = []
        params: dict[str, object] = {"exc": unmapped.resolved_component_id}
        for i, tok in enumerate(sorted(tokens)):
            key = f"t{i}"
            like_parts.append(f"UPPER(model) LIKE :{key}")
            params[key] = f"%{tok}%"
        where_like = " OR ".join(like_parts)
        try:
            rows = session.execute(
                text(
                    f"SELECT id, model, sku, manufacturer, gtin "
                    f"FROM {table} "
                    f"WHERE ({where_like}) "
                    f"  AND (:exc IS NULL OR id <> :exc) "
                    f"  AND {_EXCLUDE_SKELETONS_SQL} "
                    f"LIMIT {_RANKED_GATHER_LIMIT}"
                ),
                params,
            ).mappings().all()
            by_model = [dict(r) for r in rows]
        except Exception:
            by_model = []

    # Дедуп: by_tokens уже содержит min_price, остальные — нет. Допишем
    # min_price одним запросом по собранным id. Для by_tokens значение
    # сохраняем как есть.
    merged: dict[int, dict] = {}
    for c in by_tokens:
        merged[int(c["id"])] = dict(c)
    for c in by_brand:
        merged.setdefault(int(c["id"]), dict(c))
    for c in by_model:
        merged.setdefault(int(c["id"]), dict(c))

    if not merged:
        return []

    need_price_ids = [
        cid for cid, c in merged.items() if "min_price" not in c
    ]
    if need_price_ids:
        try:
            rows = session.execute(
                text(
                    "SELECT component_id, "
                    "       MIN(price) FILTER (WHERE stock_qty > 0) AS min_price "
                    "FROM supplier_prices "
                    "WHERE category = :cat AND component_id = ANY(:ids) "
                    "GROUP BY component_id"
                ),
                {"cat": unmapped.guessed_category, "ids": list(need_price_ids)},
            ).all()
            price_map = {int(r.component_id): r.min_price for r in rows}
        except Exception:
            price_map = {}
        for cid in need_price_ids:
            merged[cid].setdefault("min_price", price_map.get(cid))

    # Скоринг + reason для каждого кандидата.
    scored: list[dict] = []
    for cid, c in merged.items():
        score, reason = _score_breakdown(
            unmapped.raw_name, unmapped.brand, c,
            category=unmapped.guessed_category,
            mpn=unmapped.mpn,
        )
        scored.append({
            "id":           cid,
            "model":        c.get("model") or "",
            "sku":          c.get("sku"),
            "manufacturer": c.get("manufacturer") or "",
            "gtin":         c.get("gtin"),
            "min_price":    c.get("min_price"),
            "score":        score,
            "reason":       reason,
        })

    # Сортировка: сначала по score DESC, потом по min_price ASC
    # (NULL в конец), потом по id для детерминизма.
    def _sort_key(item: dict) -> tuple:
        mp = item.get("min_price")
        return (
            -int(item["score"]),
            0 if mp is not None else 1,
            float(mp) if mp is not None else 0.0,
            int(item["id"]),
        )

    scored.sort(key=_sort_key)
    return scored[: int(limit)]


def calculate_score(
    session: Session, unmapped: "UnmappedRow",
) -> tuple[int, int | None]:
    """Возвращает (score, best_candidate_component_id).

    Тонкая обёртка над calculate_candidates_ranked — берёт лучшего.
    Сохранена как отдельная функция, т. к. точки вызова
    (recalculate_unmapped_scores.py, ensure_score) хранят в БД только
    id и score, без остальных полей кандидата.
    """
    ranked = calculate_candidates_ranked(session, unmapped, limit=1)
    if not ranked:
        return 0, None
    top = ranked[0]
    return int(top["score"]), int(top["id"])


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
