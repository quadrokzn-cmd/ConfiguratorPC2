# Бекапы PostgreSQL на Backblaze B2 с ротацией (этап 9В.2).
#
# Поток:
#   1) pg_dump --format=custom --no-owner --no-acl → bytes;
#   2) загрузка в B2 (S3-совместимый API через boto3, endpoint из B2_ENDPOINT);
#   3) ротация по политике 7-daily / 4-weekly / 6-monthly.
#
# Запуск:
#   - APScheduler в портале (portal/scheduler.py): cron 03:00 МСК, ежедневно;
#   - кнопка «Создать бекап сейчас» в /admin/backups (синхронно в фоне).
#
# Изоляция от конфигуратора: модуль не импортирует из app.* (кроме того,
# что уже подтянет shared/db.py — но мы DATABASE_URL берём напрямую из
# os.environ, чтобы не цепляться за SQLAlchemy-engine).
#
# Формат файла: pg_dump custom внутри уже сжат — дополнительно gzip-ить
# не нужно. Имена объектов в B2:
#   daily/kvadro_tech_<YYYY-MM-DDTHH-MM-SS>.dump
#   weekly/kvadro_tech_<...>.dump   (только воскресеньями)
#   monthly/kvadro_tech_<...>.dump  (только 1-го числа)
#
# Безопасность:
#   - DATABASE_URL содержит пароль БД → mask_db_url() обрезает его перед
#     каждым логированием;
#   - B2_APPLICATION_KEY никогда не попадает в логи;
#   - filename в /admin/backups/download/* проверяется регуляркой,
#     tier — whitelist (логика валидации в роутере, не здесь).

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — Python <3.9 не поддерживается
    ZoneInfo = None  # type: ignore


logger = logging.getLogger(__name__)


# --- Константы политики ротации ----------------------------------------

DAILY_PREFIX = "daily/"
WEEKLY_PREFIX = "weekly/"
MONTHLY_PREFIX = "monthly/"

KEEP_DAILY = 7
KEEP_WEEKLY = 4
KEEP_MONTHLY = 6

_FILENAME_RE = re.compile(r"^kvadro_tech_[\d\-T]+\.dump$")
_VALID_TIERS = (DAILY_PREFIX.rstrip("/"), WEEKLY_PREFIX.rstrip("/"), MONTHLY_PREFIX.rstrip("/"))

_MSK = ZoneInfo("Europe/Moscow") if ZoneInfo else None
_PG_DUMP_TIMEOUT_SEC = 600  # 10 минут — БД ~30 МБ, реальный дамп секунды


# --- Маскирование секретов ---------------------------------------------

_DB_URL_RE = re.compile(
    r"(?P<scheme>postgres(?:ql)?)://(?P<user>[^:]+):(?P<password>[^@]+)@"
)


def mask_db_url(url: str) -> str:
    """Скрывает пароль в connection string PostgreSQL.

    'postgresql://user:secretpw@host:5432/db' → 'postgresql://user:****@host:5432/db'.
    Если URL не содержит password-сегмент — возвращается без изменений.
    """
    if not url:
        return url
    return _DB_URL_RE.sub(r"\g<scheme>://\g<user>:****@", url)


def _scrub_password(text_blob: str, password: str | None) -> str:
    """Дополнительная подстраховка: если в тексте (например, в stderr
    pg_dump) встречается «голый» пароль из URL — заменяет на ****."""
    if not password or not text_blob:
        return text_blob
    return text_blob.replace(password, "****")


def _extract_password(url: str) -> str | None:
    m = _DB_URL_RE.search(url or "")
    return m.group("password") if m else None


# --- pg_dump -----------------------------------------------------------

# Резервный путь для локальной Windows-разработки. На Railway pg_dump
# приедет через apt в Dockerfile.portal, и shutil.which его найдёт.
_WINDOWS_PG_DUMP_FALLBACK = r"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe"


def _resolve_pg_dump_binary() -> str:
    """Находит pg_dump: сначала через PATH, потом фоллбек на Windows-путь."""
    found = shutil.which("pg_dump")
    if found:
        return found
    if os.name == "nt" and Path(_WINDOWS_PG_DUMP_FALLBACK).exists():
        return _WINDOWS_PG_DUMP_FALLBACK
    raise RuntimeError(
        "pg_dump не найден ни в PATH, ни по дефолтному Windows-пути "
        f"({_WINDOWS_PG_DUMP_FALLBACK}). "
        "На Railway он ставится из postgresql-client-18 (см. Dockerfile.portal); "
        "локально установите PostgreSQL 18 (мажор клиента должен совпадать с сервером)."
    )


