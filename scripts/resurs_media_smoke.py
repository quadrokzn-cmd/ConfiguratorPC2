# Resurs Media SOAP smoke по чек-листу программиста (Волков С.).
#
# Одноразовый прогон, артефакт под .gitignore. Не импортируется в проде.
#
# Делает 7 SOAP-вызовов с паузой ≥ 65 сек между каждым:
#   1. Notification(FromDate)                                    — уведомления
#   2. GetMaterialData (без фильтров)                            — описание, весь каталог
#   3. GetPrices (без фильтра групп, GetAvailableCount=True)     — цены+остатки, весь каталог
#   4a. GetMaterialData (MaterialGroup_Tab=10 групп)             — описание, 10 групп
#   4b. GetPrices (MaterialGroup_Tab=10 групп, +stock)           — цены+остатки, 10 групп
#   5. GetPrices (без фильтра групп, GetAvailableCount=False)    — только цены
#   6. GetItemsAvail (без фильтров)                              — только остатки
#
# На rate-limit (Result=3): спим recommended+5 сек, делаем ОДИН retry. Если
# опять Result=3 — пункт помечаем 'deferred', идём дальше.
#
# Маскировка: zeep-loggers и urllib3 переведены на WARNING (иначе они печатают
# SOAP-envelope с заголовком Basic auth). Креды читаются из env, в логе не
# появляются. Attachment из Notification (base64Binary) сохраняется отдельным
# файлом в logs/resurs_media_notification_<ts>_<name> — в основной лог байты
# не попадают.
#
# Запуск:
#   python scripts/resurs_media_smoke.py
#
# Параметры:
#   --dry-run      — не вызывать SOAP, только показать план;
#   --from-date    — дата для Notification.FromDate (по умолчанию today-30d);
#   --allow-prod   — разрешить prod-URL (без подстроки 'test' в WSDL).
#                    Без флага — отказывает (SystemExit(2)). С флагом —
#                    печатает WARNING и спрашивает YES в stdin.

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# .env подхватываем тем же путём, что и portal/main.py (python-dotenv).
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import requests
from requests.auth import HTTPBasicAuth
import zeep
from zeep.cache import InMemoryCache
from zeep.helpers import serialize_object
from zeep.transports import Transport


MSK = ZoneInfo("Europe/Moscow")

# Пауза между ЛЮБЫМИ SOAP-вызовами. 60 — минимум по чек-листу Сергея,
# +5 запас на джиттер.
PAUSE_SECONDS = 65

# Дополнительная пауза при retry на Result=3, поверх рекомендованного
# интервала из ErrorMessage.
RATE_LIMIT_RETRY_PADDING = 5

# Москва — единственный склад на test-стенде (см.
# scripts/_diag_resurs_media_warehouses.json).
WAREHOUSE_ID = "00011"

# 10 групп для пункта 4. Берём ровно те же, что fetcher использует для prod-
# загрузки (8 наших категорий, 10 групп — см. _CATEGORY_GROUP_MAP в
# portal/services/configurator/auto_price/fetchers/resurs_media.py:72).
TEN_GROUPS: list[str] = [
    "Z999-919999",  # psu (БП корпусов)
    "Z999-9992",    # psu (серверные/прочие)
    "Z999-999979",  # cooler
    "Z999-10001",   # gpu
    "Z383",         # storage (внутр. HDD)
    "Z897",         # storage (SSD)
    "Z373",         # storage (флеш)
    "Z999-10006",   # motherboard
    "Z431",         # ram
    "Z999-911999",  # case
]


# ---------------------------------------------------------------------------
# Логирование
# ---------------------------------------------------------------------------

def setup_logging() -> tuple[logging.Logger, Path]:
    ts = datetime.now(MSK).strftime("%Y%m%d-%H%M%S")
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / f"resurs_media_smoke_{ts}.log"

    logger = logging.getLogger("rm_smoke")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    # На Windows stdout по умолчанию cp1251 — кириллица в консоли превращается
    # в кракозябры. Принудительно переключаем на utf-8 (Python 3.7+).
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    # zeep/urllib3/requests при DEBUG/INFO печатают SOAP-envelope с заголовком
    # Basic auth — глушим до WARNING, иначе креды утекут в файл лога.
    for name in ("zeep", "zeep.transports", "zeep.wsdl",
                 "urllib3", "urllib3.connectionpool", "requests"):
        logging.getLogger(name).setLevel(logging.WARNING)

    return logger, log_path


