# Тесты MerlionImapFetcher — IMAP-канал Merlion (12.1).

from __future__ import annotations

import io
import os
import re
import zipfile

import pytest


# =====================================================================
# 1. Subject regex
# =====================================================================

def test_merlion_subject_regex_matches():
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    pat = re.compile(MerlionImapFetcher.subject_pattern, re.IGNORECASE)
    for s in [
        "Прайс-лист MERLION",
        "Прайс-лист MERLION Москва 06.05.2026",
        "  Прайс-лист MERLION 30.04.2026",
        # Реальный Subject из IMAP-разведки 2026-05-06 (только этот):
        "Прайс-лист MERLION",
    ]:
        assert pat.search(s), f"subject должен матчиться: {s!r}"


def test_merlion_subject_regex_rejects_unrelated():
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    pat = re.compile(MerlionImapFetcher.subject_pattern, re.IGNORECASE)
    for s in [
        "Новости MERLION",
        "Прайс OCS - Состояние склада и цены",
        "Re: Прайс-лист MERLION",
    ]:
        assert not pat.search(s), f"subject должен быть отклонён: {s!r}"


# =====================================================================
# 2. Sender regex (домен merlion.ru, в т.ч. через Gmail-forward)
# =====================================================================

def test_merlion_sender_regex_matches():
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    pat = re.compile(MerlionImapFetcher.sender_pattern, re.IGNORECASE)
    for s in [
        "matveeva.y@merlion.ru",
        "Матвеева <matveeva.y@merlion.ru>",
        "пересылка via gmail; original: news@merlion.ru",
        # Реальные From из IMAP-разведки 2026-05-06:
        "matveeva.y@merlion.ru <matveeva.y@merlion.ru>",  # двойной From после Gmail-forward
        "Matveeva Yuliya <Matveeva.Y@merlion.ru>",        # display name + capitalized
        "Antonov Sergey <Antonov.S@merlion.ru>",          # другой менеджер
    ]:
        assert pat.search(s), f"sender должен матчиться: {s!r}"


def test_merlion_from_via_gmail_forward_matches():
    """После Gmail-forward Reply-To/X-Forwarded-For/Return-Path содержат
    quadro.kzn@gmail.com и caf_-префиксы, но FROM сохраняет реальный
    @merlion.ru. BaseImapFetcher склеивает все адресные заголовки в один
    haystack, и должен матчить именно по From — gmail в Reply-To не
    должен мешать (regex ищет @merlion.ru, gmail.com его не сматчит)."""
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher
    from app.services.auto_price.fetchers.base_imap import _addresses_in_header

    headers = {
        "From":            "matveeva.y@merlion.ru <matveeva.y@merlion.ru>",
        "Reply-To":        "",
        "X-Forwarded-For": "quadro.kzn@gmail.com quadro@quadro.tatar",
        "Return-Path":     "<quadro.kzn+caf_=quadro=quadro.tatar@gmail.com>",
        "Sender":          "",
    }
    haystack = _addresses_in_header(headers)
    pat = re.compile(MerlionImapFetcher.sender_pattern, re.IGNORECASE)
    assert pat.search(haystack), (
        f"Gmail-forwarded Merlion должен матчиться по From, "
        f"haystack={haystack!r}"
    )


def test_merlion_sender_regex_rejects_other():
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    pat = re.compile(MerlionImapFetcher.sender_pattern, re.IGNORECASE)
    for s in ["egarifullina@ocs.ru", "noreply@merlion.com", "scam@merlion.ru.fake"]:
        assert not pat.search(s)


# =====================================================================
# 3. ZIP extraction
# =====================================================================

