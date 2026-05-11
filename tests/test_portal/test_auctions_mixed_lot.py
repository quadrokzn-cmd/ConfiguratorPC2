"""Тесты бейджа типа лота и фильтра print-only на /auctions (мини-этап 9a-mixed-lot-flag).

Проверяем:
- лот со всеми позициями печатными → badge «только оргтехника»;
- лот с 1 печатной + 3 непечатных → badge «смешанный лот»;
- лот без печатных позиций → badge «смешанный лот» (тот же mixed-кейс);
- фильтр print_only сужает выдачу до чисто print-лотов.

Префиксы KTRU для печатной техники:
  - 26.20.18.000- → МФУ;
  - 26.20.16.120- → Принтер.
"""
from __future__ import annotations

from tests.test_portal.auctions_fixtures import (
    auctions_viewer,     # noqa: F401 — pytest fixture
    insert_tender,
    insert_tender_item,
    login_as,
)


def test_badge_print_only_when_all_items_are_printers(
    portal_client, auctions_viewer, db_session,
):
    """Все позиции — МФУ/Принтер → бейдж «только оргтехника»."""
    rn = "print-only-lot-001"
    insert_tender(db_session, reg_number=rn, submit_deadline_offset_hours=-72)
    insert_tender_item(
        db_session, tender_id=rn, position_num=1,
        ktru_code="26.20.18.000-00000001", name="МФУ A4 ч/б",
    )
    insert_tender_item(
        db_session, tender_id=rn, position_num=2,
        ktru_code="26.20.16.120-00000001", name="Принтер A4 ч/б",
    )

    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    assert f'data-testid="lot-row-{rn}"' in r.text
    assert f'data-testid="lot-type-print-{rn}"' in r.text
    assert "только оргтехника" in r.text
    assert f'data-testid="lot-type-mixed-{rn}"' not in r.text


def test_badge_mixed_when_some_items_are_non_printer(
    portal_client, auctions_viewer, db_session,
):
    """1 печатная + 3 непечатных → бейдж «смешанный лот»."""
    rn = "mixed-lot-001"
    insert_tender(db_session, reg_number=rn, submit_deadline_offset_hours=-72)
    insert_tender_item(
        db_session, tender_id=rn, position_num=1,
        ktru_code="26.20.18.000-00000001", name="МФУ A4 ч/б",
    )
    # 3 непечатных позиции — мониторы / ПК / ноутбук.
    insert_tender_item(
        db_session, tender_id=rn, position_num=2,
        ktru_code="26.20.17.110-00000001", name="Монитор 24\"",
    )
    insert_tender_item(
        db_session, tender_id=rn, position_num=3,
        ktru_code="26.20.15.000-00000001", name="Системный блок",
    )
    insert_tender_item(
        db_session, tender_id=rn, position_num=4,
        ktru_code=None, name="Ноутбук без KTRU",
    )

    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    assert f'data-testid="lot-type-mixed-{rn}"' in r.text
    assert "смешанный лот" in r.text
    assert f'data-testid="lot-type-print-{rn}"' not in r.text


def test_badge_mixed_when_zero_printer_items(
    portal_client, auctions_viewer, db_session,
):
    """Лот без печатных позиций → бейдж «смешанный лот» (тот же mixed-кейс).

    На практике такие лоты в инбоксе не должны появляться (KTRU-watchlist
    ingest'a фильтрует только печатные префиксы), но если попадут через
    ручной триггер или search — бейдж сигнализирует «не чисто печать».
    """
    rn = "no-print-lot-001"
    insert_tender(db_session, reg_number=rn, submit_deadline_offset_hours=-72)
    insert_tender_item(
        db_session, tender_id=rn, position_num=1,
        ktru_code="26.20.17.110-00000001", name="Монитор 24\"",
    )
    insert_tender_item(
        db_session, tender_id=rn, position_num=2,
        ktru_code="26.20.15.000-00000001", name="Системный блок",
    )

    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    assert f'data-testid="lot-type-mixed-{rn}"' in r.text
    assert "смешанный лот" in r.text
    assert f'data-testid="lot-type-print-{rn}"' not in r.text


def test_print_only_filter_narrows_results(
    portal_client, auctions_viewer, db_session,
):
    """Фильтр print_only=1 оставляет только чисто print-лоты."""
    # Чисто print
    rn_print = "po-print"
    insert_tender(db_session, reg_number=rn_print, submit_deadline_offset_hours=-72)
    insert_tender_item(
        db_session, tender_id=rn_print, position_num=1,
        ktru_code="26.20.18.000-00000001", name="МФУ",
    )
    # Mixed
    rn_mixed = "po-mixed"
    insert_tender(db_session, reg_number=rn_mixed, submit_deadline_offset_hours=-72)
    insert_tender_item(
        db_session, tender_id=rn_mixed, position_num=1,
        ktru_code="26.20.18.000-00000001", name="МФУ",
    )
    insert_tender_item(
        db_session, tender_id=rn_mixed, position_num=2,
        ktru_code="26.20.15.000-00000001", name="Системный блок",
    )
    # No print
    rn_noprint = "po-noprint"
    insert_tender(db_session, reg_number=rn_noprint, submit_deadline_offset_hours=-72)
    insert_tender_item(
        db_session, tender_id=rn_noprint, position_num=1,
        ktru_code="26.20.17.110-00000001", name="Монитор",
    )

    login_as(portal_client, auctions_viewer)

    # Без фильтра — все три лота на странице.
    r_all = portal_client.get("/auctions")
    assert r_all.status_code == 200
    assert f'data-testid="lot-row-{rn_print}"' in r_all.text
    assert f'data-testid="lot-row-{rn_mixed}"' in r_all.text
    assert f'data-testid="lot-row-{rn_noprint}"' in r_all.text

    # С print_only=1 — только чисто print-лот.
    r_po = portal_client.get("/auctions?print_only=1")
    assert r_po.status_code == 200
    assert f'data-testid="lot-row-{rn_print}"' in r_po.text
    assert f'data-testid="lot-row-{rn_mixed}"' not in r_po.text
    assert f'data-testid="lot-row-{rn_noprint}"' not in r_po.text


def test_no_badge_when_tender_has_no_items(
    portal_client, auctions_viewer, db_session,
):
    """Тендер без позиций — бейджа типа лота нет (никакой)."""
    rn = "empty-items-lot"
    insert_tender(db_session, reg_number=rn, submit_deadline_offset_hours=-72)

    login_as(portal_client, auctions_viewer)
    r = portal_client.get("/auctions")
    assert r.status_code == 200
    assert f'data-testid="lot-row-{rn}"' in r.text
    assert f'data-testid="lot-type-print-{rn}"' not in r.text
    assert f'data-testid="lot-type-mixed-{rn}"' not in r.text
