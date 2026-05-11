# Реальный тест OpenAI-парсера: проверяет, что raw_summary не копирует
# входной запрос дословно, а содержит собственное резюме.
#
# Тратит деньги (~0.0005 $ за запуск), поэтому помечен @pytest.mark.live
# и пропускается, пока не задан RUN_LIVE_TESTS=1. Запуск вручную:
#
#   RUN_LIVE_TESTS=1 pytest tests/test_nlu/test_parser_live.py -s
#
# или на PowerShell:
#
#   $env:RUN_LIVE_TESTS=1; pytest tests/test_nlu/test_parser_live.py -s

from __future__ import annotations

import os

import pytest


pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _skip_if_disabled(monkeypatch):
    # Отменяем autouse-фикстуры conftest.py, которые подменяют API-ключ
    # и курс — тут нам нужен реальный OpenAI-вызов, а не фейковый клиент.
    if os.getenv("RUN_LIVE_TESTS") != "1":
        pytest.skip("live-тесты отключены (установите RUN_LIVE_TESTS=1)")


def test_parser_raw_summary_is_not_verbatim_copy():
    """На сложном запросе с несколькими упоминаниями моделей raw_summary
    должен быть собственным резюме, а не копией входа."""
    from dotenv import load_dotenv
    load_dotenv()

    from portal.services.configurator.nlu.parser import parse

    query = (
        "Системный блок УРМ в составе: iRU Office 310H6S Intel Core i5 12400, "
        "DDR4 16ГБ, 512ГБ(SSD), Intel UHD Graphics 730"
    )
    out = parse(query, usd_rub_rate=90.0)
    summary = (out.parsed.raw_summary or "").strip().lower()

    assert summary, "raw_summary пустой"
    # Не должен содержать характерный кусок исходника.
    assert "системный блок урм в составе" not in summary, (
        f"raw_summary всё ещё копирует входной текст:\n  {out.parsed.raw_summary}"
    )
    # Должен отражать извлечённые факты: тип ПК или хотя бы слово «понял».
    assert any(kw in summary for kw in ("офис", "понял", "частично")), (
        f"raw_summary не содержит осмысленного резюме:\n  {out.parsed.raw_summary}"
    )
