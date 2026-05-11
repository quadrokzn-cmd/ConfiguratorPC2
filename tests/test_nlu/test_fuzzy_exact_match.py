# Тесты точного совпадения по номеру модели в fuzzy_lookup.
#
# Сценарий бага, из-за которого тест и написан:
#   Запрос менеджера: "Intel Core i5 12400"
#   В БД есть и "Intel Core i5-12400" (id=9), и "Intel Core i5-12400F" (id=14).
#   Обычная сортировка по цене возвращала 12400F первым, хотя менеджер
#   писал без F → выбирали не то.
#
# Теперь rerank_by_exact_match должен поднимать наверх кандидата с
# совпадающим base+suffix, не нарушая порядок цен внутри одного ранга.

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from portal.services.configurator.nlu import fuzzy_lookup
from portal.services.configurator.nlu.schema import ModelMention


# -----------------------------------------------------------------------------
# extract_model_number — чистая функция, без БД
# -----------------------------------------------------------------------------

class TestExtractModelNumber:
    def test_plain_four_digit(self):
        assert fuzzy_lookup.extract_model_number("I5 12400") == ("12400", "")

    def test_with_suffix_f(self):
        assert fuzzy_lookup.extract_model_number("I5 13400F") == ("13400", "F")

    def test_with_suffix_kf(self):
        assert fuzzy_lookup.extract_model_number("I7 13700KF") == ("13700", "KF")

    def test_five_digit(self):
        assert fuzzy_lookup.extract_model_number("RYZEN 5 7600X") == ("7600", "X")

    def test_no_number(self):
        assert fuzzy_lookup.extract_model_number("ASUS PRIME") is None

    def test_dashed_in_model_name(self):
        # "INTEL CORE I5-12400" — после re.sub не-буквенных на пробел становится
        # "INTEL CORE I5 12400", нам же важно, чтобы сама функция работала и на
        # дефисированных строках (она ищет \b(\d{4,5})([A-Z]*)\b и справляется).
        assert fuzzy_lookup.extract_model_number("INTEL CORE I5-12400") == ("12400", "")
        assert fuzzy_lookup.extract_model_number("INTEL CORE I5-12400F") == ("12400", "F")

    def test_last_match_wins(self):
        # В прайсах часто: «… i5-12400 / BX8071512400». Берём именно модель,
        # не кусок 10-значного SKU (он за рамками 4-5 цифр, мимо).
        assert fuzzy_lookup.extract_model_number("CORE I5-12400 BX8071512400") \
            == ("12400", "")


# -----------------------------------------------------------------------------
# rerank_by_exact_match — тоже чистая функция
# -----------------------------------------------------------------------------

class TestRerank:
    def test_exact_wins_over_cheaper_suffix(self):
        # В БД сначала дешевле F-версия (как у нас с 12400 и 12400F),
        # но запрос без F → наверху должен быть 12400.
        rows = [
            {"id": 14, "model": "Intel Core i5-12400F", "sku": "X", "min_price": 150.0},
            {"id": 9,  "model": "Intel Core i5-12400",  "sku": "Y", "min_price": 180.0},
        ]
        out = fuzzy_lookup.rerank_by_exact_match(rows, query_upper="CORE I5 12400")
        assert [r["id"] for r in out] == [9, 14]

    def test_suffix_match_wins(self):
        # Запрос с F → вверх уходит F-вариант.
        rows = [
            {"id": 9,  "model": "Intel Core i5-12400",  "sku": "Y", "min_price": 180.0},
            {"id": 14, "model": "Intel Core i5-12400F", "sku": "X", "min_price": 150.0},
        ]
        out = fuzzy_lookup.rerank_by_exact_match(rows, query_upper="I5 12400F")
        assert [r["id"] for r in out] == [14, 9]

    def test_no_exact_keeps_price_order(self):
        # Запрос 13400, в БД только 13400F — порядок сохраняется (по цене).
        rows = [
            {"id": 2, "model": "Intel Core i5-13400F", "sku": "A", "min_price": 170.0},
            {"id": 3, "model": "Intel Core i5-13400F", "sku": "B", "min_price": 200.0},
        ]
        out = fuzzy_lookup.rerank_by_exact_match(rows, query_upper="I5 13400")
        assert [r["id"] for r in out] == [2, 3]

    def test_stable_within_same_rank(self):
        # Несколько точных совпадений — порядок по цене сохраняется.
        rows = [
            {"id": 1, "model": "AMD Ryzen 5 7600 OEM", "sku": "O", "min_price": 175.0},
            {"id": 2, "model": "AMD Ryzen 5 7600 BOX", "sku": "B", "min_price": 200.0},
        ]
        out = fuzzy_lookup.rerank_by_exact_match(rows, query_upper="RYZEN 5 7600")
        assert [r["id"] for r in out] == [1, 2]

    def test_query_without_number_noop(self):
        rows = [
            {"id": 1, "model": "ASUS PRIME B650M-A", "sku": "P1", "min_price": 150.0},
        ]
        out = fuzzy_lookup.rerank_by_exact_match(rows, query_upper="ASUS PRIME")
        assert out == rows