def make_pg_dump(database_url: str) -> bytes:
    """Запускает pg_dump в формате custom и возвращает stdout как bytes.

    Формат custom (-Fc) — бинарный, со встроенным сжатием. Дополнительно
    gzip-ить не нужно. Восстановление: pg_restore --clean --if-exists
    --no-owner --no-acl --dbname=<url> <file.dump>.
    """
    if not database_url:
        raise RuntimeError("DATABASE_URL пустой — нечего бекапить.")

    binary = _resolve_pg_dump_binary()
    cmd = [
        binary,
        "--format=custom",
        "--no-owner",
        "--no-acl",
        database_url,
    ]

    # Логируем НЕ полный URL, а маскированный.
    logger.info("backup: pg_dump start (%s)", mask_db_url(database_url))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=_PG_DUMP_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"pg_dump превысил таймаут {_PG_DUMP_TIMEOUT_SEC} c"
        ) from exc

    if proc.returncode != 0:
        password = _extract_password(database_url)
        stderr_text = proc.stderr.decode("utf-8", errors="replace")
        stderr_safe = _scrub_password(mask_db_url(stderr_text), password)
        raise RuntimeError(
            f"pg_dump упал (rc={proc.returncode}): {stderr_safe.strip()[:1000]}"
        )

    size = len(proc.stdout)
    logger.info("backup: pg_dump ok, dump size=%d bytes", size)
    return proc.stdout


# --- Boto3 / B2 -------------------------------------------------------

@dataclass
class _B2Config:
    endpoint: str
    bucket: str
    key_id: str
    application_key: str


def _read_b2_config() -> _B2Config:
    """Читает конфиг B2 из переменных окружения. Без них модуль работать
    не может — поднимаем понятную ошибку, чтобы не падать где-то глубоко
    в boto3."""
    endpoint = os.environ.get("B2_ENDPOINT", "").strip()
    bucket = os.environ.get("B2_BUCKET", "").strip()
    key_id = os.environ.get("B2_KEY_ID", "").strip()
    app_key = os.environ.get("B2_APPLICATION_KEY", "").strip()
    missing = [n for n, v in (
        ("B2_ENDPOINT", endpoint),
        ("B2_BUCKET", bucket),
        ("B2_KEY_ID", key_id),
        ("B2_APPLICATION_KEY", app_key),
    ) if not v]
    if missing:
        raise RuntimeError(
            "Не заданы переменные окружения для Backblaze B2: "
            + ", ".join(missing)
            + ". См. docs/deployment.md (этап 9В.2)."
        )
    return _B2Config(endpoint=endpoint, bucket=bucket, key_id=key_id, application_key=app_key)


def _make_b2_client(config: _B2Config | None = None):
    """Создаёт boto3 S3-клиент, настроенный на B2-эндпоинт."""
    import boto3  # импорт здесь, чтобы тесты, не использующие B2, не тянули его на старте
    cfg = config or _read_b2_config()
    return boto3.client(
        "s3",
        endpoint_url=cfg.endpoint,
        aws_access_key_id=cfg.key_id,
        aws_secret_access_key=cfg.application_key,
    ), cfg


# --- Высокоуровневые операции -----------------------------------------

def upload_to_b2(data: bytes, key: str) -> dict:
    """Загружает байты в B2 под именем `key`. Возвращает {size_bytes, etag}.

    Метаданные:
      - created-at-utc: ISO-таймстемп (для аудита, независимо от имени файла);
      - source: 'kvadro-tech-portal'.
    """
    client, cfg = _make_b2_client()
    iso_ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = client.put_object(
        Bucket=cfg.bucket,
        Key=key,
        Body=data,
        Metadata={
            "created-at-utc": iso_ts,
            "source": "kvadro-tech-portal",
        },
    )
    etag = (response or {}).get("ETag", "").strip('"')
    logger.info("backup: uploaded key=%s size=%d etag=%s", key, len(data), etag)
    return {"size_bytes": len(data), "etag": etag}


def list_backups() -> list[dict]:
    """Возвращает все объекты бакета как [{key, size_bytes, last_modified, tier}, ...].

    tier ∈ {'daily', 'weekly', 'monthly', 'other'}. Сортировка от новых
    к старым по last_modified.
    """
    client, cfg = _make_b2_client()
    paginator = client.get_paginator("list_objects_v2")
    items: list[dict] = []
    for page in paginator.paginate(Bucket=cfg.bucket):
        for obj in page.get("Contents", []) or []:
            key = obj["Key"]
            tier = _tier_of(key)
            items.append({
                "key": key,
                "size_bytes": int(obj.get("Size", 0)),
                "last_modified": obj.get("LastModified"),
                "tier": tier,
            })
    items.sort(key=lambda r: r["last_modified"] or datetime.min.replace(tzinfo=timezone.utc),
               reverse=True)
    return items


def delete_backup(key: str) -> None:
    """Удаляет объект из B2 по ключу. Идемпотентно — несуществующий
    ключ B2 удаляет молча."""
    client, cfg = _make_b2_client()
    client.delete_object(Bucket=cfg.bucket, Key=key)
    logger.info("backup: deleted key=%s", key)