def now_msk() -> str:
    return datetime.now(MSK).strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Клиент
# ---------------------------------------------------------------------------

def make_client(*, allow_prod: bool = False) -> zeep.Client:
    wsdl = (os.environ.get("RESURS_MEDIA_WSDL_URL") or "").strip()
    username = (os.environ.get("RESURS_MEDIA_USERNAME") or "").strip()
    password = (os.environ.get("RESURS_MEDIA_PASSWORD") or "").strip()
    if not (wsdl and username and password):
        raise RuntimeError(
            "Не заданы переменные окружения. Нужны: "
            "RESURS_MEDIA_WSDL_URL, RESURS_MEDIA_USERNAME, "
            "RESURS_MEDIA_PASSWORD."
        )
    # Двойная защита от случайного выстрела по prod: см.
    # scripts/_resurs_media_safety.py.
    from scripts._resurs_media_safety import check_prod_safety
    check_prod_safety(wsdl, allow_prod)

    session = requests.Session()
    session.auth = HTTPBasicAuth(username, password)
    transport = Transport(
        session=session,
        cache=InMemoryCache(),
        timeout=30,
        operation_timeout=120,
    )
    return zeep.Client(wsdl=wsdl, transport=transport)


# ---------------------------------------------------------------------------
# Вспомогательные парсеры
# ---------------------------------------------------------------------------

_RATE_LIMIT_RE = re.compile(r"(\d+)\s*сек", re.IGNORECASE)


def _parse_rate_limit_seconds(error_message: Any) -> int:
    if not error_message:
        return PAUSE_SECONDS
    m = _RATE_LIMIT_RE.search(str(error_message))
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return PAUSE_SECONDS
    return PAUSE_SECONDS


def _resp_to_dict(resp: Any) -> dict:
    if resp is None:
        return {}
    try:
        d = serialize_object(resp, target_cls=dict)
        return d if isinstance(d, dict) else {"value": d}
    except Exception:
        return {"__repr__": repr(resp)[:200]}


def _approx_size_bytes(d: Any) -> int:
    try:
        return len(json.dumps(d, ensure_ascii=False, default=str))
    except Exception:
        return -1


def _extract_items(d: dict, *tab_keys: str) -> list[Any]:
    for key in tab_keys:
        tab = d.get(key)
        if tab is None:
            continue
        if isinstance(tab, list):
            return tab
        if isinstance(tab, dict):
            inner = tab.get("Item") or tab.get("Material") or tab.get("MaterialData")
            if isinstance(inner, list):
                return inner
            if inner is not None:
                return [inner]
    return []


def _safe_first_n(items: list[Any], n: int, keys: tuple[str, ...]) -> list[dict]:
    """Сэмпл из первых n элементов, только перечисленные ключи."""
    out: list[dict] = []
    for it in items[:n]:
        if isinstance(it, dict):
            sample = {k: it.get(k) for k in keys if k in it}
        else:
            sample = {k: getattr(it, k, None) for k in keys}
        for k, v in list(sample.items()):
            if isinstance(v, (bytes, bytearray)):
                sample[k] = f"<bytes:{len(v)}>"
            elif isinstance(v, str) and len(v) > 80:
                sample[k] = v[:77] + "..."
        out.append(sample)
    return out


def _summarize(op_name: str, d: dict) -> tuple[int, list[dict]]:
    """Возвращает (count, sample) по типу операции."""
    if op_name == "Notification":
        items = _extract_items(d, "Notification")
        sample = _safe_first_n(
            items, 3, ("NotificationID", "Text", "AttachmentName"),
        )
        for it in sample:
            if "Text" in it and isinstance(it["Text"], str):
                it["Text"] = it["Text"][:120] + ("..." if len(it["Text"]) > 120 else "")
        return len(items), sample
    if op_name == "GetMaterialData":
        items = _extract_items(d, "MaterialData_Tab", "Material_Tab")
        sample = _safe_first_n(
            items, 3,
            ("MaterialID", "Vendor", "VendorPart", "MaterialText", "MaterialGroup"),
        )
        return len(items), sample
    if op_name == "GetPrices":
        items = _extract_items(d, "Material_Tab")
        sample = _safe_first_n(
            items, 3,
            ("MaterialID", "Price", "PriceUSD", "AvailableCount", "PartNum"),
        )
        return len(items), sample
    if op_name == "GetItemsAvail":
        items = _extract_items(d, "Material_Tab")
        sample = _safe_first_n(
            items, 3, ("MaterialID", "AvailableCount", "PartNum"),
        )
        return len(items), sample
    return 0, []


