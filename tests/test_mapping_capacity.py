# Юнит-тесты capacity-check для mapping_service (этап 7.3).
#
# На этапе 7.2 токены объёма/частоты/мощности были исключены из
# значимых — они перестали давать ложные +50 за общий размер у разных
# моделей. Но обратной проверки («разный объём → разные модели») не
# было: SSD 1TB и SSD 512GB одного бренда получали score=100 и оседали
# в «подозрительных».
#
# Этот модуль проверяет, что _capacity_mismatch корректно ловит такие
# пары, а _score_breakdown штрафует их до ≤ 30 — но только в категориях,
# где размерность действительно различает модели (storage/ram/gpu/psu/
# cooler). Для cpu/motherboard/case проверка должна быть пассивной.

from __future__ import annotations

from app.services.mapping_service import (
    _capacity_mismatch,
    _extract_capacities,
    _score_breakdown,
)


# ---- _capacity_mismatch -----------------------------------------------


def test_capacity_mismatch_storage():
    """SSD разного объёма одной серии — mismatch срабатывает."""
    assert _capacity_mismatch(
        "Apacer SSD PANTHER AS350X 2TB",
        "Apacer SSD PANTHER AS350X 1TB",
    ) is True


def test_capacity_match_storage():
    """SSD одного объёма — mismatch не срабатывает."""
    assert _capacity_mismatch(
        "Apacer SSD PANTHER AS350X 1TB",
        "Apacer SSD PANTHER AS350X 1TB",
    ) is False


def test_capacity_mismatch_ram():
    """DDR4 16GB vs 8GB — mismatch срабатывает даже при совпавшей частоте."""
    assert _capacity_mismatch(
        "Kingston DDR4 16GB 3200MHz",
        "Kingston DDR4 8GB 3200MHz",
    ) is True


def test_capacity_mismatch_psu():
    """БП 450W vs 650W — mismatch срабатывает по группе power."""
    assert _capacity_mismatch(
        "DeepCool PF450 450W",
        "DeepCool PF650 650W",
    ) is True


def test_capacity_unit_normalization():
    """«2TB» и «2000GB» — одно и то же после десятичной нормализации."""
    assert _capacity_mismatch(
        "Samsung SSD 2TB",
        "Samsung SSD 2000GB",
    ) is False


def test_capacity_tolerance_1tb_vs_1024gb():
    """1TB = 1000GB, 1024GB — маркетологи называют одно и то же
    по-разному. Допуск 5 % должен поглотить разницу 2.4 %."""
    assert _capacity_mismatch(
        "WD SSD Blue 1TB",
        "WD SSD Blue 1024GB",
    ) is False


def test_no_capacity_in_one():
    """Метка есть только в одном имени — это может быть сокращение,
    не штрафуем."""
    assert _capacity_mismatch(
        "Kingston DDR4 16GB 3200MHz",
        "Kingston DDR4 Value",
    ) is False


def test_capacity_mismatch_ssd_with_noise():
    """Продакшен-кейс (этап 7.3 fix): Crucial BX500 на 500GB vs 240GB
    с шумом «540 MB/s Read 500 MB/s Write» в обоих именах. Раньше
    скорости MB/s попадали в группу size и совпадали с объёмами,
    гася mismatch. После фикса MB/s исключаются регуляркой, а сравнение
    идёт по максимумам — объём различается и cap=30 срабатывает."""
    a = ("Crucial SSD Disk BX500 500GB SATA 2.5 7mm SSD "
         "(540 MB/s Read 500 MB/s Write), 1 Year Warranty OEM")
    b = ("Crucial SSD Disk BX500 240GB SATA 2.5 7mm SSD "
         "(540 MB/s Read 500 MB/s Write), 1 Year Warranty OEM")
    assert _capacity_mismatch(a, b) is True

    # И через полный score: обе строки одного бренда и серии,
    # отличаются только объёмом — score должен быть ≤ 30.
    score, reason = _score_breakdown(
        raw_name=a, brand="Crucial",
        cand={"model": b, "manufacturer": "Crucial"},
        category="storage",
    )
    assert score <= 30, f"ожидали ≤ 30, получили {score} ({reason})"


def test_capacity_ignore_speed_mb_per_s():
    """«540 MB/s» — это скорость, не объём. Регулярка такое игнорирует,
    _extract_capacities возвращает пустой dict."""
    assert _extract_capacities("540 MB/s") == {}
    assert _extract_capacities("SSD 540MB/s Read 500 MB/s Write") == {}
    # А «500GB» в той же строке — ловится штатно.
    caps = _extract_capacities("SSD 500GB 540 MB/s Read")
    assert caps.get("size") == {500.0}
    # И русские/английские ключевые слова тоже исключают метку:
    assert _extract_capacities("540 MB Read") == {}
    assert _extract_capacities("540 MB чтение") == {}
    assert _extract_capacities("540 MB per second") == {}


def test_capacity_match_with_noise():
    """Объём совпадает (500GB в обеих строках), но «шумовые» скорости
    разные (540 MB/s vs 530 MB/s). Mismatch не должен срабатывать —
    MB/s не попадает в size-метки, а максимум объёма одинаковый."""
    assert _capacity_mismatch(
        "Samsung SSD Pro 500GB (540 MB/s Read)",
        "Samsung SSD Pro 500GB (530 MB/s Read)",
    ) is False


def test_capacity_mhz_normalization():
    """1GHz = 1000MHz — если в одной «3200MHZ», в другой «3.2GHZ»,
    они считаются одинаковыми."""
    assert _capacity_mismatch(
        "Kingston RAM 16GB 3200MHZ",
        "Kingston RAM 16GB 3.2GHZ",
    ) is False


# ---- _extract_capacities ----------------------------------------------


