# Тесты NetlabHttpFetcher — HTTP-канал Netlab (этап 12.2).
#
# httpx подменяем FakeClient'ом — никакой реальной сети. NetlabLoader
# подменяем стабом, чтобы не разводить большой XLSX-фикстур: нам важно
# проверить, что fetcher СОБИРАЕТ байты, ВРЕМЕННЫЙ ФАЙЛ создан и существует
# на момент iter_rows, а потом ОЧИЩЕН в finally.

from __future__ import annotations

import os

import httpx
import pytest


# =====================================================================
# Helpers — подмена httpx.Client
# =====================================================================

class FakeResponse:
    def __init__(
        self,
        status_code: int,
        content: bytes = b"",
        headers: dict | None = None,
        text: str = "",
    ):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.text = text


class FakeClient:
    """httpx.Client-совместимый мок: get(url) возвращает то, что отдаёт
    handler. handler — callable(url) → FakeResponse либо raises."""

    def __init__(self, handler):
        self._handler = handler

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url):
        return self._handler(url)


def _patch_httpx(monkeypatch, handler):
    import app.services.auto_price.fetchers.netlab_http as mod

    def _factory(timeout=None, follow_redirects=False):
        return FakeClient(handler)

    monkeypatch.setattr(mod.httpx, "Client", _factory)


def _patch_no_sleep(monkeypatch):
    """Чтобы retry-backoff не задерживал тесты."""
    import app.services.auto_price.fetchers.netlab_http as mod
    monkeypatch.setattr(mod.time, "sleep", lambda *_a, **_k: None)


# Вспомогательный байтовый «ZIP-маркер» — реального unzip-а в этих тестах
# нет (NetlabLoader подменён), просто знакомая последовательность для
# наглядности при отладке.
_FAKE_ZIP_BYTES = b"PK\x03\x04" + b"X" * 1024 + b"END"


# =====================================================================
# Stub NetlabLoader — записывает путь и читает байты обратно
# =====================================================================

class _StubLoader:
    def __init__(self):
        self.calls = []

    def iter_rows(self, filepath):
        # Ожидаем: файл существует к моменту вызова.
        self.calls.append({
            "filepath": filepath,
            "exists":   os.path.exists(filepath),
            "bytes":    open(filepath, "rb").read(),
        })
        return iter([])  # пустой Iterator[PriceRow]


def _patch_loader(monkeypatch, loader_instance):
    import app.services.auto_price.fetchers.netlab_http as mod
    monkeypatch.setattr(mod, "NetlabLoader", lambda: loader_instance)


def _patch_save_price_rows(monkeypatch, *, upload_id=42):
    """Подменяет orchestrator.save_price_rows — НЕ полезли в БД."""
    captured: dict = {}

    def _fake_save(*, supplier_name, source, rows):
        captured["supplier_name"] = supplier_name
        captured["source"] = source
        captured["rows"] = list(rows) if rows is not None else []
        return {"upload_id": upload_id}

    import app.services.price_loaders.orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "save_price_rows", _fake_save)
    return captured


# =====================================================================
# 1. Скачивание + парсинг + save_price_rows вызывается с правильными аргументами
# =====================================================================

def test_fetcher_downloads_and_parses(monkeypatch):
    from app.services.auto_price.fetchers.netlab_http import NetlabHttpFetcher

    monkeypatch.setenv(
        "NETLAB_PRICE_URL", "http://example.test/products/dealerd.zip",
    )

    seen_url = {}

    def handler(url):
        seen_url["url"] = url
        return FakeResponse(
            200,
            content=_FAKE_ZIP_BYTES,
            headers={"Content-Length": str(len(_FAKE_ZIP_BYTES))},
        )

    _patch_httpx(monkeypatch, handler)
    loader = _StubLoader()
    _patch_loader(monkeypatch, loader)
    captured = _patch_save_price_rows(monkeypatch, upload_id=777)

    fetcher = NetlabHttpFetcher()
    upload_id = fetcher.fetch_and_save()

    assert upload_id == 777
    # GET ушёл по env-URL.
    assert seen_url["url"] == "http://example.test/products/dealerd.zip"

    # Loader получил путь к временному .zip с правильными байтами.
    assert len(loader.calls) == 1
    call = loader.calls[0]
    assert call["filepath"].endswith(".zip")
    assert call["exists"] is True
    assert call["bytes"] == _FAKE_ZIP_BYTES

    # save_price_rows позвана с supplier_name="Netlab" и filename из URL.
    assert captured["supplier_name"] == "Netlab"
    assert captured["source"].startswith("auto_netlab_http_")
    assert "dealerd.zip" in captured["source"]
    assert captured["rows"] == []  # стаб-loader вернул пустой iterator

    # Временный файл удалён (после iter_rows + save → finally).
    assert not os.path.exists(call["filepath"])


