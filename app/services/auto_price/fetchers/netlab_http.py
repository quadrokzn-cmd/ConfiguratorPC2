# HTTP-канал автозагрузки прайса Netlab (этап 12.2).
#
# Прайс Netlab публикуется по прямой ссылке вида
# http://www.netlab.ru/products/dealerd.zip — без авторизации, без
# cookies и без referer'а. Внутри ZIP — DealerD.xlsx, который умеет
# читать существующий NetlabLoader (price_loaders/netlab.py: метод
# _open_workbook сам распаковывает .zip во временный каталог и чистит
# его в finally).
#
# Поток:
#   1. GET по NETLAB_PRICE_URL (httpx, follow_redirects, retry 3 с
#      backoff 5/15/45 на сетевых ошибках и 5xx).
#   2. Sanity-check размера: ≤ 50 МБ — по аналогии с IMAP-вложениями
#      (см. base_imap.max_attachment_size_mb).
#   3. Записываем bytes во временный файл (.zip), путь отдаём
#      NetlabLoader.iter_rows(); внутренний .xlsx распаковывается и
#      парсится тем же кодом, что и ручная загрузка через
#      /admin/price-uploads.
#   4. Передаём List[PriceRow] в общий save_price_rows() —
#      (UPSERT supplier_prices, mapping, disappeared, price_uploads).
#   5. В finally — удаляем временный .zip (NetlabLoader сам чистит
#      распакованный xlsx-каталог).
#
# Идемпотентность: у HTTP-канала нет ключа письма/Message-ID — каждый
# скачанный архив считается «свежим». Это безопасно: orchestrator при
# total_rows == 0 закрывается failed (и disappeared не запускается),
# а при ненулевом rows работает как обычный price-upload.

from __future__ import annotations

import logging
import os
import re
import tempfile
import time
from datetime import date
from urllib.parse import unquote, urlparse

import httpx

from app.services.auto_price.base import BaseAutoFetcher, register_fetcher
from app.services.price_loaders.models import PriceRow
from app.services.price_loaders.netlab import NetlabLoader


logger = logging.getLogger(__name__)


# Дефолтный URL — публичная дилерская ссылка Netlab. Если в окружении
# задан NETLAB_PRICE_URL, он перекроет дефолт. Если default снести и
# env пустая — __init__ кидает RuntimeError.
_DEFAULT_NETLAB_URL = "http://www.netlab.ru/products/dealerd.zip"

# Файл ~10–20 МБ; даём щедрый read-таймаут на медленный канал.
_TIMEOUT = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)

# Backoff между попытками на 5xx и сетевых ошибках.
_RETRY_BACKOFFS = (5, 15, 45)

# Лимит размера — общий c IMAP-вложениями (чтобы не повесить процесс
# на диком redirect-loop'е, отдающем чанками гигабайтами).
_MAX_SIZE_MB = 50


