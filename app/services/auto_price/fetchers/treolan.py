# Авто-загрузка прайса Treolan через REST API + JWT (этап 12.3 / 12.3-fix).
#
# Поток:
#   _get_token()     — POST /v1/auth/token (или fallback /v1/auth/login),
#                       JWT в теле ответа, кеш 1ч до exp.
#   _fetch_catalog() — POST /v1/Catalog/Get с пустыми фильтрами (весь
#                       склад), Bearer token, retry 5/15/45 на сетевых
#                       ошибках и 5xx, на 401 — сброс токена и 1 повтор.
#   _save()          — рекурсивно обходит дерево categories[].children/products,
#                       преобразует товары в PriceRow и зовёт общий
#                       orchestrator.save_price_rows() — тот же pipeline что и
#                       /admin/price-uploads (upsert supplier_prices, mapping,
#                       disappeared, etc.).
#
# 12.3-fix: production-API возвращает иерархию categories→children/products
# (а не плоский positions[]). Старая версия адаптера ожидала positions[]
# на верхнем уровне и тихо отдавала пустой список → run #17 пометил 1391 SKU
# disappeared. Теперь обход дерева через _walk_products(); если categories=[]
# или после walk собрано 0 товаров — RuntimeError, чтобы pipeline закрылся
# failed и НЕ запустил disappeared.
#
# Конвертация валют:
#   currency='USD' → price * cb_rate_usd_rub из exchange_rates на
#                    последний день; результат записывается в RUB.
#   currency='RUB' → как есть.
#   иначе          → позиция пропускается, в лог warning «unmapped currency».
#
# atStock/atTransit в production приходят строками («<10», «10», «нет»…),
# не int — см. _parse_stock_str().

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Iterator

import httpx
from sqlalchemy import text

from app.services.auto_price.base import BaseAutoFetcher, register_fetcher
from app.services.price_loaders._qual_stock import TREOLAN_QUAL_STOCK
from app.services.price_loaders.models import PriceRow
from shared.db import SessionLocal


logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "https://api.treolan.ru/api"


# Категории Treolan (substring-match по category.name) → наша категория.
#
# 12.3-fix: имя category берётся напрямую из дерева (categories[].name и
# вложенные children[].name), а не из rusName. Substring проверяется по
# каждому имени в path от корня к листу — попадание на любом уровне даёт
# our_category. Это покрывает случаи вроде:
#     Комплектующие -> Процессоры -> Intel Core i5
# где конкретная подкатегория ('Intel Core i5') слишком узка, а корневая
# 'Комплектующие' слишком широка — но средний уровень 'Процессоры'
# матчится на 'процессор'.
#
# Полное category_id-mapping остаётся в техдолге — substring достаточен
# для реальных корневых ветвей в проде.
# ВНИМАНИЕ: порядок ключей значим. _detect_our_category() итерируется
# в порядке вставки и возвращает ПЕРВЫЙ match. Категории, чьё имя
# содержит подстроку другой категории («БП для корпусов» ⊃ «корпус»),
# должны идти РАНЬШЕ — иначе path «Комплектующие → БП для корпусов»
# попадёт в 'case', а не в 'psu' (это и был баг 12.3-fix-2: ~210 PSU
# терялись на верификации).
_CATEGORY_NAME_MAP: dict[str, str] = {
    "процессор":             "cpu",
    "материнск":             "motherboard",
    "оперативн":             "ram",
    "видеокарт":             "gpu",
    "ssd":                   "storage",
    "жестк":                 "storage",
    "блок питания":          "psu",
    "бп для":                "psu",
    "корпус":                "case",
    "охлажд":                "cooler",
}


