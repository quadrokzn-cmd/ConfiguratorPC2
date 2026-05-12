# Тесты sanity-чекера scripts/_resurs_media_safety.py.
#
# Цель — гарантировать, что ни smoke, ни bootstrap не выстрелят по prod-API
# Resurs Media без явного --allow-prod И интерактивного подтверждения YES.
#
# Тесты дёргают check_prod_safety напрямую — никаких подпроцессов или
# CLI-парсера, только pure-function behaviour.

from __future__ import annotations

import io

import pytest

from scripts._resurs_media_safety import check_prod_safety


TEST_URL = "https://testapi.resurs-media.ru/test9/ws/WSAPI?wsdl"
PROD_URL = "https://api.resurs-media.ru/ws/WSAPI?wsdl"


def _fail_input(_prompt: str) -> str:  # pragma: no cover
    """Маркер «input не должен был вызваться». Используется в кейсах,
    где safety обязан пропустить молча."""
    raise AssertionError("input() не должен был быть вызван")


def test_test_url_without_allow_prod_passes_silently():
    """Кейс 1: WSDL содержит 'test', --allow-prod не передан → проходит
    молча, input() не дёргается, exit не возникает."""
    out = io.StringIO()
    # SystemExit здесь — провал теста (а не ожидаемое поведение).
    check_prod_safety(TEST_URL, allow_prod=False, input_fn=_fail_input, out=out)
    assert out.getvalue() == ""


def test_test_url_with_allow_prod_passes_silently():
    """Кейс 2: WSDL содержит 'test', --allow-prod передан (безвреден) →
    проходит молча, без WARNING и без запроса YES."""
    out = io.StringIO()
    check_prod_safety(TEST_URL, allow_prod=True, input_fn=_fail_input, out=out)
    assert out.getvalue() == ""


def test_prod_url_without_allow_prod_exits_with_code_2():
    """Кейс 3: WSDL без 'test', --allow-prod не передан → SystemExit(2)
    с пояснением в stdout."""
    out = io.StringIO()
    with pytest.raises(SystemExit) as ei:
        check_prod_safety(PROD_URL, allow_prod=False, input_fn=_fail_input, out=out)
    assert ei.value.code == 2
    printed = out.getvalue()
    assert "ERROR" in printed
    assert "--allow-prod" in printed
    assert PROD_URL in printed


def test_prod_url_with_allow_prod_and_yes_passes():
    """Кейс 4: WSDL без 'test', --allow-prod передан, ввод 'YES' →
    проходит, в stdout есть WARNING."""
    out = io.StringIO()
    check_prod_safety(
        PROD_URL,
        allow_prod=True,
        input_fn=lambda _prompt: "YES",
        out=out,
    )
    printed = out.getvalue()
    assert "PRODUCTION RESURS MEDIA" in printed
    assert PROD_URL in printed


def test_prod_url_with_allow_prod_and_wrong_answer_exits_zero():
    """Кейс 5: WSDL без 'test', --allow-prod передан, ввод 'no' →
    SystemExit(0) (пользователь явно отказался)."""
    out = io.StringIO()
    with pytest.raises(SystemExit) as ei:
        check_prod_safety(
            PROD_URL,
            allow_prod=True,
            input_fn=lambda _prompt: "no",
            out=out,
        )
    assert ei.value.code == 0
    assert "Прервано пользователем" in out.getvalue()


def test_yes_must_be_uppercase():
    """Доп. кейс: 'yes' нижним регистром не считается подтверждением —
    защита от расфокуса (caps lock off)."""
    out = io.StringIO()
    with pytest.raises(SystemExit) as ei:
        check_prod_safety(
            PROD_URL,
            allow_prod=True,
            input_fn=lambda _prompt: "yes",
            out=out,
        )
    assert ei.value.code == 0


def test_input_with_trailing_whitespace_is_stripped():
    """Доп. кейс: 'YES\\n' с переводом строки от input() — должен
    распознаваться как подтверждение (strip перед сравнением)."""
    out = io.StringIO()
    check_prod_safety(
        PROD_URL,
        allow_prod=True,
        input_fn=lambda _prompt: "  YES\n",
        out=out,
    )
    assert "PRODUCTION RESURS MEDIA" in out.getvalue()
