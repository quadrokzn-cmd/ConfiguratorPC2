# Локальный образ каталога «Ресурс Медиа» — инкрементальная дельта
# GetMaterialData (мини-этап 2026-05-12).
#
# Контекст. По spec API_РМ_v7.5 раздел «Методические требования к работе
# с данными» (стр. 4-5) рекомендует разовый GetMaterialData по всему
# каталогу с сохранением локально, и регулярную сверку MaterialID из
# GetPrices с локальной таблицей — по новинкам звать GetMaterialData,
# а свежие данные брать из локала.
#
# До этого этапа fetcher.fetch_and_save() звал GetMaterialData по всему
# списку MaterialID из каждого GetPrices (~25 729 позиций на test-стенде),
# что создавало лишнее давление на rate-limit РМ. Теперь — только по
# дельте: «новые + stale > 30 дней».
#
# Этот модуль — pure helpers поверх таблицы resurs_media_catalog
# (миграция 0037_resurs_media_catalog.sql). Сетевых вызовов нет: SOAP
# делает fetcher, парсинг ответа — здесь.

from __future__ import annotations

import json
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from portal.services.configurator.auto_price.fetchers.resurs_media import (
    _get,
    _items_in_tab,
    _strip_or_empty,
)


logger = logging.getLogger(__name__)


# Stale-порог по умолчанию. 30 дней защищает от устаревания атрибутов
# (вес/штрих-коды/сертификаты могут меняться у поставщика без замены
# MaterialID). Параметризован у compute_delta — тесты передают свои
# значения, и в будущем при необходимости можно вынести в settings.
DEFAULT_STALE_AFTER = timedelta(days=30)


# Поля, которые мы держим в плоских колонках для быстрых выборок.
# raw_jsonb всё равно хранит полный ответ — эти колонки нужны, чтобы
# UI/SQL-аналитика могли не парсить JSON.
_FLAT_FIELDS = (
    "part_num",
    "material_text",
    "material_group",
    "vendor",
    "vendor_part",
    "unit_of_measurement",
    "multiplicity",
    "weight",
    "volume",
    "width",
    "length",
    "height",
    "vat",
    "web_description",
)

# Какие из плоских полей — числовые (NUMERIC в БД). Конвертируем в
# Decimal/None через _decimal_or_none перед параметризацией.
_NUMERIC_FIELDS = frozenset((
    "multiplicity", "weight", "volume", "width", "length", "height", "vat",
))


def _decimal_or_none(value: Any) -> Decimal | None:
    """РМ SOAP отдаёт числа то как float, то как str, иногда с запятой.
    Унифицируем; пусто/мусор → None (psycopg запишет NULL в NUMERIC)."""
    if value is None:
        return None
    s = str(value).strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


# ---------------------------------------------------------------------
# compute_delta
# ---------------------------------------------------------------------

def compute_delta(
    engine: Engine,
    material_ids: list[str],
    stale_after: timedelta = DEFAULT_STALE_AFTER,
) -> tuple[list[str], dict[str, dict[str, str]]]:
    """Разбивает входной список MaterialID на «надо запросить через
    GetMaterialData» (новые + stale) и «уже есть в локале» (свежие).

    Возвращает:
      ids_to_fetch — отсортированный список MaterialID для GetMaterialData.
      cached_data  — dict {material_id: {vendor, vendor_part, material_text,
                     material_group}} для свежих позиций. Формат совпадает
                     с тем, что строит _build_material_index у fetcher'а —
                     fetcher переиспользует его в _save_rows без адаптера.

    Stale-порог измеряется в БД как `synced_at < NOW() - stale_after`.
    """
    if not material_ids:
        return [], {}

    # Уникализуем и стрипаем — на случай дублей в Material_Tab GetPrices.
    unique_ids = sorted({mid.strip() for mid in material_ids if mid and mid.strip()})
    if not unique_ids:
        return [], {}

    # Один запрос: вытаскиваем все известные MaterialID одним SELECT'ом
    # и решаем «свежий vs stale» в Python. NUMERIC-сравнение по synced_at
    # делаем тоже SQL'ом, чтобы не тащить tzinfo туда-обратно.
    threshold_seconds = int(stale_after.total_seconds())
    sql = text(
        "SELECT material_id, vendor, vendor_part, material_text, "
        "       material_group, "
        "       (synced_at >= NOW() - make_interval(secs => :sec)) AS is_fresh "
        "  FROM resurs_media_catalog "
        " WHERE material_id = ANY(:ids)"
    )
    with engine.begin() as conn:
        rows = conn.execute(
            sql, {"ids": unique_ids, "sec": threshold_seconds},
        ).all()

    cached_data: dict[str, dict[str, str]] = {}
    fresh_ids: set[str] = set()
    for r in rows:
        mid = r.material_id
        if r.is_fresh:
            fresh_ids.add(mid)
            cached_data[mid] = {
                "vendor":         (r.vendor or ""),
                "vendor_part":    (r.vendor_part or ""),
                "material_text":  (r.material_text or ""),
                "material_group": (r.material_group or ""),
            }
        # stale-строка не идёт в cached_data: её данные могли протухнуть,
        # пускай fetcher перезапросит. После upsert_catalog'а попадёт
        # в md_index уже свежей.

    ids_to_fetch = sorted(mid for mid in unique_ids if mid not in fresh_ids)
    return ids_to_fetch, cached_data