# Blocklist: если в любом из имён path встречается одно из этих
# слов — путь точно НЕ про ПК-комплектующее. Нужен потому что
# substring-match сам по себе наивный: «1-процессорные серверы»
# содержит «процессор» и иначе попал бы в cpu, обрушив подбор.
# Корневые ветви Treolan (сервер/ноутбук/монитор/принтер/ИБП/…)
# заносим сюда.
_CATEGORY_BLOCKLIST: tuple[str, ...] = (
    "сервер", "ноутбук", "планшет", "монитор", "телевизор",
    "принтер", "сканер", "мфу", "источник",  # «Источники бесперебойного питания»
    "сетев", "телефон", "коммутац",
    "автоматическая идентификация", "промышленн", "электрика",
    "запчасти", "кресл", "расходн", "professional", "pro av",
)


def _detect_our_category(category_path: list[str] | str | None) -> str | None:
    """Маппит путь категорий Treolan в нашу. Принимает либо list имён
    (от корня к листу), либо одно имя — для обратной совместимости.
    None — категория не относится к ПК-комплектующим (периферия,
    серверы, ИБП и т.п.); orchestrator такие позиции пропустит.

    Blocklist отрабатывает первым: если в любом узле path встречается
    «сервер»/«ноутбук»/«монитор»/«ИБП»/… — return None, даже если ниже
    по дереву есть имя с substring «процессор» (case: «Серверы → 1-
    процессорные → DELL PowerEdge»)."""
    if not category_path:
        return None
    names = [category_path] if isinstance(category_path, str) else category_path
    full_lower = " ".join(str(n) for n in names if n).lower()
    for stopword in _CATEGORY_BLOCKLIST:
        if stopword in full_lower:
            return None
    for name in names:
        if not name:
            continue
        s = str(name).lower()
        for kw, cat in _CATEGORY_NAME_MAP.items():
            if kw in s:
                return cat
    return None


def _walk_products(
    categories: list[Any] | None,
    _path: tuple[str, ...] = (),
) -> Iterator[tuple[list[str], int | None, dict[str, Any]]]:
    """Рекурсивный DFS по дереву Treolan. Yield-ит (path, leaf_cat_id,
    product) для каждого товара в каждой непустой category.products[] на
    любой глубине; path — список имён категорий от корня к текущей,
    leaf_cat_id — id той категории, в чьих products[] лежит позиция (для
    lookup в category_map; см. этап 12.5c). None — если у узла нет int id.

    Treolan API возвращает {"categories": [{"id":..., "name":..., "products":[...],
    "children":[{...тот же формат...}]}]} — товары лежат в листьях, плюс
    могут лежать на промежуточных уровнях. Подкатегории — в children[]."""
    for node in categories or []:
        if not isinstance(node, dict):
            continue
        name = node.get("name") or node.get("rusName") or ""
        cur_path = (*_path, str(name)) if name else _path
        cat_id_raw = node.get("id")
        cat_id = cat_id_raw if isinstance(cat_id_raw, int) else None
        prods = node.get("products") or []
        if isinstance(prods, list):
            for p in prods:
                if isinstance(p, dict):
                    yield list(cur_path), cat_id, p
        kids = node.get("children") or []
        if isinstance(kids, list) and kids:
            yield from _walk_products(kids, cur_path)


# ---- Кеш JWT-токена (process-level) -------------------------------------
#
# Кеш живёт в module-globals (а не в class-attr), чтобы все экземпляры
# в одном процессе делили один токен. На pytest-xdist каждый воркер —
# отдельный процесс, кеши не пересекаются (а для тестов креды
# monkeypatch'атся отдельно).

_TOKEN_CACHE: dict[str, Any] = {
    "token":  None,    # str | None
    "exp_ts": 0,       # int (unix ts of exp claim)
}

# Сколько секунд до exp считаем «токен ещё живой и можно отдавать из кеша».
_TOKEN_REFRESH_BUFFER_SECONDS = 60 * 60  # 1 час


