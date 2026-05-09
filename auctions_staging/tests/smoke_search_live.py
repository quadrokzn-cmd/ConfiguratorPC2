"""Опциональный live-смок (HTTP) — не запускается в обычном pytest.
Запуск: python -m tests.smoke_search_live

Тянет реальные лоты с zakupki.gov.ru по двум KTRU-зонтикам (МФУ и Принтер)
через структурированный фильтр `ktruCodeNameList` (миграция 0009).
Печатает количество и первые 5 reg_numbers — проверка, что клиент + парсер
списка работают и что новый параметр поиска возвращает ожидаемый объём.
"""

from __future__ import annotations

from app.modules.auctions.ingest.http_client import ZakupkiClient
from app.modules.auctions.ingest.search import search_by_ktru

WATCHLIST: tuple[tuple[str, str], ...] = (
    ("26.20.18.000-00000001", "Многофункциональное устройство (МФУ)"),
    ("26.20.16.120-00000001", "Принтер"),
)


def main() -> None:
    with ZakupkiClient(delay_min=1.5, delay_max=3.0) as client:
        for code, display_name in WATCHLIST:
            hits = list(
                search_by_ktru(client, code, display_name, max_pages=2)
            )
            print(f"KTRU {code} ({display_name}): {len(hits)} hits")
            for hit in hits[:5]:
                print(f"  {hit.reg_number} {hit.url}")


if __name__ == "__main__":
    main()