# ---------------------------------------------------------------------
# upsert_catalog
# ---------------------------------------------------------------------

def _flat_value(item: Any, soap_key: str) -> Any:
    """Достаёт значение из SOAP-item'а по ключу-camelCase."""
    return _strip_or_empty(_get(item, soap_key)) or None


# Маппинг SOAP-имя → имя плоской колонки в БД. В spec у разных операций
# поля одинаково именованы (CamelCase), поэтому пары статичные.
_SOAP_TO_FLAT: dict[str, str] = {
    "PartNum":             "part_num",
    "MaterialText":        "material_text",
    "MaterialGroup":       "material_group",
    "Vendor":              "vendor",
    "VendorPart":          "vendor_part",
    "UnitOfMeasurement":   "unit_of_measurement",
    "Multiplicity":        "multiplicity",
    "Weight":              "weight",
    "Volume":              "volume",
    "Width":               "width",
    "Length":              "length",
    "Height":              "height",
    "VAT":                 "vat",
    "WebDescription":      "web_description",
}


def _extract_flat_fields(item: Any) -> dict[str, Any]:
    """SOAP-item → {plain_column: value}. Числовые поля прогоняются
    через _decimal_or_none, текстовые — через strip."""
    out: dict[str, Any] = {}
    for soap_key, flat_key in _SOAP_TO_FLAT.items():
        raw = _get(item, soap_key)
        if flat_key in _NUMERIC_FIELDS:
            out[flat_key] = _decimal_or_none(raw)
        else:
            stripped = _strip_or_empty(raw)
            out[flat_key] = stripped or None
    return out


def _item_to_jsonable(item: Any) -> dict[str, Any]:
    """SOAP CompoundValue → JSON-сериализуемый dict. Достаточно глубокий
    рекурсивный обход: zeep.helpers.serialize_object уже превращает
    CompoundValue в dict с примитивами, но Decimal/date он оставляет —
    конвертируем их в строки, чтобы json.dumps не упал."""
    from zeep.helpers import serialize_object
    if not isinstance(item, (dict, list)):
        item = serialize_object(item, target_cls=dict)

    def _coerce(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: _coerce(vv) for k, vv in v.items()}
        if isinstance(v, list):
            return [_coerce(x) for x in v]
        if isinstance(v, Decimal):
            return str(v)
        if isinstance(v, (bytes, bytearray)):
            # base64Binary (например, Images) — оставляем как строку
            # с пометкой, чтобы JSON был валидным.
            import base64
            return {"__b64__": base64.b64encode(bytes(v)).decode("ascii")}
        if hasattr(v, "isoformat"):  # date / datetime
            try:
                return v.isoformat()
            except Exception:
                return str(v)
        return v

    return _coerce(item)


# Размер chunk'а для batch UPSERT'а. 500 — это компромисс между:
#   * psycopg2-лимитом на число параметров в одном запросе (~32767);
#     при 16 колонках × 500 = 8000 параметров — с запасом;
#   * сетевой latency удалённых БД (Railway prod);
#     1 round-trip вместо 500 на каждые 500 позиций.
# На локальной БД ускорения почти не даёт, на удалённой — ~200x+.
_BATCH_CHUNK_SIZE = 500


