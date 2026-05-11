from __future__ import annotations

import random
import time
from typing import Iterable

import httpx
from loguru import logger

ZAKUPKI_BASE = "https://zakupki.gov.ru"

USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
)

DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


class ZakupkiBlockedError(RuntimeError):
    pass


class ZakupkiClient:
    """Sync HTTP client for zakupki.gov.ru with UA rotation, retries, and throttling.

    Throttling — random sleep within [delay_min, delay_max] before every request to
    avoid bursts. Retries — exponential backoff on transient HTTP errors and on
    suspected anti-bot pages.
    """

    def __init__(
        self,
        delay_min: float = 1.0,
        delay_max: float = 2.0,
        max_retries: int = 3,
        timeout: float = 30.0,
        user_agents: Iterable[str] | None = None,
    ) -> None:
        self._delay_min = delay_min
        self._delay_max = delay_max
        self._max_retries = max_retries
        self._user_agents = tuple(user_agents) if user_agents else USER_AGENTS
        self._client = httpx.Client(
            base_url=ZAKUPKI_BASE,
            timeout=timeout,
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ZakupkiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _pick_ua(self) -> str:
        return random.choice(self._user_agents)

    def _throttle(self) -> None:
        time.sleep(random.uniform(self._delay_min, self._delay_max))

    def get_html(self, url: str, params: dict[str, object] | None = None) -> str:
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            self._throttle()
            headers = {"User-Agent": self._pick_ua()}
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.RequestError as exc:
                last_exc = exc
                logger.warning(
                    "zakupki request error (attempt {}/{}): {}",
                    attempt, self._max_retries, exc,
                )
                time.sleep(2.0 ** attempt)
                continue

            if response.status_code == 200:
                text = response.text
                if _looks_blocked(text):
                    last_exc = ZakupkiBlockedError(f"blocked-page heuristic for {url}")
                    logger.warning(
                        "zakupki returned anti-bot page (attempt {}/{})",
                        attempt, self._max_retries,
                    )
                    time.sleep(5.0 * attempt)
                    continue
                return text

            if response.status_code in (429, 502, 503, 504):
                last_exc = httpx.HTTPStatusError(
                    f"transient {response.status_code}", request=response.request, response=response,
                )
                logger.warning(
                    "zakupki transient {} (attempt {}/{})",
                    response.status_code, attempt, self._max_retries,
                )
                time.sleep(2.0 ** attempt)
                continue

            response.raise_for_status()
            return response.text

        raise ZakupkiBlockedError(f"failed to fetch {url} after {self._max_retries} attempts") from last_exc


def _looks_blocked(html: str) -> bool:
    if not html or len(html) < 500:
        return True
    lowered = html[:4000].lower()
    markers = ("captcha", "доступ ограничен", "access denied", "请稍候")
    return any(marker in lowered for marker in markers)