def _save_attachments(logger: logging.Logger, items: list[Any], started_ts: str) -> list[str]:
    """Notification.Attachment приходит как base64Binary (zeep декодирует в bytes).
    Если есть — сохраним каждое в logs/resurs_media_notification_<ts>_<name>.
    Возвращает список путей."""
    saved: list[str] = []
    log_dir = ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    for idx, it in enumerate(items, start=1):
        if isinstance(it, dict):
            att = it.get("Attachment")
            att_name = it.get("AttachmentName") or f"attachment_{idx}.bin"
        else:
            att = getattr(it, "Attachment", None)
            att_name = getattr(it, "AttachmentName", None) or f"attachment_{idx}.bin"
        if att is None:
            continue
        if isinstance(att, str):
            try:
                import base64
                att = base64.b64decode(att)
            except Exception:
                logger.warning("Notification[%d]: Attachment base64-декодирование не удалось", idx)
                continue
        if not isinstance(att, (bytes, bytearray)):
            continue
        # Санитизация имени: только латиница/цифры/._- ; кириллицу заменяем _
        safe_name = re.sub(r"[^A-Za-z0-9._\-]+", "_", str(att_name))[:120] or f"attachment_{idx}.bin"
        path = log_dir / f"resurs_media_notification_{started_ts}_{idx:02d}_{safe_name}"
        path.write_bytes(bytes(att))
        saved.append(str(path.relative_to(ROOT)))
        logger.info("Notification[%d]: вложение сохранено → %s (%d байт)",
                    idx, saved[-1], len(att))
    return saved


# ---------------------------------------------------------------------------
# Один вызов SOAP с retry на rate-limit
# ---------------------------------------------------------------------------

def call_with_retry(
    logger: logging.Logger,
    client: zeep.Client,
    *,
    item_num: str,
    op_name: str,
    op_label: str,
    kwargs: dict,
    started_ts: str,
) -> dict:
    """Один пункт чек-листа: вызывает SOAP, ретраит на Result=3 один раз.

    Возвращает report-dict:
      status:   'ok' | 'deferred' | 'error'
      time_msk: HH:MM:SS МСК (время старта)
      elapsed:  секунды
      result:   значение Result из ответа (или None при exception)
      count:    число позиций в *_Tab ответа
      bytes:    приблизительный размер ответа (json.dumps)
      sample:   первые 3 элемента, безопасные поля
      error:    текст ошибки (если есть)
      attachments: list[str] (только для Notification)
    """
    started_msk = now_msk()
    logger.info(
        "================= ПУНКТ %s — %s =================",
        item_num, op_label,
    )
    logger.info("Пункт %s [%s]: START в %s МСК, op=%s, params=%s",
                item_num, op_label, started_msk, op_name, _kwargs_repr(kwargs))

    for attempt in range(1, 3):
        t0 = time.monotonic()
        try:
            op = getattr(client.service, op_name)
            resp = op(**kwargs)
        except zeep.exceptions.Fault as f:
            elapsed = time.monotonic() - t0
            logger.error("Пункт %s [%s]: SOAP Fault attempt=%d: %s (%.2fs)",
                         item_num, op_label, attempt, f.message, elapsed)
            return {
                "status": "error", "time_msk": started_msk, "elapsed": elapsed,
                "result": None, "count": 0, "bytes": 0, "sample": [],
                "error": f"SOAP Fault: {f.message}", "attachments": [],
            }
        except Exception as e:
            elapsed = time.monotonic() - t0
            logger.error("Пункт %s [%s]: ИСКЛЮЧЕНИЕ %s attempt=%d (%.2fs)",
                         item_num, op_label, type(e).__name__, attempt, elapsed)
            return {
                "status": "error", "time_msk": started_msk, "elapsed": elapsed,
                "result": None, "count": 0, "bytes": 0, "sample": [],
                "error": f"{type(e).__name__}: {str(e)[:200]}", "attachments": [],
            }
        elapsed = time.monotonic() - t0
        d = _resp_to_dict(resp)
        result = d.get("Result")
        error_msg = d.get("ErrorMessage") or ""
        size_bytes = _approx_size_bytes(d)

        if result == 3:
            wait = _parse_rate_limit_seconds(error_msg)
            logger.warning(
                "Пункт %s [%s]: RATE-LIMIT Result=3 attempt=%d, "
                "ErrorMessage=%r, рекомендованный интервал=%d сек, жду %d сек",
                item_num, op_label, attempt, error_msg, wait,
                wait + RATE_LIMIT_RETRY_PADDING,
            )
            if attempt == 2:
                logger.warning(
                    "Пункт %s [%s]: после 2 попыток rate-limit не отпустил — "
                    "помечаем 'deferred', идём дальше",
                    item_num, op_label,
                )
                return {
                    "status": "deferred", "time_msk": started_msk, "elapsed": elapsed,
                    "result": 3, "count": 0, "bytes": size_bytes, "sample": [],
                    "error": f"Result=3 (rate-limit, deferred): {error_msg}",
                    "attachments": [],
                }
            time.sleep(wait + RATE_LIMIT_RETRY_PADDING)
            continue

        # Любой не-3 Result — считаем «вызов состоялся»; ниже разбор по типу.
        count, sample = _summarize(op_name, d)
        attachments: list[str] = []
        if op_name == "Notification":
            items = _extract_items(d, "Notification")
            attachments = _save_attachments(logger, items, started_ts)

        status = "ok"
        if result not in (0, None):
            status = "error" if result == 1 else "ok"
            # Result=4 (заказы отключены) для нас «ok» — для smoke это норма.

        logger.info(
            "Пункт %s [%s]: Result=%s, count=%d, ~%d байт, elapsed=%.2fs",
            item_num, op_label, result, count, size_bytes, elapsed,
        )
        if error_msg:
            logger.info("Пункт %s [%s]: ErrorMessage=%r", item_num, op_label, error_msg)
        for i, s in enumerate(sample, start=1):
            logger.info("  sample[%d]: %s", i, json.dumps(s, ensure_ascii=False, default=str))

        return {
            "status": status, "time_msk": started_msk, "elapsed": elapsed,
            "result": result, "count": count, "bytes": size_bytes, "sample": sample,
            "error": error_msg if error_msg else None,
            "attachments": attachments,
        }
    return {  # недостижимо
        "status": "error", "time_msk": started_msk, "elapsed": 0,
        "result": None, "count": 0, "bytes": 0, "sample": [],
        "error": "unreachable", "attachments": [],
    }