def upsert_catalog(
    engine: Engine,
    material_data_response: Any,
    *,
    chunk_size: int = _BATCH_CHUNK_SIZE,
) -> dict[str, int]:
    """Записывает позиции из ответа GetMaterialData в resurs_media_catalog.

    На вход: ЛЮБОЙ из вариантов ответа SOAP-операции GetMaterialData —
      - dict {"MaterialData_Tab": {"Item": [...]}}
      - dict {"MaterialData_Tab": [...]}
      - dict {"MaterialData_Tab": null} (пустой ответ)
      - zeep CompoundValue — распакуется через _items_in_tab.

    Делает UPSERT по material_id, synced_at = NOW(), батчем
    `INSERT ... VALUES (...), (...), ... ON CONFLICT ... DO UPDATE`
    в одной транзакции на chunk_size позиций. Это критично для
    удалённой БД (Railway prod): per-item commit с latency 50-100ms
    на каждом round-trip превращает 25k позиций в 5+ часов работы.

    Возвращает счётчики:
      {inserted, updated, errors}.

    Ошибки. Item без MaterialID отфильтровывается до batch'а
    (errors += 1). Если падает сам batch (например, кривая запись
    внутри chunk'а или duplicate material_id в одном INSERT) —
    fall-back на per-item обработку этого chunk'а: каждая позиция
    в своей транзакции, ошибка одной не блокирует остальные.
    Это сохраняет инвариант «одна кривая позиция не валит всю
    операцию», но платит сетевой latency только за проблемный
    chunk, а не за весь каталог.
    """
    counters = {"inserted": 0, "updated": 0, "errors": 0}

    raw_tab = _get(material_data_response, "MaterialData_Tab")
    items = _items_in_tab(raw_tab)
    if not items:
        return counters

    flat_cols = ("material_id",) + _FLAT_FIELDS
    update_set = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in _FLAT_FIELDS
    )

    # SQL для per-item фолбэка — тот же, что был до chunked-варианта.
    per_item_sql = text(
        "INSERT INTO resurs_media_catalog "
        f"    ({', '.join(flat_cols)}, raw_jsonb, synced_at) "
        f"VALUES ({', '.join(f':{c}' for c in flat_cols)}, "
        "        CAST(:raw_jsonb AS JSONB), NOW()) "
        "ON CONFLICT (material_id) DO UPDATE SET "
        f"    {update_set}, "
        "    raw_jsonb = EXCLUDED.raw_jsonb, "
        "    synced_at = NOW() "
        "RETURNING (xmax = 0) AS inserted"
    )

    def _per_item_fallback(chunk: list[dict[str, Any]]) -> None:
        for p in chunk:
            try:
                with engine.begin() as conn:
                    row = conn.execute(per_item_sql, p).first()
                if row is None:
                    counters["errors"] += 1
                    continue
                if bool(row.inserted):
                    counters["inserted"] += 1
                else:
                    counters["updated"] += 1
            except Exception as exc:
                logger.warning(
                    "resurs_media_catalog: ошибка UPSERT'а material_id=%r — "
                    "(%s: %s)",
                    p.get("material_id") or "<no-id>",
                    type(exc).__name__, exc,
                )
                counters["errors"] += 1

    # Стадия 1. Подготовка параметров. Item'ы без MaterialID
    # отфильтровываем до batch'а — они увеличивают errors и не
    # попадают в INSERT.
    prepared: list[dict[str, Any]] = []
    for item in items:
        material_id = _strip_or_empty(_get(item, "MaterialID"))
        if not material_id:
            logger.warning(
                "resurs_media_catalog: пропускаю item без MaterialID."
            )
            counters["errors"] += 1
            continue
        params: dict[str, Any] = {"material_id": material_id}
        params.update(_extract_flat_fields(item))
        params["raw_jsonb"] = json.dumps(
            _item_to_jsonable(item), ensure_ascii=False,
        )
        prepared.append(params)

    # Стадия 2. Batch UPSERT по chunk_size.
    for start in range(0, len(prepared), chunk_size):
        chunk = prepared[start:start + chunk_size]

        row_clauses: list[str] = []
        batch_params: dict[str, Any] = {}
        for i, p in enumerate(chunk):
            ph = ", ".join(f":{c}_{i}" for c in flat_cols)
            row_clauses.append(
                f"({ph}, CAST(:raw_jsonb_{i} AS JSONB), NOW())"
            )
            for c in flat_cols:
                batch_params[f"{c}_{i}"] = p[c]
            batch_params[f"raw_jsonb_{i}"] = p["raw_jsonb"]

        batch_sql = text(
            "INSERT INTO resurs_media_catalog "
            f"    ({', '.join(flat_cols)}, raw_jsonb, synced_at) "
            f"VALUES {', '.join(row_clauses)} "
            "ON CONFLICT (material_id) DO UPDATE SET "
            f"    {update_set}, "
            "    raw_jsonb = EXCLUDED.raw_jsonb, "
            "    synced_at = NOW() "
            "RETURNING (xmax = 0) AS inserted"
        )

        try:
            with engine.begin() as conn:
                rows = conn.execute(batch_sql, batch_params).all()
            for r in rows:
                if bool(r.inserted):
                    counters["inserted"] += 1
                else:
                    counters["updated"] += 1
        except Exception as exc:
            logger.warning(
                "resurs_media_catalog: batch UPSERT chunk %d..%d упал "
                "(%s: %s) — fall-back на per-item обработку chunk'а.",
                start, start + len(chunk),
                type(exc).__name__, exc,
            )
            _per_item_fallback(chunk)

    logger.info(
        "resurs_media_catalog UPSERT: inserted=%d updated=%d errors=%d "
        "(total items=%d, chunk_size=%d)",
        counters["inserted"], counters["updated"], counters["errors"],
        len(items), chunk_size,
    )
    return counters