# =====================================================================
# 2. 5xx → retry, через несколько попыток успех
# =====================================================================

def test_fetcher_retries_on_5xx(monkeypatch):
    from app.services.auto_price.fetchers.netlab_http import NetlabHttpFetcher

    monkeypatch.setenv("NETLAB_PRICE_URL", "http://example.test/dealerd.zip")
    _patch_no_sleep(monkeypatch)

    state = {"calls": 0}

    def handler(url):
        state["calls"] += 1
        if state["calls"] < 3:
            return FakeResponse(503, text="bad")
        return FakeResponse(
            200,
            content=_FAKE_ZIP_BYTES,
            headers={"Content-Length": str(len(_FAKE_ZIP_BYTES))},
        )

    _patch_httpx(monkeypatch, handler)
    _patch_loader(monkeypatch, _StubLoader())
    _patch_save_price_rows(monkeypatch, upload_id=1)

    fetcher = NetlabHttpFetcher()
    fetcher.fetch_and_save()

    # Две неудачные попытки + одна успешная.
    assert state["calls"] == 3


def test_fetcher_retries_on_network_error(monkeypatch):
    """httpx.RequestError тоже должен ретраиться."""
    from app.services.auto_price.fetchers.netlab_http import NetlabHttpFetcher

    monkeypatch.setenv("NETLAB_PRICE_URL", "http://example.test/dealerd.zip")
    _patch_no_sleep(monkeypatch)

    state = {"calls": 0}

    def handler(url):
        state["calls"] += 1
        if state["calls"] == 1:
            raise httpx.ConnectError("network down", request=None)
        return FakeResponse(
            200,
            content=_FAKE_ZIP_BYTES,
            headers={"Content-Length": str(len(_FAKE_ZIP_BYTES))},
        )

    _patch_httpx(monkeypatch, handler)
    _patch_loader(monkeypatch, _StubLoader())
    _patch_save_price_rows(monkeypatch)

    fetcher = NetlabHttpFetcher()
    fetcher.fetch_and_save()
    assert state["calls"] == 2


def test_fetcher_gives_up_after_all_retries(monkeypatch):
    """Бесконечный 5xx → RuntimeError со ссылкой на последнюю ошибку."""
    from app.services.auto_price.fetchers.netlab_http import NetlabHttpFetcher

    monkeypatch.setenv("NETLAB_PRICE_URL", "http://example.test/dealerd.zip")
    _patch_no_sleep(monkeypatch)

    def handler(url):
        return FakeResponse(503, text="still bad")

    _patch_httpx(monkeypatch, handler)
    _patch_loader(monkeypatch, _StubLoader())

    fetcher = NetlabHttpFetcher()
    with pytest.raises(RuntimeError, match="попытки исчерпаны"):
        fetcher.fetch_and_save()


# =====================================================================
# 3. Слишком большой ответ → RuntimeError, БД не трогаем
# =====================================================================

def test_fetcher_raises_on_oversized_attachment(monkeypatch):
    """Content-Length > 50 МБ → RuntimeError, save_price_rows не зван."""
    from app.services.auto_price.fetchers.netlab_http import NetlabHttpFetcher

    monkeypatch.setenv("NETLAB_PRICE_URL", "http://example.test/dealerd.zip")

    def handler(url):
        # Фейковый Content-Length в 51 МБ — тело тут не выкачиваем,
        # проверка должна сработать на заголовке.
        return FakeResponse(
            200,
            content=b"",
            headers={"Content-Length": str(51 * 1024 * 1024)},
        )

    _patch_httpx(monkeypatch, handler)
    _patch_loader(monkeypatch, _StubLoader())
    captured = _patch_save_price_rows(monkeypatch)

    fetcher = NetlabHttpFetcher()
    with pytest.raises(RuntimeError, match="превышает лимит"):
        fetcher.fetch_and_save()

    # save_price_rows не вызвана.
    assert "supplier_name" not in captured


def test_fetcher_raises_on_empty_body(monkeypatch):
    """Пустое тело — RuntimeError (иначе передали бы NetlabLoader-у пустой
    .zip и получили бы непрозрачную ошибку distantly от парсера)."""
    from app.services.auto_price.fetchers.netlab_http import NetlabHttpFetcher

    monkeypatch.setenv("NETLAB_PRICE_URL", "http://example.test/dealerd.zip")
    _patch_no_sleep(monkeypatch)

    def handler(url):
        return FakeResponse(200, content=b"", headers={})

    _patch_httpx(monkeypatch, handler)

    fetcher = NetlabHttpFetcher()
    with pytest.raises(RuntimeError, match="пустой"):
        fetcher.fetch_and_save()