# -----------------------------------------------------------------------------
# find() — интеграционная проверка с мок-сессией
# -----------------------------------------------------------------------------

def _mk_session(results_per_call: list[list[dict]]) -> MagicMock:
    """Копия фикстуры из test_fuzzy_lookup.py — мок SQLAlchemy-сессии."""
    session = MagicMock()
    calls = {"i": 0}

    def execute(*args, **kwargs):
        i = calls["i"]
        calls["i"] += 1
        rows = results_per_call[i] if i < len(results_per_call) else []
        result = MagicMock()
        mappings = MagicMock()
        mappings.all.return_value = rows
        mappings.first.return_value = rows[0] if rows else None
        result.mappings.return_value = mappings
        return result

    session.execute.side_effect = execute
    return session


class TestFindExactMatch:
    def test_request_12400_picks_non_f_even_if_more_expensive(self):
        """Ключевой сценарий бага: запрос '12400' и в БД есть и 12400, и 12400F,
        причём 12400F дешевле. До фикса выбирался 12400F — а надо 12400."""
        rows = [
            # БД отсортировала по цене: F-версия дешевле, как было в реальности
            {"id": 14, "model": "Intel Core i5-12400F", "sku": "CM-F", "min_price": 150.0},
            {"id": 9,  "model": "Intel Core i5-12400",  "sku": "CM-OK", "min_price": 180.0},
        ]
        session = _mk_session([rows])
        rm = fuzzy_lookup.find(
            session, ModelMention(category="cpu", query="Intel Core i5 12400"),
        )
        assert rm.found_id == 9
        assert rm.found_model == "Intel Core i5-12400"
        assert rm.is_substitute is False
        assert rm.note is None

    def test_request_12400F_picks_the_F_variant(self):
        rows = [
            {"id": 9,  "model": "Intel Core i5-12400",  "sku": "CM-OK", "min_price": 150.0},
            {"id": 14, "model": "Intel Core i5-12400F", "sku": "CM-F", "min_price": 180.0},
        ]
        session = _mk_session([rows])
        rm = fuzzy_lookup.find(
            session, ModelMention(category="cpu", query="Intel Core i5 12400F"),
        )
        assert rm.found_id == 14
        assert rm.found_model == "Intel Core i5-12400F"
        assert rm.is_substitute is False

    def test_request_13400_gets_13400F_as_substitute(self):
        """Если точного совпадения нет совсем (только F-версия в БД),
        берём F-версию и помечаем substitute=True с пояснением."""
        rows = [
            {"id": 3, "model": "Intel Core i5-13400F", "sku": "CM-13F",  "min_price": 170.0},
            {"id": 4, "model": "Intel Core i5-13400F", "sku": "CM-13F2", "min_price": 190.0},
        ]
        session = _mk_session([rows])
        rm = fuzzy_lookup.find(
            session, ModelMention(category="cpu", query="Intel Core i5 13400"),
        )
        assert rm.found_id == 3
        assert rm.is_substitute is True
        assert rm.note is not None
        assert "близкий вариант" in rm.note
