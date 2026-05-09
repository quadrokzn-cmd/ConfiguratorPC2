from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Iterator

from bs4 import BeautifulSoup
from loguru import logger

from app.services.auctions.ingest.http_client import ZAKUPKI_BASE, ZakupkiClient

SEARCH_PATH = "/epz/order/extendedsearch/results.html"
NOTICE_LINK_RE = re.compile(r"/epz/order/notice/[^/]+/view/common-info\.html\?regNumber=\d+")
REG_NUMBER_RE = re.compile(r"regNumber=(\d{19,20})")


@dataclass(frozen=True)
class SearchHit:
    reg_number: str
    url: str


def build_search_params(
    ktru_code: str,
    display_name: str,
    page_number: int,
    records_per_page: int = 50,
) -> dict[str, object]:
    """Собрать параметры структурированного KTRU-поиска по zakupki.gov.ru.

    Параметр `ktruCodeNameList` имеет вид `<КОД>&&&<НАЗВАНИЕ>` — это пара
    «код+название», которую zakupki использует как структурированный фильтр
    (URL-encoded `&&&` = `%26%26%26`). Без этого параметра поиск падает на
    текстовый match по `searchString` и почти ничего не находит.

    Этап «подача заявок» = `af=on`. Дату публикации не фильтруем — этап подачи
    уже отсекает закрытые лоты, и собственник проверял: окно «активные» даёт
    нужный объём.
    """
    return {
        "morphology": "on",
        "fz44": "on",
        "af": "on",
        "currencyIdGeneral": "-1",
        "showLotsInfoHidden": "false",
        "sortBy": "UPDATE_DATE",
        "sortDirection": "false",
        "recordsPerPage": f"_{records_per_page}",
        "pageNumber": page_number,
        "ktruCodeNameList": f"{ktru_code}&&&{display_name}",
        "ktruSelectedPageNum": "1",
    }


def _extract_hits(html: str) -> list[SearchHit]:
    soup = BeautifulSoup(html, "lxml")
    hits: dict[str, SearchHit] = {}
    for link in soup.find_all("a", href=NOTICE_LINK_RE):
        href = link.get("href", "")
        match = REG_NUMBER_RE.search(href)
        if not match:
            continue
        reg_number = match.group(1)
        if reg_number in hits:
            continue
        url = href if href.startswith("http") else f"{ZAKUPKI_BASE}{href}"
        hits[reg_number] = SearchHit(reg_number=reg_number, url=url)
    return list(hits.values())


def _has_no_results(html: str) -> bool:
    return "Поиск не дал результатов" in html


def search_by_ktru(
    client: ZakupkiClient,
    ktru_code: str,
    display_name: str,
    max_pages: int = 10,
    records_per_page: int = 50,
) -> Iterator[SearchHit]:
    seen: set[str] = set()
    for page_number in range(1, max_pages + 1):
        params = build_search_params(ktru_code, display_name, page_number, records_per_page)
        html = client.get_html(SEARCH_PATH, params=params)
        if _has_no_results(html):
            logger.info("search ktru={} page={} → no results", ktru_code, page_number)
            return
        hits = _extract_hits(html)
        if not hits:
            logger.info("search ktru={} page={} → empty page, stopping pagination", ktru_code, page_number)
            return
        new_on_page = 0
        for hit in hits:
            if hit.reg_number in seen:
                continue
            seen.add(hit.reg_number)
            new_on_page += 1
            yield hit
        logger.info(
            "search ktru={} page={} → {} hits ({} new)",
            ktru_code, page_number, len(hits), new_on_page,
        )
        if new_on_page == 0:
            return


def collect_hits(
    client: ZakupkiClient,
    watchlist: Iterable[tuple[str, str]],
    max_pages: int = 10,
) -> dict[str, SearchHit]:
    """Пройтись по watchlist пар (код, название), агрегируя уникальные reg_numbers."""
    pairs = list(watchlist)
    aggregated: dict[str, SearchHit] = {}
    for code, display_name in pairs:
        for hit in search_by_ktru(client, code, display_name, max_pages=max_pages):
            aggregated.setdefault(hit.reg_number, hit)
    logger.info(
        "search aggregated: {} unique reg_numbers from {} ktru codes",
        len(aggregated), len(pairs),
    )
    return aggregated