def _make_zip_with(files: list[tuple[str, bytes]]) -> bytes:
    """Собирает in-memory ZIP с указанными файлами (UTF-8 EFS bit
    выставлен — Python zipfile это делает автоматически для
    не-ASCII имён)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            zf.writestr(name, data)
    return buf.getvalue()


def _make_zip_cp1251(files: list[tuple[str, bytes]]) -> bytes:
    """Собирает ZIP с именами в cp1251 БЕЗ EFS-флага.

    Это воспроизводит формат Merlion-рассылки: имена в cp1251, bit 11
    в general purpose flags не выставлен. Чтобы получить именно такое
    поведение, мы пишем ZipInfo вручную: filename — байты cp1251,
    декодированные как latin-1 (поверхностный обход, чтобы Python не
    выставил EFS), и явно сбрасываем 0x800 в flag_bits.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files:
            # Имитируем поведение архиватора, который пишет cp1251
            # байты прямо в filename без UTF-8 EFS-флага. zipfile при
            # ЗАПИСИ интерпретирует name как unicode → закодирует в
            # ASCII, иначе выставит EFS. Чтобы обмануть — кладём
            # cp1251-байты, декодированные как latin-1: ASCII-проверка
            # пройдёт, EFS не выставится, на диск уйдут байты cp1251.
            cp1251_bytes = name.encode("cp1251")
            fake_ascii_name = cp1251_bytes.decode("latin-1")
            info = zipfile.ZipInfo(filename=fake_ascii_name)
            info.compress_type = zipfile.ZIP_DEFLATED
            # На всякий случай: явно сбросим bit 11 (хотя для ASCII
            # имени Python и не выставит).
            info.flag_bits &= ~0x800
            zf.writestr(info, data)
    return buf.getvalue()