# =====================================================================
# 4. Если URL не сконфигурирован (env пуст и default снесён) → RuntimeError
# =====================================================================

def test_fetcher_raises_when_url_not_configured(monkeypatch):
    """Если в окружении пусто И _DEFAULT_NETLAB_URL принудительно очищен —
    __init__ должен бросить RuntimeError. Дефолт как раз и существует,
    чтобы этот сценарий не наступил в проде; но защита нужна на случай
    рефакторинга, который снесёт дефолт."""
    import app.services.auto_price.fetchers.netlab_http as mod
    from app.services.auto_price.fetchers.netlab_http import NetlabHttpFetcher

    monkeypatch.delenv("NETLAB_PRICE_URL", raising=False)
    monkeypatch.setattr(mod, "_DEFAULT_NETLAB_URL", "")

    with pytest.raises(RuntimeError, match="NETLAB_PRICE_URL"):
        NetlabHttpFetcher()


def test_fetcher_uses_default_url_when_env_empty(monkeypatch):
    """Если env-переменная не задана, fetcher должен использовать
    _DEFAULT_NETLAB_URL (публичную дилерскую ссылку)."""
    import app.services.auto_price.fetchers.netlab_http as mod
    from app.services.auto_price.fetchers.netlab_http import NetlabHttpFetcher

    monkeypatch.delenv("NETLAB_PRICE_URL", raising=False)

    fetcher = NetlabHttpFetcher()
    assert fetcher.url == mod._DEFAULT_NETLAB_URL
    assert fetcher.url.startswith("http")
    assert fetcher.url.endswith(".zip")


# =====================================================================
# 5. Cleanup временного файла на исключении
# =====================================================================

def test_fetcher_cleans_up_temp_file_on_exception(monkeypatch, tmp_path):
    """Если NetlabLoader/iter_rows бросает ошибку — finally должен
    удалить временный .zip. Иначе на проде прайс качали бы каждый день
    с утечкой ~10 МБ в /tmp."""
    from app.services.auto_price.fetchers.netlab_http import NetlabHttpFetcher

    monkeypatch.setenv("NETLAB_PRICE_URL", "http://example.test/dealerd.zip")

    def handler(url):
        return FakeResponse(
            200,
            content=_FAKE_ZIP_BYTES,
            headers={"Content-Length": str(len(_FAKE_ZIP_BYTES))},
        )

    _patch_httpx(monkeypatch, handler)

    captured_path = {}

    class _ExplodingLoader:
        def iter_rows(self, filepath):
            captured_path["path"] = filepath
            assert os.path.exists(filepath), "файл должен быть на месте"
            raise ValueError("xlsx не открылся")

    _patch_loader(monkeypatch, _ExplodingLoader())
    _patch_save_price_rows(monkeypatch)  # на всякий случай

    fetcher = NetlabHttpFetcher()
    with pytest.raises(ValueError, match="xlsx не открылся"):
        fetcher.fetch_and_save()

    # Временный файл удалён, несмотря на исключение.
    assert "path" in captured_path
    assert not os.path.exists(captured_path["path"])


# =====================================================================
# 6. _derive_filename — Content-Disposition приоритетнее URL
# =====================================================================

def test_derive_filename_prefers_content_disposition(monkeypatch):
    from app.services.auto_price.fetchers.netlab_http import NetlabHttpFetcher

    monkeypatch.setenv("NETLAB_PRICE_URL", "http://example.test/static/d.zip")
    fetcher = NetlabHttpFetcher()

    # Прямое имя в Content-Disposition.
    r1 = FakeResponse(
        200,
        headers={"Content-Disposition": 'attachment; filename="DealerD.zip"'},
    )
    assert fetcher._derive_filename(r1) == "DealerD.zip"

    # Без кавычек.
    r2 = FakeResponse(
        200, headers={"Content-Disposition": "attachment; filename=DealerD.zip"},
    )
    assert fetcher._derive_filename(r2) == "DealerD.zip"

    # Без Content-Disposition — basename из URL.
    r3 = FakeResponse(200, headers={})
    assert fetcher._derive_filename(r3) == "d.zip"


# =====================================================================
# 7. Регистрация в реестре fetcher'ов
# =====================================================================

def test_netlab_fetcher_is_registered():
    """@register_fetcher должен повесить класс под slug 'netlab'."""
    from app.services.auto_price.base import get_fetcher_class
    from app.services.auto_price.fetchers.netlab_http import NetlabHttpFetcher

    cls = get_fetcher_class("netlab")
    assert cls is NetlabHttpFetcher
