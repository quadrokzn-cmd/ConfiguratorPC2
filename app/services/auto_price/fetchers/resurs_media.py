# Авто-загрузка прайса «Ресурс Медиа» через SOAP API + zeep (этап 12.4-РМ-1).
#
# 5-й канал автозагрузки. По аналогии с Treolan (REST), но через SOAP/zeep.
#
# Поток fetch_and_save():
#   1. Поднять zeep.Client с requests.Session, авторизация — HTTP BasicAuth.
#      InMemoryCache — чтобы повторный fetch_and_save() в том же процессе
#      не парсил WSDL заново. Кэш — на инстансе fetcher'а (не глобально):
#      на gunicorn-workers процессы изолированы, общий module-level cache
#      нам не нужен и усложнял бы юнит-тестирование.
#   2. _call_with_rate_limit("GetPrices", ...) — один вызов на ВСЕ группы.
#      Запрос содержит MaterialGroup_Tab со всеми _ALL_GROUP_IDS (12 шт.,
#      покрывают наши 8 категорий — у storage и psu по несколько групп).
#      WareHouseID="00011" (Москва) — единственный склад на test/prod пока.
#   3. _call_with_rate_limit("GetMaterialData", ...) — один вызов на все
#      MaterialID, полученные на шаге 2. Без характеристик/баркодов/изображений
#      (нам нужны только vendor/vendor_part/material_text/material_group).
#   4. Сборка PriceRow по уникальным MaterialID. Маппинг group_id → our_category
#      через _GROUP_TO_OUR_CATEGORY (производное от _CATEGORY_GROUP_MAP).
#      Позиции, у которых group_id вне наших 8 категорий, пропускаются.
#   5. save_price_rows(...) — общий orchestrator-pipeline (тот же, что у
#      Treolan/OCS/Merlion/Netlab), возвращает price_uploads.id.
#
# Result-коды Resurs Media (от _call_with_rate_limit):
#   0           — успех.
#   1           — общая ошибка API → RuntimeError(ErrorMessage).
#   3           — rate-limit. Из ErrorMessage парсим N сек, sleep N+2,
#                 один retry. На повторе тот же 3 → RuntimeError (не loop).
#   4           — работа с заказами отключена. Для нас (только цены) это
#                 НЕ блокер, но семантически «нет новых данных» — поэтому
#                 NoNewDataException. Runner пометит run 'no_new_data'.
#   None        — встречается у части операций (zeep отдаёт его, когда
#                 поле Result отсутствует в ответе). Считаем успехом.
#   иначе       — RuntimeError с дампом ответа.
#
# Замечание по env-переменной:
#   В .env уже есть RESURS_MEDIA_WSDL_URL_TEST (test endpoint, разведка).
#   Здесь читаем сначала RESURS_MEDIA_WSDL_URL (канон), при его отсутствии —
#   fallback на _TEST для обратной совместимости. На проде админ
#   пропишет RESURS_MEDIA_WSDL_URL=<prod URL> и удалит _TEST из .env.

from __future__ import annotations

import logging
import os
import re
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

import requests
from requests.auth import HTTPBasicAuth
import zeep
from zeep.cache import InMemoryCache
from zeep.helpers import serialize_object
from zeep.transports import Transport

from app.services.auto_price.base import BaseAutoFetcher, register_fetcher
from app.services.auto_price.fetchers.base_imap import NoNewDataException
from app.services.price_loaders.models import PriceRow


logger = logging.getLogger(__name__)


# Маппинг наших категорий в group_id (MaterialGroup) Resurs Media. Ключи
# взяты из разведки 12.4-РМ-0 (см. scripts/_diag_resurs_media_catalog_tree.txt).
# Все 8 наших категорий покрыты; у storage три группы (внутренние HDD + SSD
# + флеш-носители как один зонтик), у psu — две (Z999-919999 — БП для
# корпусов, Z999-9992 — серверные/прочие БП).
_CATEGORY_GROUP_MAP: dict[str, list[str]] = {
    "psu":         ["Z999-919999", "Z999-9992"],
    "cooler":      ["Z999-999979"],
    "gpu":         ["Z999-10001"],
    "storage":     ["Z383", "Z897", "Z373"],
    "motherboard": ["Z999-10006"],
    "ram":         ["Z431"],
    "case":        ["Z999-911999"],
    "cpu":         ["Z999-10110"],
}

