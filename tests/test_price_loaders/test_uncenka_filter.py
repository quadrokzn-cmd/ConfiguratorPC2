"""Тесты regex-фильтра уценок/повреждений/refurb/б-у/восстановленных.

Контракт: `is_uncenka(name)` возвращает True, если в имени позиции
есть маркер дисконта/повторной продажи (которая для аукционов 44-ФЗ
неприменима — нельзя заявить новый товар с уценкой).

Покрытие:
  - позитив на каждый паттерн (см. UNCENKA_PATTERNS в модуле);
  - реальные имена с pre-prod kvadro_tech (G&G, Pantum, HP);
  - негатив на нормальные имена прайсов;
  - edge: None / пустая строка / разные регистры / латиница vs кириллица.
"""

from __future__ import annotations

import pytest

from app.services.price_loaders.uncenka_filter import is_uncenka


# ---- POSITIVE: уценка в разных формах ----------------------------------

@pytest.mark.parametrize("name", [
    "Принтер X (уценка)",
    "Уценка: Pantum P2500W",
    "HP LJ M428 уценку выкупают",
    "HP LJ M428 уценкой",
    "Pantum BM5100 уценённый",
    "Pantum BM5100 уценённая упаковка",
    "Pantum BM5100 уцененный",
    "HP LJ M428 (Уцененный товар)",
])
def test_uncenka_keyword_positive(name):
    assert is_uncenka(name), f"Должно ловить уценку в: {name!r}"


# ---- POSITIVE: повреждение / коробка / упаковка ------------------------

@pytest.mark.parametrize("name", [
    "G&G P2022W, незначительное повреждение коробки",
    "Принтер HP LaserJet поврежденный",
    "Принтер HP LaserJet повреждённый",
    "Принтер HP LaserJet повреждена упаковка",
    "Принтер HP LaserJet (повр. коробки)",
    "МФУ Pantum (повр.коробк)",
    "МФУ Pantum повр упаковки",
])
def test_damaged_box_positive(name):
    assert is_uncenka(name), f"Должно ловить повреждение в: {name!r}"


# ---- POSITIVE: б/у --------------------------------------------------------

@pytest.mark.parametrize("name", [
    "Принтер HP б/у",
    "Принтер HP б\\у",
    "Принтер HP б-у",
    "Принтер HP б у",
    "Принтер HP бу",
    "БУ принтер HP",
    "Б/У МФУ Pantum",
])
def test_bu_positive(name):
    assert is_uncenka(name), f"Должно ловить б/у в: {name!r}"


# ---- POSITIVE: refurb / recond / восстановленный -----------------------

@pytest.mark.parametrize("name", [
    "Refurbished HP LaserJet 1320",
    "HP LaserJet 1320 (Refurb)",
    "Reconditioned HP LJ",
    "HP LJ recond.",
    "Принтер HP LaserJet восстановленный",
    "Принтер HP LaserJet восстановлен",
    "Принтер HP восстановл.",
    "Восстан. принтер HP",
])
def test_refurb_positive(name):
    assert is_uncenka(name), f"Должно ловить refurb в: {name!r}"


# ---- POSITIVE: выставочный/витринный образец --------------------------

@pytest.mark.parametrize("name", [
    "Принтер HP LaserJet (выставочный образец)",
    "Витринный экземпляр HP LJ",
    "HP LJ M428 — витринная модель",
])
def test_demo_positive(name):
    assert is_uncenka(name), f"Должно ловить выставочный/витринный в: {name!r}"


# ---- POSITIVE: open box / OS ------------------------------------------

@pytest.mark.parametrize("name", [
    "HP LJ Open Box",
    "HP LJ openbox",
    "HP LJ Open  box",  # двойной пробел
    "HP LaserJet OS",
])
def test_openbox_positive(name):
    assert is_uncenka(name), f"Должно ловить openbox/OS в: {name!r}"


# ---- NEGATIVE: нормальные имена прайсов --------------------------------

@pytest.mark.parametrize("name", [
    "Принтер лазерный HP LaserJet Pro M428fdn",
    "МФУ Pantum BM5100ADN A4 33 ppm USB Wi-Fi",
    "Pantum P2500W, Mono laser, A4, 22 ppm, USB, Wi-Fi",
    "Brother HL-L2375DW лазерный",
    "Canon imageRUNNER 2425i, 25 ppm",
    "Kyocera ECOSYS P3145dn (новый, 1500 стр.)",
    "Картридж HP CF259A (без принтера)",
    "Принтер Bulat P1024W, ч/б, A4",
    # Нет ни одного маркера — пустые/нейтральные слова.
    "Sindoh N512 МФУ",
])
def test_normal_names_negative(name):
    assert not is_uncenka(name), f"FP на нормальном имени: {name!r}"


# ---- EDGE: None / пустая строка ---------------------------------------

@pytest.mark.parametrize("name", [None, "", "   "])
def test_empty_or_none_returns_false(name):
    assert is_uncenka(name) is False


# ---- EDGE: регистронезависимость ---------------------------------------

@pytest.mark.parametrize("name", [
    "ПРИНТЕР HP (УЦЕНКА)",
    "Принтер HP REFURBISHED",
    "Принтер HP Б/У",
    "ПРИНТЕР HP RECOND.",
])
def test_case_insensitive(name):
    assert is_uncenka(name)


# ---- EDGE: «бу» как часть слова не ловится -----------------------------

@pytest.mark.parametrize("name", [
    "автобус для перевозки",  # «бус» — не «бу»
    "Картридж HP бухгалтерия отдел продаж",  # «бух» — не «бу»
    "Бумага А4 Brother",  # «Бум» — не «бу»
])
def test_bu_word_boundary(name):
    """\\bбу\\b требует границы слова: «автобус», «бухгалтерия», «бумага»
    не должны срабатывать."""
    assert not is_uncenka(name), f"FP на не-бу слове: {name!r}"


# ---- EDGE: латинская «os» как часть слова не ловится -------------------

@pytest.mark.parametrize("name", [
    "OSMO Action 4",
    "Pantum BOSS series",
    # «ОС» кириллица — другие коды, не должна срабатывать.
    "Принтер HP с поддержкой ОС Windows",
])
def test_os_word_boundary(name):
    assert not is_uncenka(name), f"FP на не-os слове: {name!r}"