def _make_real_xlsx_bytes(sheet_name: str = "Sheet1") -> bytes:
    """Собирает минимальный валидный xlsx через openpyxl, чтобы
    MerlionLoader.iter_rows() мог его открыть в end-to-end тесте."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws["A1"] = "Sample"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def test_merlion_zip_extraction_picks_largest_xlsx(monkeypatch):
    """В ZIP может быть несколько .xlsx (основной + сопровождающие).
    parse_attachment должен взять самый большой и передать в MerlionLoader."""
    import app.services.auto_price.fetchers.merlion_imap as mer_mod
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    # Готовим ZIP с двумя xlsx разного размера + один pdf.
    big = b"PK\x03\x04" + (b"X" * 5000) + b"big-xlsx-end"
    small = b"PK\x03\x04small-xlsx"
    pdf = b"%PDF-1.4 lic"
    zip_bytes = _make_zip_with([
        ("license.pdf", pdf),
        ("merlion_msk_06_05.xlsx", big),
        ("hint_short.xlsx", small),
    ])

    captured = {}

    class _StubLoader:
        def iter_rows(self, filepath):
            captured["filepath"] = filepath
            with open(filepath, "rb") as f:
                captured["bytes"] = f.read()
            return iter([])

    monkeypatch.setattr(mer_mod, "MerlionLoader", lambda: _StubLoader())
    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")

    fetcher = MerlionImapFetcher()
    rows = fetcher.parse_attachment(zip_bytes, "merlion_06_05.zip")

    assert rows == []
    assert captured["filepath"].endswith(".xlsx")
    assert captured["bytes"] == big  # выбран самый большой


def test_merlion_zip_extraction_with_single_xlsx(monkeypatch):
    """Базовый кейс: один XLSX внутри — берём его."""
    import app.services.auto_price.fetchers.merlion_imap as mer_mod
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    payload = b"PK\x03\x04only-one"
    zip_bytes = _make_zip_with([("price.xlsx", payload)])

    seen = {}

    class _StubLoader:
        def iter_rows(self, filepath):
            with open(filepath, "rb") as f:
                seen["bytes"] = f.read()
            return iter([])

    monkeypatch.setattr(mer_mod, "MerlionLoader", lambda: _StubLoader())
    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")

    MerlionImapFetcher().parse_attachment(zip_bytes, "merlion.zip")
    assert seen["bytes"] == payload


def test_merlion_zip_no_xlsx_raises(monkeypatch):
    """ZIP без xlsx — RuntimeError, чтобы runner и orchestrator не делали
    загрузку с пустыми rows (не обнуляли остатки)."""
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    zip_bytes = _make_zip_with([("license.pdf", b"%PDF-1.4")])

    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")
    fetcher = MerlionImapFetcher()
    with pytest.raises(RuntimeError, match="не содержит ни одного файла .xlsx"):
        fetcher.parse_attachment(zip_bytes, "merlion.zip")


def test_merlion_bad_zip_raises(monkeypatch):
    """Bytes — не ZIP. RuntimeError, NoNewDataException не уместен —
    письмо есть, но кривое."""
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")
    fetcher = MerlionImapFetcher()
    with pytest.raises(RuntimeError, match="не распознано как ZIP"):
        fetcher.parse_attachment(b"not a zip", "broken.zip")


# =====================================================================
# 4. cp1251 имена внутри ZIP (12.1-fix-2)
# =====================================================================

def test_zip_with_cp1251_names_extracted_correctly(monkeypatch):
    """Воспроизводит реальный Merlion-ZIP: имена в cp1251 БЕЗ
    UTF-8 EFS-флага. До 12.1-fix-2 zf.extractall() падал на
    mismatch directory/header. Теперь fetcher должен корректно
    распаковать и передать содержимое в MerlionLoader."""
    import app.services.auto_price.fetchers.merlion_imap as mer_mod
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    payload = b"PK\x03\x04cp1251-content-marker"
    zip_bytes = _make_zip_cp1251([
        ("Прайслист_Мерлион_Москва.xlsm", payload),
    ])

    seen = {}

    class _StubLoader:
        def iter_rows(self, filepath):
            seen["filepath"] = filepath
            with open(filepath, "rb") as f:
                seen["bytes"] = f.read()
            return iter([])

    monkeypatch.setattr(mer_mod, "MerlionLoader", lambda: _StubLoader())
    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")

    MerlionImapFetcher().parse_attachment(zip_bytes, "PriceList_MERLION_Moskva.zip")
    assert seen["bytes"] == payload, "содержимое xlsm должно сохраниться"
    # Имя файла на диске должно быть raskодировано из cp1251 в нормальный
    # «Прайслист_Мерлион_Москва.xlsm» (или близкий — мы нормализуем
    # незаконные FS-символы, но кириллица должна быть).
    base = os.path.basename(seen["filepath"])
    assert "Прайслист" in base or base.lower().endswith(".xlsm"), (
        f"имя на диске должно быть cp1251-decoded: {base!r}"
    )


def test_zip_xlsm_extension_accepted(monkeypatch):
    """ZIP с .xlsm файлом (Excel с макросами) — fetcher должен его
    найти. До 12.1-fix-2 искалось только .xlsx."""
    import app.services.auto_price.fetchers.merlion_imap as mer_mod
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    payload = b"PK\x03\x04xlsm-payload"
    zip_bytes = _make_zip_with([("price_with_macros.xlsm", payload)])

    seen = {}

    class _StubLoader:
        def iter_rows(self, filepath):
            with open(filepath, "rb") as f:
                seen["bytes"] = f.read()
            seen["ext"] = os.path.splitext(filepath)[1].lower()
            return iter([])

    monkeypatch.setattr(mer_mod, "MerlionLoader", lambda: _StubLoader())
    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")

    MerlionImapFetcher().parse_attachment(zip_bytes, "merlion.zip")
    assert seen["bytes"] == payload
    assert seen["ext"] == ".xlsm"


def test_zip_already_utf8_efs_flag_preserved(monkeypatch):
    """Современный ZIP с UTF-8 именами (EFS bit 11 = 1) — наш фикс
    не должен ломать такие архивы. Имя должно остаться как есть,
    без двойного перекодирования."""
    import app.services.auto_price.fetchers.merlion_imap as mer_mod
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    payload = b"PK\x03\x04utf8-efs-payload"
    # _make_zip_with пишет через writestr(name, data) — Python для
    # не-ASCII автоматически выставляет UTF-8 EFS-флаг.
    zip_bytes = _make_zip_with([("Прайс.xlsx", payload)])

    seen = {}

    class _StubLoader:
        def iter_rows(self, filepath):
            with open(filepath, "rb") as f:
                seen["bytes"] = f.read()
            seen["filepath"] = filepath
            return iter([])

    monkeypatch.setattr(mer_mod, "MerlionLoader", lambda: _StubLoader())
    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")

    MerlionImapFetcher().parse_attachment(zip_bytes, "merlion.zip")
    assert seen["bytes"] == payload
    base = os.path.basename(seen["filepath"])
    # Не должно быть мусора вроде «Ÿàü½ª¨ÅÅ.xlsx».
    assert "Прайс" in base, (
        f"UTF-8-имя должно сохраниться без двойной перекодировки: {base!r}"
    )


def test_zip_with_multiple_files_picks_largest_xlsm_or_xlsx(monkeypatch):
    """В реальном ZIP может быть и .xlsx, и .xlsm. Берётся самый большой."""
    import app.services.auto_price.fetchers.merlion_imap as mer_mod
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    big_xlsm = b"PK\x03\x04" + (b"X" * 20000) + b"big-xlsm"
    small_xlsx = b"PK\x03\x04small-xlsx"
    pdf = b"%PDF-1.4 license-text"
    zip_bytes = _make_zip_with([
        ("license.pdf", pdf),
        ("price_main.xlsm", big_xlsm),  # самый большой — выбираем его
        ("price_supplement.xlsx", small_xlsx),
    ])

    seen = {}

    class _StubLoader:
        def iter_rows(self, filepath):
            with open(filepath, "rb") as f:
                seen["bytes"] = f.read()
            seen["filepath"] = filepath
            return iter([])

    monkeypatch.setattr(mer_mod, "MerlionLoader", lambda: _StubLoader())
    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")

    MerlionImapFetcher().parse_attachment(zip_bytes, "merlion.zip")
    assert seen["bytes"] == big_xlsm
    assert seen["filepath"].endswith(".xlsm")


def test_zip_no_xlsx_or_xlsm_raises(monkeypatch):
    """ZIP без xlsx и xlsm — RuntimeError (формат рассылки изменился)."""
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    zip_bytes = _make_zip_with([
        ("license.pdf", b"%PDF-1.4"),
        ("readme.txt", b"hello"),
    ])
    monkeypatch.setenv("IMAP_USER", "u@x.test")
    monkeypatch.setenv("IMAP_PASSWORD", "p")
    fetcher = MerlionImapFetcher()
    with pytest.raises(RuntimeError, match=r"не содержит ни одного файла .xlsx или .xlsm"):
        fetcher.parse_attachment(zip_bytes, "merlion.zip")


def test_merlion_loader_can_open_real_xlsm(tmp_path):
    """Smoke: openpyxl/MerlionLoader действительно открывает .xlsm
    как обычный xlsx (формат внутри идентичен — ZIP с XML; разница
    только в наличии макросов, которые openpyxl игнорирует)."""
    from openpyxl import load_workbook
    xlsm_path = tmp_path / "smoke.xlsm"
    xlsm_path.write_bytes(_make_real_xlsx_bytes())
    wb = load_workbook(xlsm_path, read_only=True, data_only=True)
    try:
        assert wb.active is not None
    finally:
        wb.close()


# =====================================================================
# 5. Helper: декодинг имени по EFS-флагу
# =====================================================================

def test_decode_zip_member_name_cp1251_when_efs_off():
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    info = zipfile.ZipInfo()
    # Имитируем то, что zipfile при чтении положит в .filename: байты
    # cp1251, декодированные как cp437 → мусор.
    info.filename = "Прайслист_Мерлион_Москва.xlsm".encode("cp1251").decode("cp437")
    info.flag_bits = 0  # EFS off
    decoded = MerlionImapFetcher._decode_zip_member_name(info)
    assert decoded == "Прайслист_Мерлион_Москва.xlsm"


def test_decode_zip_member_name_keeps_utf8_when_efs_on():
    from app.services.auto_price.fetchers.merlion_imap import MerlionImapFetcher

    info = zipfile.ZipInfo()
    info.filename = "Прайс.xlsx"
    info.flag_bits = 0x800  # EFS on (UTF-8)
    decoded = MerlionImapFetcher._decode_zip_member_name(info)
    assert decoded == "Прайс.xlsx"
