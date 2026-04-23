# Тесты fuzzy_lookup: нормализация запросов и поиск моделей в БД.
#
# Поиск тестируем через мок-сессию, возвращающую заранее подготовленные
# строки. Реальную PG-БД не дёргаем — это ускоряет тесты и убирает
# зависимость от состояния каталога.

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.nlu import fuzzy_lookup
from app.services.nlu.schema import ModelMention


# -----------------------------------------------------------------------------
# normalize_query / pick_model_number
# -----------------------------------------------------------------------------

class TestNormalize:
    def test_ryzen(self):
        assert fuzzy_lookup.normalize_query("Ryzen 5 7600") == ["RYZEN", "5", "7600"]

    def test_intel_core(self):
        # "Core" — стоп-слово, "i5-13400F" нормализуется
        toks = fuzzy_lookup.normalize_query("Core i5-13400F")
        assert toks == ["I5", "13400F"]

    def test_geforce_rtx(self):
        # GEFORCE и NVIDIA удаляются как стоп-слова
        assert fuzzy_lookup.normalize_query("GeForce RTX 4060") == ["RTX", "4060"]

    def test_motherboard(self):
        toks = fuzzy_lookup.normalize_query("ASUS PRIME B650M-A")
        # ASUS не в стоп-листе, B650M-A → ['B650M', 'A']
        assert "ASUS" in toks
        assert "PRIME" in toks
        assert "B650M" in toks

    def test_storage_keeps_ssd(self):
        # Для категории storage SSD — значимый токен
        toks = fuzzy_lookup.normalize_query("Kingston NV2 1TB SSD", category="storage")
        assert "SSD" in toks
        assert "1TB" in toks

    def test_storage_default_drops_ssd(self):
        # Для не-storage SSD удаляется (как стоп-слово категории)
        toks = fuzzy_lookup.normalize_query("Kingston NV2 1TB SSD")
        assert "SSD" not in toks

    def test_empty(self):
        assert fuzzy_lookup.normalize_query("") == []
        assert fuzzy_lookup.normalize_query("   ") == []

    def test_pick_model_number(self):
        assert fuzzy_lookup.pick_model_number(["RYZEN", "5", "7600"]) == "7600"
        assert fuzzy_lookup.pick_model_number(["I5", "13400F"]) == "13400F"
        assert fuzzy_lookup.pick_model_number(["ASUS", "PRIME"]) is None


# -----------------------------------------------------------------------------
# find() с мок-сессией
# -----------------------------------------------------------------------------

def _mk_session(results_per_call: list[list[dict]]) -> MagicMock:
    """Возвращает MagicMock сессии, у которой .execute().mappings().all()
    отдаёт по очереди строки из results_per_call. .first() — первый из них.
    """
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


class TestFind:
    def test_substitute_by_suffix_mismatch(self):
        # Запрос "Ryzen 5 7600" (base=7600, без суффикса), а в БД только
        # "Ryzen 5 7600X OEM" (base=7600, suffix=X) — это аналог, а не точное
        # совпадение. Новое поведение rerank_by_exact_match помечает substitute.
        rows = [{"id": 42, "model": "AMD Ryzen 5 7600X OEM", "sku": "ABC", "min_price": 180.0}]
        session = _mk_session([rows])
        rm = fuzzy_lookup.find(session, ModelMention(category="cpu", query="Ryzen 5 7600"))
        assert rm.found_id == 42
        assert rm.is_substitute is True
        assert rm.note is not None and "близкий вариант" in rm.note

    def test_chooses_cheapest_when_exact_match_exists(self):
        # Два точных совпадения по base+suffix — порядок по цене, substitute=False.
        rows = [
            {"id": 7,  "model": "Ryzen 5 7600 OEM", "sku": "OEM-1", "min_price": 175.0},
            {"id": 8,  "model": "Ryzen 5 7600 BOX", "sku": "BOX-1", "min_price": 200.0},
        ]
        session = _mk_session([rows])
        rm = fuzzy_lookup.find(session, ModelMention(category="cpu", query="Ryzen 5 7600"))
        assert rm.found_id == 7
        assert rm.found_sku == "OEM-1"
        assert rm.is_substitute is False

    def test_substitute_via_model_number(self):
        # Первый поиск (по всем токенам) — пусто; второй (по номеру) — что-то нашёл
        rows_substitute = [
            {"id": 99, "model": "Palit RTX 4060 Ti EVO", "sku": "P-4060TI", "min_price": 320.0},
        ]
        session = _mk_session([[], rows_substitute])
        rm = fuzzy_lookup.find(session, ModelMention(category="gpu", query="RTX 4060"))
        assert rm.found_id == 99
        assert rm.is_substitute is True
        assert rm.note is not None
        assert "близкий вариант" in rm.note

    def test_not_found_at_all(self):
        # И полный поиск, и поиск по номеру — пусто
        session = _mk_session([[], []])
        rm = fuzzy_lookup.find(session, ModelMention(category="gpu", query="RTX 9999"))
        assert rm.found_id is None
        assert rm.is_substitute is False
        assert rm.note and "не найдена" in rm.note

    def test_empty_query_after_normalize(self):
        # "GeForce RTX" без номера → стоп-слова съели всё, остаётся ['RTX']
        session = MagicMock()  # не должен вызываться: пусто после нормализации?
        # Но 'RTX' остаётся — поэтому на самом деле нормализация не пуста.
        # Возьмём полностью «пустой» случай: только стоп-слова.
        session = _mk_session([])  # ничего не вернёт
        rm = fuzzy_lookup.find(session, ModelMention(category="cpu", query="Core"))
        assert rm.found_id is None
        assert rm.note and "общее" in rm.note.lower()

    def test_unknown_category_raises(self):
        with pytest.raises(ValueError):
            fuzzy_lookup.find(_mk_session([]), ModelMention(category="wifi", query="x"))