def _tier_of(key: str) -> str:
    if key.startswith(DAILY_PREFIX):
        return "daily"
    if key.startswith(WEEKLY_PREFIX):
        return "weekly"
    if key.startswith(MONTHLY_PREFIX):
        return "monthly"
    return "other"


def _filter_tier(items: Iterable[dict], tier: str) -> list[dict]:
    return [it for it in items if it["tier"] == tier]


def rotate_backups() -> dict[str, list[str]]:
    """Применяет политику ротации:
      daily/   — оставить последние KEEP_DAILY=7
      weekly/  — оставить последние KEEP_WEEKLY=4
      monthly/ — оставить последние KEEP_MONTHLY=6

    Файлы вне трёх префиксов (tier=other) не трогаем — они могут быть
    оставлены вручную и не должны пострадать от автоматики.

    Возвращает {'deleted': [...keys...], 'kept': [...keys...]}.
    """
    items = list_backups()
    deleted: list[str] = []
    kept: list[str] = []

    plans = [
        ("daily", KEEP_DAILY),
        ("weekly", KEEP_WEEKLY),
        ("monthly", KEEP_MONTHLY),
    ]
    for tier, keep_n in plans:
        tier_items = _filter_tier(items, tier)
        # items уже отсортированы по убыванию last_modified — первые keep_n остаются.
        kept.extend(it["key"] for it in tier_items[:keep_n])
        for it in tier_items[keep_n:]:
            try:
                delete_backup(it["key"])
                deleted.append(it["key"])
            except Exception as exc:
                logger.warning(
                    "backup: rotate — не удалось удалить %s: %s",
                    it["key"], exc,
                )

    logger.info(
        "backup: rotate done, deleted=%d kept=%d",
        len(deleted), len(kept),
    )
    return {"deleted": deleted, "kept": kept}


# --- Главная функция: perform_backup ----------------------------------

def _now_msk() -> datetime:
    """Текущее время в МСК. ZoneInfo приходит с Python-tzdata.

    На Windows tzdata часто не установлена; для tests мы можем
    monkeypatch'ить эту функцию, поэтому отдельно её выделяем."""
    if _MSK is None:
        # На системах без zoneinfo даём UTC+3 как приближение МСК.
        from datetime import timedelta
        return datetime.now(tz=timezone(timedelta(hours=3)))
    return datetime.now(tz=_MSK)


def perform_backup() -> dict:
    """Главная функция: pg_dump → upload в нужные tier'ы → rotate.

    Возвращает {'size_bytes': int, 'tiers': [...], 'duration_sec': float}.

    При ошибке — логирует ERROR со stack trace и пробрасывает дальше
    (Sentry в 9В.3 подхватит).
    """
    started = datetime.now(tz=timezone.utc)
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL не задан — backup невозможен.")

    try:
        dump_bytes = make_pg_dump(database_url)

        now_msk = _now_msk()
        ts_str = now_msk.strftime("%Y-%m-%dT%H-%M-%S")
        filename = f"kvadro_tech_{ts_str}.dump"

        tiers: list[str] = []

        # Ежедневный бекап — всегда.
        upload_to_b2(dump_bytes, f"{DAILY_PREFIX}{filename}")
        tiers.append("daily")

        # Еженедельный — по воскресеньям (weekday() == 6).
        if now_msk.weekday() == 6:
            upload_to_b2(dump_bytes, f"{WEEKLY_PREFIX}{filename}")
            tiers.append("weekly")

        # Ежемесячный — 1-го числа.
        if now_msk.day == 1:
            upload_to_b2(dump_bytes, f"{MONTHLY_PREFIX}{filename}")
            tiers.append("monthly")

        rotate_backups()

        duration = (datetime.now(tz=timezone.utc) - started).total_seconds()
        logger.info(
            "backup: perform_backup ok — size=%d bytes, tiers=%s, duration=%.2fs",
            len(dump_bytes), ",".join(tiers), duration,
        )
        return {
            "size_bytes": len(dump_bytes),
            "tiers": tiers,
            "duration_sec": round(duration, 2),
            "filename": filename,
        }
    except Exception:
        logger.exception("backup: perform_backup упал")
        raise


# --- Валидация имён для безопасного скачивания --------------------------

def is_valid_backup_filename(filename: str) -> bool:
    """Проверяет имя файла бекапа на безопасный формат.

    kvadro_tech_2026-04-28T03-00-00.dump → True
    ../etc/passwd                        → False
    """
    return bool(_FILENAME_RE.fullmatch(filename or ""))


def is_valid_tier(tier: str) -> bool:
    """tier ∈ {'daily','weekly','monthly'} (без слеша)."""
    return tier in _VALID_TIERS