# Обратный индекс: group_id → our_category. Один dict-lookup на позицию
# в _save_rows вместо линейного перебора.
_GROUP_TO_OUR_CATEGORY: dict[str, str] = {
    group_id: our_cat
    for our_cat, groups in _CATEGORY_GROUP_MAP.items()
    for group_id in groups
}

# Все group_id одним списком — для одного-единственного MaterialGroup_Tab
# в GetPrices. Порядок не важен.
_ALL_GROUP_IDS: list[str] = [
    g for groups in _CATEGORY_GROUP_MAP.values() for g in groups
]

# Москва — пока единственный склад поставщика, и в test- и в prod-стендах.
# После prod-разведки (12.4-РМ-?) вынесем в env, если складов будет больше
# одного и это окажется значимым.
_DEFAULT_WAREHOUSE_ID = "00011"

# Таймауты zeep.Transport. SOAP может быть медленным, особенно GetPrices
# на полный каталог — 60 секунд оставляем как страховку. operation_timeout
# применяется к каждому HTTP-запросу к endpoint'у; timeout — к загрузке
# WSDL/XSD. Из-за InMemoryCache последний касается только первого вызова
# в процессе.
_WSDL_LOAD_TIMEOUT = 30
_OPERATION_TIMEOUT = 60

# Сколько секунд прибавляем к интервалу из ErrorMessage перед retry.
# +2 на сетевой джиттер: тестовый стенд иногда отдаёт 3 даже после ровно
# заявленного интервала.
_RATE_LIMIT_PADDING_SECONDS = 2


# ---- Парсеры значений из SOAP-ответа -----------------------------------

def _decimal_or_none(value: Any) -> Decimal | None:
    """SOAP отдаёт Price как float (Decimal в WSDL), но через JSON-сериализацию
    он может прилететь и строкой («188.49»), и числом. Унифицируем."""
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