@register_fetcher
class NetlabHttpFetcher(BaseAutoFetcher):
    """HTTP-канал для Netlab: качаем ZIP по прямой ссылке и отдаём
    NetlabLoader для распаковки и парсинга."""

    supplier_slug = "netlab"
    supplier_display_name = "Netlab"  # совпадает с suppliers.name

    def __init__(self) -> None:
        env_url = (os.environ.get("NETLAB_PRICE_URL") or "").strip()
        self.url = env_url or _DEFAULT_NETLAB_URL
        if not self.url:
            raise RuntimeError(
                "Netlab HTTP: не задан URL прайса. Ожидается переменная "
                "окружения NETLAB_PRICE_URL (дефолт в коде — публичная "
                "дилерская ссылка)."
            )

    # ---- main entrypoint --------------------------------------------------

    def fetch_and_save(self) -> int:
        body, filename = self._download()
        return self._parse_and_save(body, filename)

    # ---- download ---------------------------------------------------------

    def _download(self) -> tuple[bytes, str]:
        """GET с retry. Возвращает (bytes, filename) или бросает RuntimeError.
        filename — из Content-Disposition либо basename URL."""
        max_bytes = _MAX_SIZE_MB * 1024 * 1024
        last_exc: Exception | None = None
        attempt = 0
        while attempt <= len(_RETRY_BACKOFFS):
            try:
                with httpx.Client(
                    timeout=_TIMEOUT, follow_redirects=True,
                ) as client:
                    r = client.get(self.url)

                if 200 <= r.status_code < 300:
                    # Если сервер прислал Content-Length, проверяем до
                    # чтения тела (хотя r.content уже выкачан — это спасает
                    # от обманных «Transfer-Encoding: chunked» и просто как
                    # ранний отбой по заголовку).
                    cl_raw = r.headers.get("Content-Length")
                    if cl_raw:
                        try:
                            cl_int = int(cl_raw)
                        except ValueError:
                            cl_int = None
                        if cl_int is not None and cl_int > max_bytes:
                            raise RuntimeError(
                                f"Netlab HTTP: Content-Length {cl_int} байт "
                                f"превышает лимит {_MAX_SIZE_MB} МБ."
                            )
                    body = r.content or b""
                    if not body:
                        raise RuntimeError(
                            f"Netlab HTTP: ответ пустой (0 байт) от {self.url}."
                        )
                    if len(body) > max_bytes:
                        raise RuntimeError(
                            f"Netlab HTTP: тело {len(body)} байт "
                            f"превышает лимит {_MAX_SIZE_MB} МБ."
                        )
                    return body, self._derive_filename(r)

                if 500 <= r.status_code < 600:
                    last_exc = RuntimeError(
                        f"Netlab HTTP: HTTP {r.status_code} от {self.url} "
                        f"— {(r.text or '')[:200]!r}"
                    )
                else:
                    # 4xx — клиентская ошибка, ретраить смысла нет.
                    raise RuntimeError(
                        f"Netlab HTTP: HTTP {r.status_code} от {self.url} "
                        f"— {(r.text or '')[:200]!r}"
                    )
            except httpx.RequestError as exc:
                last_exc = exc

            if attempt >= len(_RETRY_BACKOFFS):
                break
            backoff = _RETRY_BACKOFFS[attempt]
            logger.warning(
                "Netlab HTTP: попытка %d не удалась (%s), повтор через %dс.",
                attempt + 1, last_exc, backoff,
            )
            time.sleep(backoff)
            attempt += 1

        raise RuntimeError(
            f"Netlab HTTP: все попытки исчерпаны. Последняя ошибка: {last_exc}"
        )

    def _derive_filename(self, response: httpx.Response) -> str:
        """Имя файла для price_uploads. Сначала пытаемся вытащить из
        Content-Disposition (RFC 6266); если нет — basename URL'а."""
        cd = response.headers.get("Content-Disposition") or ""
        # filename*=UTF-8''…  либо filename="…"  либо filename=…
        m = re.search(
            r'filename\*?=(?:UTF-8\'\')?"?([^";]+)"?',
            cd,
            re.IGNORECASE,
        )
        if m:
            name = unquote(m.group(1)).strip()
            if name:
                return name
        path = urlparse(self.url).path or ""
        base = path.rsplit("/", 1)[-1]
        return unquote(base) if base else "netlab.zip"

    # ---- parse + save -----------------------------------------------------

    def _parse_and_save(self, data: bytes, filename: str) -> int:
        """Записываем bytes во временный файл (NetlabLoader работает с
        путём — load_workbook не ест поток), отдаём loader-у, чистим в
        finally. Внутри NetlabLoader сам распакует .zip."""
        suffix = ".zip" if filename.lower().endswith(".zip") else ".xlsx"
        # delete=False, чтобы можно было закрыть handle и openpyxl мог
        # переоткрыть файл сам (на Windows это обязательно).
        fd, path = tempfile.mkstemp(prefix="auto_netlab_http_", suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            loader = NetlabLoader()
            rows: list[PriceRow] = list(loader.iter_rows(path))
            logger.info(
                "Netlab HTTP: распарсено %d PriceRow из «%s» (%d байт)",
                len(rows), filename, len(data),
            )

            # filename для price_uploads — префикс auto_netlab_http_<дата>_…
            virtual_filename = (
                f"auto_netlab_http_{date.today().isoformat()}_{filename}"
            )

            # Импорт локальный — orchestrator тянет много тяжёлого, не нужно
            # поднимать его при чистом импорте fetcher-модуля.
            from app.services.price_loaders.orchestrator import save_price_rows

            result = save_price_rows(
                supplier_name="Netlab",
                source=virtual_filename,
                rows=rows,
            )
            return int(result["upload_id"])
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