def _kwargs_repr(kwargs: dict) -> str:
    """Безопасный repr параметров вызова (без длинных списков)."""
    parts = []
    for k, v in kwargs.items():
        if isinstance(v, dict) and "Item" in v and isinstance(v["Item"], list):
            parts.append(f"{k}={{'Item': [{len(v['Item'])} элементов]}}")
        elif isinstance(v, list):
            parts.append(f"{k}=[{len(v)} элементов]")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Главный сценарий
# ---------------------------------------------------------------------------

def build_plan(from_date: date) -> list[dict]:
    """Возвращает 7 пунктов чек-листа (4-й — 2 вызова, поэтому пунктов в
    плане 7: 1, 2, 3, 4a, 4b, 5, 6)."""
    group_tab = {"Item": [{"MaterialGroup": g} for g in TEN_GROUPS]}
    return [
        {
            "num": "1", "op_name": "Notification",
            "label": "Уведомления",
            "kwargs": {"FromDate": from_date},
        },
        {
            "num": "2", "op_name": "GetMaterialData",
            "label": "Описание номенклатур (весь каталог)",
            "kwargs": {
                "MaterialID_Tab": None,
                "MaterialGroup_Tab": None,
                "VendorPart_Tab": None,
                "WithCharacteristics": False,
                "WithBarCodes": False,
                "WithCertificates": False,
                "WithImages": False,
            },
        },
        {
            "num": "3", "op_name": "GetPrices",
            "label": "Цены+остатки (весь каталог)",
            "kwargs": {
                "WareHouseID": WAREHOUSE_ID,
                "MaterialID_Tab": None,
                "MaterialGroup_Tab": None,
                "GetAvailableCount": True,
            },
        },
        {
            "num": "4a", "op_name": "GetMaterialData",
            "label": "Описание номенклатур (10 групп)",
            "kwargs": {
                "MaterialID_Tab": None,
                "MaterialGroup_Tab": group_tab,
                "VendorPart_Tab": None,
                "WithCharacteristics": False,
                "WithBarCodes": False,
                "WithCertificates": False,
                "WithImages": False,
            },
        },
        {
            "num": "4b", "op_name": "GetPrices",
            "label": "Цены+остатки (10 групп)",
            "kwargs": {
                "WareHouseID": WAREHOUSE_ID,
                "MaterialID_Tab": None,
                "MaterialGroup_Tab": group_tab,
                "GetAvailableCount": True,
            },
        },
        {
            "num": "5", "op_name": "GetPrices",
            "label": "Только цены (весь каталог)",
            "kwargs": {
                "WareHouseID": WAREHOUSE_ID,
                "MaterialID_Tab": None,
                "MaterialGroup_Tab": None,
                "GetAvailableCount": False,
            },
        },
        {
            "num": "6", "op_name": "GetItemsAvail",
            "label": "Только остатки (весь каталог)",
            "kwargs": {
                "WareHouseID": WAREHOUSE_ID,
                "MaterialID_Tab": None,
                "MaterialGroup_Tab": None,
            },
        },
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Resurs Media SOAP smoke")
    parser.add_argument("--dry-run", action="store_true",
                        help="Показать план без SOAP-вызовов")
    parser.add_argument("--from-date", type=str, default=None,
                        help="Notification.FromDate в формате YYYY-MM-DD (default: today-30d)")
    parser.add_argument("--allow-prod", action="store_true",
                        help="Разрешить запуск против prod-URL (в WSDL нет 'test'). "
                             "Дополнительно запросит подтверждение YES в stdin.")
    args = parser.parse_args()

    logger, log_path = setup_logging()

    started_ts = datetime.now(MSK).strftime("%Y%m%d-%H%M%S")
    started_iso = datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S МСК")

    if args.from_date:
        from_date = date.fromisoformat(args.from_date)
    else:
        from_date = date.today() - timedelta(days=30)

    plan = build_plan(from_date)
    logger.info("=== Resurs Media SOAP smoke ===")
    logger.info("Старт: %s; лог: %s", started_iso, log_path.relative_to(ROOT))
    logger.info("Notification.FromDate = %s", from_date.isoformat())
    logger.info("Запланировано пунктов: %d, пауза между вызовами: %d сек",
                len(plan), PAUSE_SECONDS)
    for p in plan:
        logger.info("  • %s: %s (%s)", p["num"], p["label"], p["op_name"])

    if args.dry_run:
        logger.info("--dry-run: SOAP не вызываем, выход.")
        return 0

    client = make_client(allow_prod=args.allow_prod)
    logger.info("zeep.Client поднят, WSDL загружен.")

    reports: list[dict] = []
    interval_start = now_msk()

    for idx, item in enumerate(plan):
        if idx > 0:
            logger.info("Пауза %d сек перед пунктом %s ...",
                        PAUSE_SECONDS, item["num"])
            time.sleep(PAUSE_SECONDS)
        r = call_with_retry(
            logger, client,
            item_num=item["num"], op_name=item["op_name"],
            op_label=item["label"], kwargs=item["kwargs"],
            started_ts=started_ts,
        )
        reports.append({"plan": item, "report": r})

    interval_end = now_msk()
    finished_iso = datetime.now(MSK).strftime("%Y-%m-%d %H:%M:%S МСК")

    logger.info("=== ИТОГ ===")
    logger.info("Интервал прогона МСК: %s — %s", interval_start, interval_end)
    for entry in reports:
        p, r = entry["plan"], entry["report"]
        logger.info(
            "  Пункт %s [%s]: status=%s, Result=%s, count=%d, ~%d байт, %.2fs",
            p["num"], p["label"], r["status"], r["result"],
            r["count"], r["bytes"], r["elapsed"],
        )

    # Сохраняем сырой машинный отчёт рядом с логом, для построения markdown.
    json_path = ROOT / "scripts" / f"resurs_media_smoke_report_{started_ts}.json"
    safe_reports = [
        {
            "num":     entry["plan"]["num"],
            "label":   entry["plan"]["label"],
            "op_name": entry["plan"]["op_name"],
            "params":  _kwargs_repr(entry["plan"]["kwargs"]),
            **entry["report"],
        }
        for entry in reports
    ]
    json_path.write_text(
        json.dumps({
            "started":  started_iso,
            "finished": finished_iso,
            "interval_msk": f"{interval_start} — {interval_end}",
            "from_date": from_date.isoformat(),
            "items": safe_reports,
        }, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    logger.info("JSON-отчёт: %s", json_path.relative_to(ROOT))
    logger.info("Лог:        %s", log_path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