def _int_or_none(value: Any) -> int | None:
    """AvailableCount приходит строкой («42», «0», иногда пусто). Нам нужен
    int для PriceRow.stock — None превратим в 0 на стороне сборки."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(Decimal(s.replace(",", ".")))
    except (InvalidOperation, ValueError):
        return None


def _strip_or_empty(value: Any) -> str:
    """SOAP-сервер выравнивает MaterialID/MaterialGroup пробелами справа
    («К104       »). Везде, где это поле — ключ или ссылка, обязательно
    стрипать, иначе lookup в _GROUP_TO_OUR_CATEGORY промахнётся."""
    if value is None:
        return ""
    return str(value).strip()


# ---- Хелперы для распаковки zeep-ответа -------------------------------

def _zeep_to_dict(obj: Any) -> Any:
    """zeep CompoundValue → обычный dict/list. На реальном сервере это
    нужно, чтобы dict-style доступ ['Material_Tab'] работал; на моках
    в тестах — тоже работает (dict.serialize_object тождественна).
    Возвращает obj как есть, если он уже dict/list/None."""
    if obj is None:
        return None
    if isinstance(obj, (dict, list)):
        return obj
    return serialize_object(obj, target_cls=dict)


def _get(resp: Any, *keys: str) -> Any:
    """Аналог .get(...) для zeep CompoundValue / dict. Возвращает первое
    непустое значение из перечисленных ключей. Ничего не нашёл — None."""
    if resp is None:
        return None
    if isinstance(resp, dict):
        for k in keys:
            if k in resp and resp[k] is not None:
                return resp[k]
        return None
    for k in keys:
        v = getattr(resp, k, None)
        if v is not None:
            return v
    return None


def _items_in_tab(tab: Any) -> list[Any]:
    """Извлекает список Item-ов из *_Tab-обёртки.

    GetPrices возвращает Material_Tab как ЛИСТ напрямую (без обёртки Item):
        Material_Tab: [{...}, {...}, ...]
    GetMaterialData возвращает MaterialData_Tab как dict с ключом Item:
        MaterialData_Tab: {"Item": [{...}, {...}, ...]}
    Принимаем оба варианта."""
    if tab is None:
        return []
    if isinstance(tab, list):
        return [t for t in tab if t]
    inner = _get(tab, "Item", "MaterialData", "Material")
    if inner is None:
        return []
    if isinstance(inner, list):
        return [t for t in inner if t]
    return [inner] if inner else []


# =====================================================================
# ResursMediaApiFetcher
# =====================================================================

@register_fetcher
class ResursMediaApiFetcher(BaseAutoFetcher):
    """SOAP-API канал «Ресурс Медиа». См. модульный docstring."""

    supplier_slug = "resurs_media"
    supplier_display_name = "Ресурс Медиа"

    def __init__(self) -> None:
        # WSDL: канон — RESURS_MEDIA_WSDL_URL. На переходный период (test
        # endpoint, разведка 12.4-РМ-0 закидывала именно _TEST в .env) —
        # fallback на _TEST. После переключения на prod админ переименует
        # переменную и старый fallback не понадобится.
        self.wsdl_url = (
            (os.environ.get("RESURS_MEDIA_WSDL_URL") or "").strip()
            or (os.environ.get("RESURS_MEDIA_WSDL_URL_TEST") or "").strip()
        )
        self.username = (os.environ.get("RESURS_MEDIA_USERNAME") or "").strip()
        self.password = (os.environ.get("RESURS_MEDIA_PASSWORD") or "").strip()
        if not self.wsdl_url or not self.username or not self.password:
            raise RuntimeError(
                "Resurs Media SOAP: не заданы креды. Ожидаются переменные "
                "окружения: RESURS_MEDIA_WSDL_URL (на переходный период — "
                "RESURS_MEDIA_WSDL_URL_TEST), RESURS_MEDIA_USERNAME, "
                "RESURS_MEDIA_PASSWORD."
            )
        # Per-instance кеш WSDL. Чтобы повторное создание fetcher'а в одном
        # процессе (например в тестах) не упиралось в zeep cache, ключ
        # которого определяется именем процесса/файлом.
        self._wsdl_cache = InMemoryCache()
        # Lazy: реальный zeep.Client поднимется в _client(). Так в тестах,
        # которые мокают zeep.Client, не нужен живой WSDL на __init__.
        self._client: zeep.Client | None = None
        self._warehouse_id = _DEFAULT_WAREHOUSE_ID

    # ---- main entrypoint ----------------------------------------------

    def fetch_and_save(self) -> int:
        """Полный SOAP-цикл: GetPrices → GetMaterialData → save_price_rows.
        Возвращает price_uploads.id."""
        client = self._get_client()

        # 1. GetPrices — один вызов на все group_id.
        prices_resp = self._call_with_rate_limit(
            client,
            "GetPrices",
            WareHouseID=self._warehouse_id,
            MaterialGroup_Tab={
                "Item": [{"MaterialGroup": gid} for gid in _ALL_GROUP_IDS],
            },
            GetAvailableCount=True,
        )
        raw_items = _items_in_tab(_get(prices_resp, "Material_Tab"))
        if not raw_items:
            raise NoNewDataException(
                "Resurs Media: GetPrices вернул пустой Material_Tab по всем "
                f"{len(_ALL_GROUP_IDS)} group_id (warehouse={self._warehouse_id})."
            )

        # 2. GetMaterialData — один вызов на все уникальные MaterialID.
        material_ids = sorted({
            _strip_or_empty(_get(item, "MaterialID"))
            for item in raw_items
            if _strip_or_empty(_get(item, "MaterialID"))
        })
        if not material_ids:
            raise NoNewDataException(
                "Resurs Media: GetPrices вернул позиции без MaterialID — "
                "обогащать через GetMaterialData нечем."
            )
        md_resp = self._call_with_rate_limit(
            client,
            "GetMaterialData",
            MaterialID_Tab={
                "Item": [{"MaterialID": mid} for mid in material_ids],
            },
            WithCharacteristics=False,
            WithBarCodes=False,
            WithCertificates=False,
            WithImages=False,
        )
        md_index = self._build_material_index(md_resp)

        # 3. Сборка PriceRow.
        return self._save_rows(raw_items, md_index)

    # ---- client construction ------------------------------------------

    def _get_client(self) -> zeep.Client:
        """Создаёт zeep.Client с BasicAuth. Lazy — поднимаем при первом
        вызове, переиспользуем в течение жизни инстанса fetcher'а."""
        if self._client is not None:
            return self._client
        session = requests.Session()
        session.auth = HTTPBasicAuth(self.username, self.password)
        transport = Transport(
            session=session,
            cache=self._wsdl_cache,
            timeout=_WSDL_LOAD_TIMEOUT,
            operation_timeout=_OPERATION_TIMEOUT,
        )
        self._client = zeep.Client(wsdl=self.wsdl_url, transport=transport)
        return self._client

    # ---- rate-limit retry ----------------------------------------------

    def _call_with_rate_limit(
        self, client: zeep.Client, operation: str, **kwargs: Any,
    ) -> Any:
        """Зовёт client.service.<operation>(**kwargs), интерпретирует Result."""
        resp = self._invoke(client, operation, kwargs)
        result = _get(resp, "Result")
        error_msg = _get(resp, "ErrorMessage", "ErrorText")

        if result == 3:
            wait_s = _parse_rate_limit_seconds(error_msg)
            logger.warning(
                "Resurs Media %s: Result=3 (rate-limit), жду %d сек, retry. "
                "ErrorMessage=%r",
                operation, wait_s + _RATE_LIMIT_PADDING_SECONDS, error_msg,
            )
            time.sleep(wait_s + _RATE_LIMIT_PADDING_SECONDS)
            resp = self._invoke(client, operation, kwargs)
            result = _get(resp, "Result")
            error_msg = _get(resp, "ErrorMessage", "ErrorText")
            if result == 3:
                # Не loop'имся — на втором подряд rate-limit'е скорее всего
                # сервер в плохом состоянии или мы что-то делаем не так.
                raise RuntimeError(
                    f"Resurs Media {operation}: повторный Result=3 после "
                    f"паузы {wait_s + _RATE_LIMIT_PADDING_SECONDS} сек. "
                    f"ErrorMessage={error_msg!r}."
                )

        if result == 4:
            raise NoNewDataException(
                f"Resurs Media {operation}: Result=4 (работа с заказами/API "
                f"отключена). ErrorMessage={error_msg!r}."
            )
        if result == 1:
            raise RuntimeError(
                f"Resurs Media {operation}: Result=1 (общая ошибка API). "
                f"ErrorMessage={error_msg!r}."
            )
        if result not in (0, None):
            # Неизвестный код — не глотаем; пусть админ увидит дамп в журнале.
            raise RuntimeError(
                f"Resurs Media {operation}: неожиданный Result={result!r}. "
                f"ErrorMessage={error_msg!r}."
            )
        return resp

    @staticmethod
    def _invoke(client: zeep.Client, operation: str, kwargs: dict) -> Any:
        """Один вызов SOAP-операции. Изолировано в методе, чтобы тесты
        могли подменить только его, не трогая всю обвязку retry."""
        op = getattr(client.service, operation)
        return op(**kwargs)

    # ---- enrichment & assembly -----------------------------------------

    @staticmethod
    def _build_material_index(md_resp: Any) -> dict[str, dict[str, str]]:
        """{MaterialID (stripped): {vendor, vendor_part, material_text,
        material_group}}. Используется в _save_rows для обогащения позиций
        из GetPrices, у которых нет ни бренда, ни группы."""
        items = _items_in_tab(_get(md_resp, "MaterialData_Tab"))
        index: dict[str, dict[str, str]] = {}
        for m in items:
            mid = _strip_or_empty(_get(m, "MaterialID"))
            if not mid:
                continue
            index[mid] = {
                "vendor":         _strip_or_empty(_get(m, "Vendor")),
                "vendor_part":    _strip_or_empty(_get(m, "VendorPart")),
                "material_text":  _strip_or_empty(_get(m, "MaterialText")),
                "material_group": _strip_or_empty(_get(m, "MaterialGroup")),
            }
        return index

    def _save_rows(
        self,
        raw_items: list[Any],
        md_index: dict[str, dict[str, str]],
    ) -> int:
        """Превращает позиции GetPrices+GetMaterialData в PriceRow и зовёт
        save_price_rows. Возвращает price_uploads.id."""
        rows: list[PriceRow] = []
        skipped_no_md = 0
        skipped_unknown_group = 0
        skipped_no_price = 0
        for idx, item in enumerate(raw_items, start=1):
            material_id = _strip_or_empty(_get(item, "MaterialID"))
            if not material_id:
                continue
            md = md_index.get(material_id)
            if md is None:
                # GetMaterialData не вернул данных по этому MaterialID. На
                # реальном API такое — редкий рассинхрон между каталогом и
                # запасом, на тестовом стенде не наблюдалось. Логируем
                # один раз счётчиком, чтобы в проде не залить лог.
                skipped_no_md += 1
                continue
            group_id = md["material_group"]
            our_category = _GROUP_TO_OUR_CATEGORY.get(group_id)
            if our_category is None:
                # Позиция не из наших 8 категорий. Так на test-стенде
                # отфильтруются Дискеты (Z017) и т.п. — норма.
                skipped_unknown_group += 1
                continue
            price = _decimal_or_none(_get(item, "Price"))
            if price is None:
                skipped_no_price += 1
                continue
            stock = _int_or_none(_get(item, "AvailableCount")) or 0

            # PartNum (внутренний код Resurs Media с префиксом производителя)
            # сохраняем в part_num только в логе — в PriceRow его положить
            # некуда; для матчинга используется mpn (=VendorPart) и
            # supplier_sku (=MaterialID). Если в проде потребуется матч
            # по PartNum — добавим поле в PriceRow в отдельном этапе.
            mpn = md["vendor_part"] or None
            rows.append(PriceRow(
                supplier_sku=material_id,
                mpn=mpn,
                gtin=None,
                brand=md["vendor"] or None,
                raw_category=group_id,
                our_category=our_category,
                name=md["material_text"],
                price=price,
                currency="RUB",
                stock=stock,
                transit=0,
                row_number=idx,
            ))

        logger.info(
            "Resurs Media: GetPrices=%d позиций, GetMaterialData=%d, "
            "rows=%d (skipped no_md=%d, unknown_group=%d, no_price=%d).",
            len(raw_items), len(md_index), len(rows),
            skipped_no_md, skipped_unknown_group, skipped_no_price,
        )

        if not rows:
            # Все позиции отфильтровались — на test-стенде это нормально
            # (там только Дискеты, не из наших категорий). Без NoNewData
            # save_price_rows вернул бы failed (rows_total=0) и runner
            # пометил бы запуск как error — это вводит в заблуждение.
            # 'no_new_data' семантически точнее.
            raise NoNewDataException(
                "Resurs Media: ни одной позиции из 8 целевых категорий — "
                f"GetPrices вернул {len(raw_items)} шт., все отфильтрованы "
                "(no_md/unknown_group/no_price). Возможно, маппинг "
                "_CATEGORY_GROUP_MAP не покрывает реальные группы prod-стенда."
            )

        # Имя для price_uploads — отметка дня, чтобы UI журнала отличал
        # автозагрузки от ручных и от других каналов.
        virtual_filename = f"auto_resurs_media_soap_{date.today().isoformat()}.json"

        # Импорт здесь, а не на уровне модуля — у orchestrator-а тяжёлые
        # транзитивные зависимости (openpyxl и т.п.); fetcher-модуль
        # импортируется при регистрации в auto_price/__init__.py.
        from app.services.price_loaders.orchestrator import save_price_rows

        result = save_price_rows(
            supplier_name=self.supplier_display_name,
            source=virtual_filename,
            rows=rows,
            extra_report={
                "resurs_media": {
                    "raw_items_count":       len(raw_items),
                    "material_data_count":   len(md_index),
                    "rows_built":            len(rows),
                    "skipped_no_md":         skipped_no_md,
                    "skipped_unknown_group": skipped_unknown_group,
                    "skipped_no_price":      skipped_no_price,
                    "warehouse_id":          self._warehouse_id,
                },
            },
        )
        return int(result["upload_id"])


# ---- Helpers ----------------------------------------------------------

_RATE_LIMIT_RE = re.compile(r"(\d+)\s*сек", re.IGNORECASE)


def _parse_rate_limit_seconds(error_message: Any) -> int:
    """Достаёт N сек из ErrorMessage Resurs Media. На API форма сообщений:
    «Разрешенный интервал между запросами … 60 сек.» Если регекс не
    сработал — возвращаем 65 (приблизительный безопасный дефолт)."""
    if not error_message:
        return 65
    m = _RATE_LIMIT_RE.search(str(error_message))
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return 65
    return 65
