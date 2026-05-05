# Авто-загрузка прайса Treolan через REST API + JWT (этап 12.3).
#
# Поток:
#   _get_token()     — POST /v1/auth/token (или fallback /v1/auth/login),
#                       JWT в теле ответа, кеш 1ч до exp.
#   _fetch_catalog() — POST /v1/Catalog/Get с пустыми фильтрами (весь
#                       склад), Bearer token, retry 5/15/45 на сетевых
#                       ошибках и 5xx, на 401 — сброс токена и 1 повтор.
#   _save()          — преобразует positions[] в PriceRow и зовёт
#                       общий orchestrator.save_price_rows() — тот же
#                       pipeline что и /admin/price-uploads (upsert
#                       supplier_prices, mapping, disappeared, etc.).
#
# Конвертация валют:
#   currency='USD' → price * cb_rate_usd_rub из exchange_rates на
#                    последний день; результат записывается в RUB.
#   currency='RUB' → как есть.
#   иначе          → позиция пропускается, в лог warning «unmapped currency».

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import httpx
from sqlalchemy import text

from app.services.auto_price.base import BaseAutoFetcher, register_fetcher
from app.services.price_loaders.models import PriceRow
from shared.db import SessionLocal


logger = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "https://api.treolan.ru/api"


# Категории Treolan (из rusName категории) → наша категория.
# Неполный список: API возвращает иерархию, но самый практичный фильтр —
# по наличию category в позиции (категории приходят отдельным массивом
# и в самой позиции есть поле category-id; для упрощения опираемся на
# rusName/название категории — иначе нужно тянуть отдельный запрос
# /Catalog/GetCategories. Пока отдаём our_category=None и пусть
# resolve() в orchestrator смотрит по brand/name через NLU. Для корректной
# первичной загрузки этого хватает.
#
# Если потребуется явный mapping — расширяется здесь и в Excel-loader'е
# (treolan.py); пока оставляем единый минимальный набор по партномеру.
_CATEGORY_NAME_MAP: dict[str, str] = {
    # Слова в rusName категории (case-insensitive substring match)
    "процессор":             "cpu",
    "материнск":             "motherboard",
    "оперативн":             "ram",
    "видеокарт":             "gpu",
    "ssd":                   "storage",
    "жестк":                 "storage",
    "корпус":                "case",
    "блок питания":          "psu",
    "бп для":                "psu",
    "охлажд":                "cooler",
}


def _detect_our_category(raw_category_name: str | None) -> str | None:
    """Маппит название категории Treolan в нашу. None — категория
    не относится к ПК-комплектующим (периферия и т.п.); orchestrator
    такие позиции пропустит."""
    if not raw_category_name:
        return None
    s = raw_category_name.lower()
    for kw, cat in _CATEGORY_NAME_MAP.items():
        if kw in s:
            return cat
    return None


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

    # ---- Save ----------------------------------------------------------

    def _save(self, data: dict[str, Any]) -> int:
        """Перегоняет positions[] в PriceRow и зовёт общий save-pipeline.

        Возвращает price_uploads.id."""
        positions = data.get("positions") or []
        categories = data.get("categories") or []

        # Категории приходят отдельным массивом, у позиций — category-id.
        # Соберём id → rusName, чтобы _detect_our_category мог сработать.
        cat_id_to_name: dict[Any, str] = {}
        for c in categories:
            if not isinstance(c, dict):
                continue
            cid = c.get("id") or c.get("Id") or c.get("ID")
            cname = c.get("rusName") or c.get("name") or c.get("title")
            if cid is not None and cname:
                cat_id_to_name[cid] = str(cname)

        session = SessionLocal()
        try:
            usd_rate = _latest_usd_rub_rate(session)
        finally:
            session.close()

        rows: list[PriceRow] = []
        unmapped_currency_count = 0
        for idx, pos in enumerate(positions, start=1):
            if not isinstance(pos, dict):
                continue
            articul = (pos.get("articul") or "").strip() if pos.get("articul") else ""
            if not articul:
                continue
            name = (pos.get("rusName") or pos.get("description") or "").strip()
            if not name:
                continue

            currency = (pos.get("currency") or "").strip().upper()

            # currentPrice приоритетен; если 0 — fallback на price.
            price_raw = _to_decimal(pos.get("currentPrice"))
            if price_raw is None:
                price_raw = _to_decimal(pos.get("price"))
            if price_raw is None:
                continue

            if currency == "USD":
                if usd_rate is None:
                    # exchange_rates пустой — при первом старте ещё не подтянули
                    # курс. Пропускаем USD-позицию, RUB-позиции загрузятся.
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

            cat_id = pos.get("category-id") or pos.get("categoryId") or pos.get("category")
            cat_name = cat_id_to_name.get(cat_id) if not isinstance(cat_id, str) else cat_id
            if isinstance(cat_id, str) and cat_id:
                cat_name = cat_id  # category иногда приходит уже как имя
            our_category = _detect_our_category(cat_name)

            rows.append(PriceRow(
                supplier_sku=articul,
                mpn=articul,
                gtin=_normalize_gtin(pos.get("gtin")),
                brand=(pos.get("vendor") or "").strip() or None,
                raw_category=str(cat_name or ""),
                our_category=our_category,
                name=name,
                price=price_rub,
                currency="RUB",  # после конвертации храним всё в RUB
                stock=_to_int(pos.get("atStock")),
                transit=_to_int(pos.get("inTransit") or pos.get("transit")),
                row_number=idx,
            ))

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
