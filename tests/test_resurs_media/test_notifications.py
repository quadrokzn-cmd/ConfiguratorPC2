# Тесты сервиса fetch_and_store_notifications (мини-этап 2026-05-12).
#
# Не дёргают реальный SOAP — все вызовы Resurs Media SOAP-API мокаются
# через подмену fetcher'а собственным stub'ом с заранее заготовленным
# ответом. Тестовая БД (фикстура db_engine из tests/conftest.py)
# применяет миграцию 0036_resurs_media_notifications.sql.

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from portal.services.configurator.auto_price.resurs_media_notifications import (
    _safe_filename,
    fetch_and_store_notifications,
)


# --- Stub fetcher --------------------------------------------------------

class _FetcherStub:
    """Подмена ResursMediaApiFetcher для тестов: не создаёт zeep-клиент,
    не ходит в SOAP — возвращает заготовленный ответ или бросает
    заготовленное исключение."""

    def __init__(self, response: Any = None, exception: Exception | None = None):
        self._response = response
        self._exception = exception
        self.calls: list[dict[str, Any]] = []

    def call_notification(self, from_date=None):
        self.calls.append({"from_date": from_date})
        if self._exception is not None:
            raise self._exception
        return self._response


# --- Helpers -------------------------------------------------------------

def _count_rows(db_engine) -> int:
    with db_engine.begin() as conn:
        return int(conn.execute(
            text("SELECT COUNT(*) FROM resurs_media_notifications")
        ).scalar() or 0)


def _select_all(db_engine) -> list[dict[str, Any]]:
    with db_engine.begin() as conn:
        rows = conn.execute(text(
            "SELECT notification_id, text, attachment_name, attachment_path "
            "FROM resurs_media_notifications ORDER BY id"
        )).all()
    return [dict(r._mapping) for r in rows]


# --- Tests ---------------------------------------------------------------

def test_single_notification_no_attachment(db_engine, tmp_path):
    """1. Один Item без вложения → 1 INSERT, 0 файлов, errors=0."""
    fetcher = _FetcherStub(response={
        "Notification_Tab": [
            {
                "NotificationID": "notif-001",
                "Text": "Обновление API запланировано на 2026-06-01.",
                "Attachment": None,
                "AttachmentName": None,
            }
        ],
        "Result": 0,
    })

    result = fetch_and_store_notifications(
        fetcher=fetcher,
        engine=db_engine,
        storage_dir=tmp_path,
    )

    assert result == {
        "notifications_seen": 1,
        "new_notifications":  1,
        "attachments_saved":  0,
        "errors":             0,
    }
    assert fetcher.calls == [{"from_date": None}]

    rows = _select_all(db_engine)
    assert len(rows) == 1
    assert rows[0]["notification_id"] == "notif-001"
    assert rows[0]["text"] == "Обновление API запланировано на 2026-06-01."
    assert rows[0]["attachment_name"] is None
    assert rows[0]["attachment_path"] is None

    # tmp_path не должен содержать файлов.
    assert list(tmp_path.iterdir()) == []


def test_notification_with_attachment(db_engine, tmp_path):
    """2. Item с вложением → 1 INSERT + 1 файл."""
    payload = b"Hello, World! This is an attachment."
    fetcher = _FetcherStub(response={
        # spec допускает имя поля 'Notification' (без _Tab) — проверяем,
        # что сервис распознаёт оба варианта.
        "Notification": [
            {
                "NotificationID": "notif-att-002",
                "Text": "Прикреплён файл с инструкцией.",
                "Attachment": payload,
                "AttachmentName": "instruction.txt",
            }
        ],
        "Result": 0,
    })

    result = fetch_and_store_notifications(
        fetcher=fetcher,
        engine=db_engine,
        storage_dir=tmp_path,
    )

    assert result["notifications_seen"] == 1
    assert result["new_notifications"] == 1
    assert result["attachments_saved"] == 1
    assert result["errors"] == 0

    saved = list(tmp_path.iterdir())
    assert len(saved) == 1
    assert saved[0].read_bytes() == payload
    assert saved[0].name == "notif-att-002_instruction.txt"

    rows = _select_all(db_engine)
    assert rows[0]["attachment_name"] == "instruction.txt"
    assert rows[0]["attachment_path"] == "notif-att-002_instruction.txt"


def test_dedup_on_repeat(db_engine, tmp_path):
    """3. Повторный вызов с тем же NotificationID → 0 новых, файл не
    перезаписывается (mtime/contents совпадают), ON CONFLICT DO NOTHING."""
    payload = b"fixed content"
    response = {
        "Notification_Tab": [
            {
                "NotificationID": "notif-dup-003",
                "Text": "повтор",
                "Attachment": payload,
                "AttachmentName": "doc.pdf",
            }
        ],
        "Result": 0,
    }

    # Первый запуск.
    fetcher1 = _FetcherStub(response=response)
    r1 = fetch_and_store_notifications(
        fetcher=fetcher1, engine=db_engine, storage_dir=tmp_path,
    )
    assert r1["new_notifications"] == 1
    assert r1["attachments_saved"] == 1

    file_path = tmp_path / "notif-dup-003_doc.pdf"
    assert file_path.exists()
    # Подменяем содержимое — чтобы убедиться, что повторный вызов НЕ
    # перезаписывает существующий файл.
    file_path.write_bytes(b"manually changed")

    # Второй запуск с тем же ответом — НЕ должен ни INSERT'ить, ни
    # перезаписывать файл.
    fetcher2 = _FetcherStub(response=response)
    r2 = fetch_and_store_notifications(
        fetcher=fetcher2, engine=db_engine, storage_dir=tmp_path,
    )
    assert r2 == {
        "notifications_seen": 1,
        "new_notifications":  0,  # дедуп сработал
        "attachments_saved":  0,  # файл уже есть — не перезаписываем
        "errors":             0,
    }
    # И содержимое наше — не затёрто исходным payload.
    assert file_path.read_bytes() == b"manually changed"
    assert _count_rows(db_engine) == 1