def test_extract_capacities_multiple_groups():
    """Функция возвращает значения, нормализованные к базовой единице
    каждой группы (GB / MHz / W)."""
    caps = _extract_capacities("Kingston DDR4 16GB 3200MHz")
    assert caps.get("size") == {16.0}
    assert caps.get("freq") == {3200.0}


def test_extract_capacities_empty():
    """Имя без меток — пустой dict."""
    assert _extract_capacities("Intel Core i5-12400") == {}


# ---- _score_breakdown + category --------------------------------------


def test_score_breakdown_storage_capacity_mismatch_caps_at_30():
    """SSD 1TB vs 512GB одного бренда и серии — score ≤ 30."""
    score, reason = _score_breakdown(
        raw_name="Apacer SSD PANTHER AS350X 1TB",
        brand="Apacer",
        cand={"model": "Apacer SSD PANTHER AS350X 512GB",
              "manufacturer": "Apacer"},
        category="storage",
    )
    assert score <= 30, f"ожидали ≤ 30, получили {score} ({reason})"
    assert "несовпадение объёма" in reason


def test_score_breakdown_storage_capacity_match_keeps_high():
    """SSD 1TB vs 1TB одного бренда и серии — score НЕ капнут до 30.

    Без MPN fallback упирается в _SCORE_FALLBACK_CAP (70) — важно лишь,
    что capacity-cap (30) не сработал, значит объёмы признаны равными.
    """
    score, _reason = _score_breakdown(
        raw_name="Apacer SSD PANTHER AS350X 1TB",
        brand="Apacer",
        cand={"model": "Apacer SSD PANTHER AS350X 1TB",
              "manufacturer": "Apacer"},
        category="storage",
    )
    assert score >= 50, f"ожидали ≥ 50 (capacity-cap не должен был сработать), получили {score}"


def test_score_breakdown_ram_capacity_mismatch_caps_at_30():
    """DDR4 16GB vs 8GB — score ≤ 30, даже если совпала частота."""
    score, reason = _score_breakdown(
        raw_name="Kingston ValueRAM DDR4 16GB 3200MHz",
        brand="Kingston",
        cand={"model": "Kingston ValueRAM DDR4 8GB 3200MHz",
              "manufacturer": "Kingston"},
        category="ram",
    )
    assert score <= 30, f"ожидали ≤ 30, получили {score} ({reason})"


def test_score_breakdown_psu_capacity_mismatch_caps_at_30():
    """БП 450W vs 650W — score ≤ 30."""
    score, _reason = _score_breakdown(
        raw_name="DeepCool PF450 450W",
        brand="DeepCool",
        cand={"model": "DeepCool PF650 650W",
              "manufacturer": "DeepCool"},
        category="psu",
    )
    assert score <= 30


def test_score_breakdown_gpu_vram_mismatch_caps_at_30():
    """Видеокарта 8GB vs 12GB одной модели — score ≤ 30."""
    score, _reason = _score_breakdown(
        raw_name="Palit RTX 4060 Ti 8GB",
        brand="Palit",
        cand={"model": "Palit RTX 4060 Ti 12GB",
              "manufacturer": "Palit"},
        category="gpu",
    )
    assert score <= 30


def test_score_breakdown_no_capacity_in_candidate_no_penalty():
    """В имени A есть 1TB, в модели B нет объёма — штрафа нет."""
    score, _reason = _score_breakdown(
        raw_name="Apacer SSD PANTHER AS350X 1TB",
        brand="Apacer",
        cand={"model": "Apacer SSD PANTHER AS350X",
              "manufacturer": "Apacer"},
        category="storage",
    )
    # Совпадение бренда + lev (почти идентичные имена) + токен серии →
    # fallback даёт 100, но без MPN капается до 70. Важно, что capacity-cap
    # (30) не сработал, т. е. отсутствие объёма у кандидата не считается
    # «несовпадением».
    assert score >= 50, f"ожидали ≥ 50 (capacity-cap не должен был сработать), получили {score}"


def test_score_breakdown_motherboard_capacity_check_disabled():
    """Для motherboards проверка capacity отключена — упоминание
    «DDR5 6400MHz» в имени платы не должно резать score, даже если
    у кандидата другая частота. Без MPN fallback упирается в 70."""
    score, _reason = _score_breakdown(
        raw_name="ASUS PRIME B760-M DDR5 6400MHz",
        brand="ASUS",
        cand={"model": "ASUS PRIME B760-M DDR5 6400MHz",
              "manufacturer": "ASUS"},
        category="motherboard",
    )
    assert score >= 50, f"ожидали ≥ 50 (capacity-cap не должен был сработать), получили {score}"


def test_score_breakdown_unit_normalization_keeps_match():
    """Кандидат в GB, запись — в TB: значения должны нормализоваться
    и считаться одинаковыми (mismatch не сработает, cap=30 не применится).

    Используем имена со значимым общим токеном (MZ77E2TB / MZ77E2000GB
    в стиле SKU), чтобы итоговый score был заведомо выше 30 — так
    разница между «нормализация сработала» (score ≥ 50) и «mismatch
    капнул» (score ≤ 30) видна однозначно.
    """
    score, _reason = _score_breakdown(
        raw_name="Samsung 870 EVO MZ77E500 2TB",
        brand="Samsung",
        cand={"model": "Samsung 870 EVO MZ77E500 2000GB",
              "manufacturer": "Samsung"},
        category="storage",
    )
    # Если бы нормализация не сработала — mismatch капнул бы score до 30.
    # Общий токен MZ77E500 даёт +50, бренд +30 → ожидаем ≥ 50.
    assert score >= 50, f"ожидали ≥ 50 (нормализация должна удержать высокий score), получили {score}"