def _decode_jwt_exp(token: str) -> int | None:
    """Распарсить exp из payload JWT. Не валидирует подпись — только парсит.

    Возвращает unix-timestamp либо None, если структура нестандартная.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        # base64url + padding
        padding = "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        payload = json.loads(payload_bytes.decode("utf-8"))
        exp = payload.get("exp")
        if isinstance(exp, (int, float)):
            return int(exp)
    except Exception:
        return None
    return None


# ---- httpx settings -----------------------------------------------------

# Каталог большой, ответ ~10-30МБ JSON. Длинный таймаут на read.
_CATALOG_TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=60.0, pool=30.0)
_AUTH_TIMEOUT = httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=15.0)

# Backoff между попытками _fetch_catalog (5xx и сетевые ошибки).
_RETRY_BACKOFFS = (5, 15, 45)


# ---- Вспомогательные парсеры значений из ответа API --------------------

def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    s = str(value).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d if d > 0 else None


def _to_int(value: Any) -> int:
    if value is None:
        return 0
    s = str(value).strip().replace(",", ".")
    if not s:
        return 0
    try:
        return int(Decimal(s))
    except (InvalidOperation, ValueError):
        return 0


# 12.3-fix: production-API возвращает atStock/atTransit СТРОКАМИ:
#   "<10" — есть в наличии, но мало; "10" — точное число; "" / "нет" —
#   нет. Старая _to_int возвращала 0 для "<10", из-за чего товар был бы
#   stock=0 в supplier_prices (хотя в реальности он есть). Возвращаем 1,
#   чтобы запись попадала в active SKUs и в подбор кандидатов.
#
# 12.3-fix-2: помимо «<N»/«>N» Treolan присылает ещё и «много» — старый
# _to_int возвращал на нём 0 (Decimal не парсит), и ~700 позиций на
# каждом запуске оказывались stock=0. Теперь используем shared таблицу
# TREOLAN_QUAL_STOCK (общую с XLSX-парсером): «<10»→5, «много»→50,
# «>10»→20, «>100»→100. Lookup идёт первым; если ключа нет — fallback
# на старую логику с «<»/«>»/числом.
def _parse_stock_str(value: Any) -> int:
    if value is None:
        return 0
    s = str(value).strip()
    if not s:
        return 0
    qual = TREOLAN_QUAL_STOCK.get(s.lower().replace(" ", ""))
    if qual is not None:
        return qual
    s_low = s.lower()
    if s_low in {"нет", "no", "0", "-"}:
        return 0
    if s.startswith("<"):
        return 1  # "<5" и др. варианты, не покрытые таблицей
    if s.startswith(">"):
        rest = s[1:].strip()
        try:
            return int(Decimal(rest)) + 1
        except (InvalidOperation, ValueError):
            return 1
    return _to_int(s)


def _normalize_gtin(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    if "e" in s.lower():
        try:
            s = str(int(Decimal(s)))
        except InvalidOperation:
            return None
    digits = re.sub(r"\D", "", s)
    return digits or None


# ---- Курс ЦБ из exchange_rates -----------------------------------------

def _latest_usd_rub_rate(session) -> Decimal | None:
    """Самый свежий курс USD/RUB из exchange_rates. None — таблица пуста.
    Здесь не тянем ЦБ синхронно: scheduler конфигуратора (5 раз в день)
    обновляет его, при первом старте app/scheduler.ensure_initial_rate()
    тоже подтягивает. Если на момент автозагрузки exchange_rates пуст —
    USD-позиции пропустим (warning), RUB-позиции загрузятся как обычно."""
    row = session.execute(
        text(
            "SELECT rate_usd_rub FROM exchange_rates "
            "ORDER BY rate_date DESC, fetched_at DESC LIMIT 1"
        )
    ).first()
    if row is None:
        return None
    return Decimal(str(row.rate_usd_rub))


# ===================================================================
# TreolanFetcher
# ===================================================================

@register_fetcher
class TreolanFetcher(BaseAutoFetcher):
    """REST-API канал для Treolan. См. модульный docstring."""

    supplier_slug = "treolan"

    def __init__(self) -> None:
        # Креды читаем при создании. Если не заданы — даём админу
        # понятную ошибку со списком переменных, которые искали.
        # Оба варианта env-имён ('LOGIN' и 'USERNAME') бывают в брифе
        # от Treolan; здесь канон — TREOLAN_API_LOGIN.
        self.base_url = (os.environ.get("TREOLAN_API_BASE_URL") or "").strip() or _DEFAULT_BASE_URL
        # На всякий случай trim trailing slash, чтобы /v1/... не давало //
        self.base_url = self.base_url.rstrip("/")
        self.login = (os.environ.get("TREOLAN_API_LOGIN") or "").strip()
        self.password = (os.environ.get("TREOLAN_API_PASSWORD") or "").strip()
        if not self.login or not self.password:
            raise RuntimeError(
                "Treolan API: не заданы креды. Ожидаются переменные окружения: "
                "TREOLAN_API_LOGIN, TREOLAN_API_PASSWORD "
                "(опционально TREOLAN_API_BASE_URL, по умолчанию "
                f"{_DEFAULT_BASE_URL})."
            )
        # 12.5c: один fetch — один map; пересоздаётся в _save() на каждом
        # вызове fetch_and_save(). Хранится на инстансе fetcher'а.
        self._category_map: dict[int, str | None] = {}

    # ---- Основная точка входа -----------------------------------------

    def fetch_and_save(self) -> int:
        token = self._get_token()
        data = self._fetch_catalog(token)
        return self._save(data)

    # ---- Auth ----------------------------------------------------------

    def _get_token(self) -> str:
        """JWT с кешем. Если до exp осталось > 1 часа — отдаём из кеша."""
        cached = _TOKEN_CACHE.get("token")
        cached_exp = int(_TOKEN_CACHE.get("exp_ts") or 0)
        now = int(time.time())
        if cached and cached_exp - now > _TOKEN_REFRESH_BUFFER_SECONDS:
            return cached

        token = self._auth_request()
        exp = _decode_jwt_exp(token) or (now + 12 * 3600)  # дефолт 12ч если без exp
        _TOKEN_CACHE["token"] = token
        _TOKEN_CACHE["exp_ts"] = exp
        return token

    def _auth_request(self) -> str:
        """Сначала пробуем POST /v1/auth/token с JSON-телом; если
        401/404/405/400 — fallback на /v1/auth/login с creds в query.
        Тело ответа — сама JWT-строка (без обёртки {"token":...})."""
        primary_url = f"{self.base_url}/v1/auth/token"
        primary_body = {"login": self.login, "password": self.password}
        primary_headers = {"Accept": "application/json", "Content-Type": "application/json"}

        last_status = None
        last_text = ""
        try:
            with httpx.Client(timeout=_AUTH_TIMEOUT) as client:
                r = client.post(primary_url, json=primary_body, headers=primary_headers)
                if r.status_code == 200:
                    return self._extract_token_from_response(r)
                last_status = r.status_code
                last_text = (r.text or "")[:300]
        except httpx.RequestError as exc:
            last_text = f"network: {exc}"

        # Fallback: /v1/auth/login с creds в query
        fallback_url = f"{self.base_url}/v1/auth/login"
        try:
            with httpx.Client(timeout=_AUTH_TIMEOUT) as client:
                r = client.post(
                    fallback_url,
                    params={"login": self.login, "password": self.password},
                    headers={"Accept": "application/json"},
                )
                if r.status_code == 200:
                    return self._extract_token_from_response(r)
                raise RuntimeError(
                    "Treolan auth: оба endpoint'а не вернули 200. "
                    f"primary {primary_url} → {last_status} {last_text!r}; "
                    f"fallback {fallback_url} → {r.status_code} {(r.text or '')[:300]!r}."
                )
        except httpx.RequestError as exc:
            raise RuntimeError(
                "Treolan auth: оба endpoint'а недоступны. "
                f"primary {primary_url} → {last_status} {last_text!r}; "
                f"fallback {fallback_url} → network: {exc}."
            )

    @staticmethod
    def _extract_token_from_response(r: httpx.Response) -> str:
        """API возвращает JWT прямой строкой в теле, иногда обёрнутой в
        кавычки JSON («"eyJ..."»), иногда как plain text. Поддерживаем
        оба формата."""
        body = (r.text or "").strip()
        if body.startswith('"') and body.endswith('"'):
            try:
                body = json.loads(body)
            except Exception:
                body = body.strip('"')
        if not body or not isinstance(body, str) or "." not in body:
            raise RuntimeError(
                "Treolan auth: ответ не похож на JWT. "
                f"Получено: {(r.text or '')[:200]!r}"
            )
        return body

    # ---- Catalog -------------------------------------------------------

    def _fetch_catalog(self, token: str) -> dict[str, Any]:
        """POST /v1/Catalog/Get с пустыми фильтрами. Retry 3 раза с
        backoff'ом, на 401 — один сброс кеша и повтор."""
        url = f"{self.base_url}/v1/Catalog/Get"
        body = {
            "category":            "",
            "vendorid":            0,
            "keywords":            "",
            "criterion":           "Contains",
            "inArticul":           True,
            "inName":              False,
            "inMark":              False,
            "showNc":              0,
            "freeNom":             True,
            "withoutLocalization": False,
        }
        headers_base = {
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
            "Accept":        "application/json",
        }

        last_exc: Exception | None = None
        retried_after_401 = False
        attempt = 0
        while attempt <= len(_RETRY_BACKOFFS):
            try:
                with httpx.Client(timeout=_CATALOG_TIMEOUT) as client:
                    r = client.post(url, json=body, headers=headers_base)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 401 and not retried_after_401:
                    # Токен мог истечь раньше exp — один раз пробуем
                    # обновить и повторить с тем же набором попыток.
                    logger.warning(
                        "Treolan catalog: 401, обновляем токен и повторяем."
                    )
                    _TOKEN_CACHE["token"] = None
                    _TOKEN_CACHE["exp_ts"] = 0
                    token = self._get_token()
                    headers_base["Authorization"] = f"Bearer {token}"
                    retried_after_401 = True
                    continue
                if 500 <= r.status_code < 600:
                    last_exc = RuntimeError(
                        f"Treolan catalog: HTTP {r.status_code} — "
                        f"{(r.text or '')[:300]!r}"
                    )
                else:
                    raise RuntimeError(
                        f"Treolan catalog: HTTP {r.status_code} — "
                        f"{(r.text or '')[:300]!r}"
                    )
            except httpx.RequestError as exc:
                last_exc = exc

            if attempt >= len(_RETRY_BACKOFFS):
                break
            backoff = _RETRY_BACKOFFS[attempt]
            logger.warning(
                "Treolan catalog: попытка %d не удалась (%s), повтор через %dс.",
                attempt + 1, last_exc, backoff,
            )
            time.sleep(backoff)
            attempt += 1

        raise RuntimeError(
            f"Treolan catalog: все попытки исчерпаны. Последняя ошибка: {last_exc}"
        )

    # ---- Category map (этап 12.5c) ------------------------------------

    def _build_category_map(
        self, categories: list[Any] | None,
    ) -> dict[int, str | None]:
        """Один проход по дереву категорий → mapping {category_id: our_category}.

        Заменяет per-position substring-классификацию (этап 12.5c): вместо
        N_positions × M_keys substring-проверок (~8500 × 10 = 85k regex)
        делаем тот же _detect_our_category(path) только N_categories раз
        (~350) — один раз для каждой ветки дерева. Дальше per-position —
        просто dict.get(leaf_category_id).

        Дополнительно собирает аудит-метрики:
          - распределение категорий по нашим our_category (для INFO-лога);
          - WARNING для веток с productsQty > 0, классифицированных как
            None — это возможные ложные срабатывания blocklist'а (целевая
            ветка ошибочно отрезана).

        productsQty (а не totalProductsQty) — потому что нас интересуют
        ИМЕННО узлы с собственными товарами; промежуточные ветки без
        своих products покрываются через свои children, которые будут
        обработаны рекурсивно на их собственном уровне."""
        cat_map: dict[int, str | None] = {}
        counts_by_cat: dict[str, int] = {}
        none_count = 0
        # Все ветки с productsQty > 0, но our_category=None — для аудита
        # blocklist'а. Логируем общее число + первые 5 примеров.
        audit_misses: list[tuple[str, str, int]] = []

        def _walk(nodes: list[Any] | None, parent_path: tuple[str, ...]) -> None:
            nonlocal none_count
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                name = node.get("name") or node.get("rusName") or ""
                cur_path = (*parent_path, str(name)) if name else parent_path
                our = _detect_our_category(list(cur_path))
                cat_id_raw = node.get("id")
                if isinstance(cat_id_raw, int):
                    cat_map[cat_id_raw] = our
                if our is None:
                    none_count += 1
                    qty_raw = node.get("productsQty") or 0
                    try:
                        qty = int(qty_raw)
                    except (TypeError, ValueError):
                        qty = 0
                    if qty > 0:
                        audit_misses.append(
                            (str(name), " → ".join(cur_path), qty)
                        )
                else:
                    counts_by_cat[our] = counts_by_cat.get(our, 0) + 1
                kids = node.get("children") or []
                if isinstance(kids, list) and kids:
                    _walk(kids, cur_path)

        _walk(categories, ())

        counts_summary = ", ".join(
            f"{k}={v}" for k, v in sorted(counts_by_cat.items())
        ) or "—"
        logger.info(
            "Treolan: построен category_map (%d категорий, %s, none=%d).",
            len(cat_map), counts_summary, none_count,
        )

        if audit_misses:
            sample = audit_misses[:5]
            sample_str = "; ".join(
                f"'{n}' (path={p!r}, productsQty={q})"
                for n, p, q in sample
            )
            logger.warning(
                "Treolan: %d ветка(и) с productsQty>0 классифицированы как None — "
                "проверьте blocklist на ложные срабатывания. Первые 5: %s",
                len(audit_misses), sample_str,
            )

        return cat_map

    # ---- Save ----------------------------------------------------------

    def _save(self, data: dict[str, Any]) -> int:
        """Перегоняет товары из дерева categories→children/products в PriceRow
        и зовёт общий save-pipeline.

        12.3-fix: ответ Treolan — иерархия, а не плоский positions[]. Здесь
        DFS через _walk_products(); если categories=[] или после walk
        получили 0 товаров — RuntimeError, чтобы run закрылся failed и
        orchestrator НЕ запустил disappeared.

        Возвращает price_uploads.id."""
        categories = data.get("categories") or []
        if not categories:
            raise RuntimeError(
                "Treolan API: ответ не содержит categories[] "
                f"(top-level keys: {sorted(data.keys()) if isinstance(data, dict) else type(data).__name__})."
            )

        # 12.5c: строим category_map один раз — дальше per-position O(1) lookup.
        self._category_map = self._build_category_map(categories)

        session = SessionLocal()
        try:
            usd_rate = _latest_usd_rub_rate(session)
        finally:
            session.close()

        rows: list[PriceRow] = []
        unmapped_currency_count = 0
        skipped_no_articul = 0
        skipped_no_name = 0
        skipped_no_price = 0
        total_walked = 0
        fallback_lookups = 0  # категории, не попавшие в map (страховка)
        for idx, (cat_path, cat_id, pos) in enumerate(
            _walk_products(categories), start=1,
        ):
            total_walked += 1

            articul = (pos.get("articul") or "").strip() if pos.get("articul") else ""
            if not articul:
                skipped_no_articul += 1
                continue
            name = (pos.get("rusName") or pos.get("description") or "").strip()
            if not name:
                skipped_no_name += 1
                continue

            currency = (pos.get("currency") or "").strip().upper()

            # currentPrice приоритетен; если 0 — fallback на price.
            price_raw = _to_decimal(pos.get("currentPrice"))
            if price_raw is None:
                price_raw = _to_decimal(pos.get("price"))
            if price_raw is None:
                skipped_no_price += 1
                continue

            if currency == "USD":
                if usd_rate is None:
                    unmapped_currency_count += 1
                    continue
                price_rub = (price_raw * usd_rate).quantize(Decimal("0.01"))
            elif currency == "RUB":
                price_rub = price_raw
            else:
                unmapped_currency_count += 1
                logger.warning(
                    "Treolan API: позиция %s — неизвестная валюта %r, пропуск.",
                    articul, currency,
                )
                continue

            # 12.5c: lookup по leaf_category_id; substring-fallback на
            # случай, если позиция почему-то отнесена к категории, не
            # попавшей в map (например, узел без int id).
            if cat_id is not None and cat_id in self._category_map:
                our_category = self._category_map[cat_id]
            else:
                fallback_lookups += 1
                our_category = _detect_our_category(cat_path)

            rows.append(PriceRow(
                supplier_sku=articul,
                mpn=articul,
                gtin=_normalize_gtin(pos.get("gtin")),
                brand=(pos.get("vendor") or "").strip() or None,
                raw_category=" / ".join(cat_path),
                our_category=our_category,
                name=name,
                price=price_rub,
                currency="RUB",  # после конвертации храним всё в RUB
                stock=_parse_stock_str(pos.get("atStock")),
                transit=_parse_stock_str(pos.get("atTransit")),
                row_number=idx,
            ))

        if total_walked == 0:
            raise RuntimeError(
                "Treolan API: после обхода дерева не найдено ни одного товара "
                f"(categories={len(categories)}, products=0). Возможно, формат "
                "ответа изменился — проверьте /v1/Catalog/Get."
            )

        logger.info(
            "Treolan API: получено %d товаров из %d корневых категорий — "
            "uploaded=%d, skipped(no_articul=%d, no_name=%d, no_price=%d, "
            "unmapped_currency=%d), category_map_fallback=%d.",
            total_walked, len(categories), len(rows),
            skipped_no_articul, skipped_no_name, skipped_no_price,
            unmapped_currency_count, fallback_lookups,
        )

        if unmapped_currency_count:
            logger.info(
                "Treolan API: %d позиций пропущено из-за неизвестной валюты "
                "(или отсутствующего курса USD).",
                unmapped_currency_count,
            )

        # Файл-имя для price_uploads — отметка дня. UI журнала покажет «откуда».
        virtual_filename = f"auto_treolan_api_{date.today().isoformat()}.json"

        # Импорт здесь, а не на уровне модуля — иначе на pytest-xdist
        # импорт fetcher'а во время регистрации тянул бы тяжёлые зависимости
        # orchestrator (включая ALLOWED_TABLES, openpyxl и т.п.).
        from app.services.price_loaders.orchestrator import save_price_rows

        result = save_price_rows(
            supplier_name="Treolan",
            source=virtual_filename,
            rows=rows,
        )
        return int(result["upload_id"])


# ---- Тестовая поддержка: сброс кеша токена ----------------------------

def _reset_token_cache_for_tests() -> None:
    """В тестах вызываем перед тестовым сценарием, чтобы предыдущий тест
    не подсунул нам свой токен. Не делаем это публичным API — только для
    test-фикстур."""
    _TOKEN_CACHE["token"] = None
    _TOKEN_CACHE["exp_ts"] = 0


# Убедимся, что класс зарегистрирован в base — повторный импорт безопасен
# из-за идемпотентного check'а в register_fetcher.
def _iter_supported_keywords() -> Iterable[str]:
    return _CATEGORY_NAME_MAP.keys()