def test_rate_limit_error_swallowed(db_engine, tmp_path):
    """4. Result=3 (rate-limit-loop) внутри _call_with_rate_limit ↗
    RuntimeError → сервис ловит, errors=1, БД не меняется, exception НЕ
    пробрасывается выше (Notification — вспомогательная операция)."""
    fetcher = _FetcherStub(
        exception=RuntimeError(
            "Resurs Media Notification: повторный Result=3 после паузы 65 сек."
        ),
    )

    result = fetch_and_store_notifications(
        fetcher=fetcher,
        engine=db_engine,
        storage_dir=tmp_path,
    )

    assert result == {
        "notifications_seen": 0,
        "new_notifications":  0,
        "attachments_saved":  0,
        "errors":             1,
    }
    assert _count_rows(db_engine) == 0
    # storage_dir не создавался (пустой ответ → не входим в mkdir-ветку).
    # tmp_path сам по себе уже существует (pytest), но файлов в нём нет.
    assert list(tmp_path.iterdir()) == []


def test_empty_response(db_engine, tmp_path):
    """5. Пустой Notification_Tab → counters=0, БД и storage_dir
    не меняются."""
    fetcher = _FetcherStub(response={
        "Notification_Tab": [],
        "Result": 0,
    })

    result = fetch_and_store_notifications(
        fetcher=fetcher,
        engine=db_engine,
        storage_dir=tmp_path,
    )

    assert result == {
        "notifications_seen": 0,
        "new_notifications":  0,
        "attachments_saved":  0,
        "errors":             0,
    }
    assert _count_rows(db_engine) == 0
    assert list(tmp_path.iterdir()) == []


def test_unsafe_filename_normalized(db_engine, tmp_path):
    """6. Имя вложения с ../, кириллицей и спецсимволами → нормализуется,
    файл сохранён внутри storage_dir, путь не выходит за пределы."""
    payload = b"safe content"
    fetcher = _FetcherStub(response={
        "Notification_Tab": [
            {
                "NotificationID": "notif-unsafe-006",
                "Text": "Опасное имя файла",
                "Attachment": payload,
                "AttachmentName": "../../etc/Документ с пробелами & знаками.txt",
            }
        ],
        "Result": 0,
    })

    result = fetch_and_store_notifications(
        fetcher=fetcher,
        engine=db_engine,
        storage_dir=tmp_path,
    )

    assert result["new_notifications"] == 1
    assert result["attachments_saved"] == 1
    assert result["errors"] == 0

    saved = list(tmp_path.iterdir())
    assert len(saved) == 1
    # Файл сохранён ВНУТРИ storage_dir.
    saved_path = saved[0].resolve()
    assert str(saved_path).startswith(str(tmp_path.resolve()))
    # Кириллица и пробелы заменены на _; путь-traversal обрезан basename'ом.
    fname = saved[0].name
    assert ".." not in fname
    assert "/" not in fname
    assert "\\" not in fname
    # Из исходного имени остаётся .txt и стартовый префикс с notification_id.
    assert fname.startswith("notif-unsafe-006_")
    assert fname.endswith(".txt")
    assert saved[0].read_bytes() == payload


# --- Бонус: тесты низкоуровневых helper-функций -------------------------

def test_safe_filename_helper():
    """7. _safe_filename: basename, замена не-[A-Za-z0-9._-] на _,
    подавление стартовых . и _, дефолт 'attachment' для пустых/None."""
    assert _safe_filename(None) == "attachment"
    assert _safe_filename("") == "attachment"
    assert _safe_filename("file.txt") == "file.txt"
    # path traversal — берём только basename.
    assert _safe_filename("../../etc/passwd") == "passwd"
    # Windows-разделители.
    assert _safe_filename(r"C:\Windows\System32\notepad.exe") == "notepad.exe"
    # Кириллица + пробелы → _.
    assert _safe_filename("Файл с пробелами.pdf") == "_.pdf"
    # Стартовая точка не превращается в dotfile.
    assert _safe_filename(".hidden") == "hidden"
    # Только опасные символы.
    assert _safe_filename("///") == "attachment"


def test_attachment_from_base64_string(db_engine, tmp_path):
    """8. zeep может вернуть Attachment как base64-строку (вместо
    готовых bytes) — сервис должен корректно декодировать."""
    original = b"binary blob"
    encoded = base64.b64encode(original).decode("ascii")
    fetcher = _FetcherStub(response={
        "Notification_Tab": [
            {
                "NotificationID": "notif-b64-008",
                "Text": "Base64 path",
                "Attachment": encoded,  # строка, не bytes
                "AttachmentName": "blob.bin",
            }
        ],
        "Result": 0,
    })

    result = fetch_and_store_notifications(
        fetcher=fetcher, engine=db_engine, storage_dir=tmp_path,
    )

    assert result["attachments_saved"] == 1
    saved = list(tmp_path.iterdir())
    assert len(saved) == 1
    assert saved[0].read_bytes() == original
