"""Live-смок парсера карточки. Тянет одну реальную карточку и печатает разобранные поля.
Запуск: python -m tests.smoke_card_live <reg_number>"""

from __future__ import annotations

import sys

from app.modules.auctions.ingest.card_parser import parse_card
from app.modules.auctions.ingest.http_client import ZAKUPKI_BASE, ZakupkiClient


def main(reg_number: str) -> None:
    url = f"{ZAKUPKI_BASE}/epz/order/notice/ea20/view/common-info.html?regNumber={reg_number}"
    with ZakupkiClient(delay_min=1.0, delay_max=2.0) as client:
        html = client.get_html(url)
    card = parse_card(reg_number, url, html)
    print(f"reg_number   : {card.reg_number}")
    print(f"customer     : {card.customer}")
    print(f"region       : {card.customer_region}")
    print(f"nmck_total   : {card.nmck_total}")
    print(f"publish_date : {card.publish_date}")
    print(f"submit_dl    : {card.submit_deadline}")
    print(f"delivery_dl  : {card.delivery_deadline}")
    print(f"contacts     : {card.customer_contacts_jsonb}")
    print(f"ktru_codes   : {card.ktru_codes}")
    print(f"items count  : {len(card.items)}")
    for i, it in enumerate(card.items, 1):
        print(f"  [{i}] ktru={it.ktru_code} qty={it.qty} unit={it.unit} price={it.nmck_per_unit} name={it.name!r}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "0816500000626007072")
