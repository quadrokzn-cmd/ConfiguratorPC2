# Smoke-тесты для новой карточной раскладки /query/{id} (этап 6.1).
#
# Эти тесты — "живой" рендер через TestClient: мокают process_query,
# вставляют реальные компоненты в БД (чтобы enrich_variants_with_specs
# подтянул specs_short), ходят на /query/{id} и проверяют, что:
#   - карточная сетка собрана правильными классами;
#   - строка specs_short реально попала в HTML;
#   - блок «Предупреждения по сборке» с жёлтой рамкой рендерится
#     при наличии warnings;
#   - два варианта (Intel + AMD) идут друг под другом.
#
# HTML дополнительно сохраняется в VISUAL_DUMP_DIR (если переменная
# окружения VISUAL_DUMP=1) — удобно для ручного просмотра после прогона.
# По умолчанию файлы не пишутся, чтобы не мусорить в репозитории.

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import text as _t

from portal.services.configurator.engine.schema import (
    BuildRequest, BuildResult, ComponentChoice, SupplierOffer, Variant,
)
from portal.services.configurator.nlu.schema import FinalResponse, ParsedRequest
from tests.test_portal.conftest import extract_csrf


VISUAL_DUMP_DIR = Path(__file__).resolve().parents[2] / "visual_samples"


def _dump_html(name: str, html: str) -> None:
    if os.environ.get("VISUAL_DUMP") != "1":
        return
    VISUAL_DUMP_DIR.mkdir(exist_ok=True)
    (VISUAL_DUMP_DIR / f"{name}.html").write_text(html, encoding="utf-8")


# --------------- Общие хелперы для подготовки данных ---------------------

def _insert_cpu(session, model, mfr, cores=None, threads=None,
                base=None, turbo=None, socket=None) -> int:
    return session.execute(_t(
        "INSERT INTO cpus (model, manufacturer, socket, cores, threads, "
        "  base_clock_ghz, turbo_clock_ghz) "
        "VALUES (:m, :mfr, :sock, :c, :t, :b, :tu) RETURNING id"
    ), {"m": model, "mfr": mfr, "sock": socket, "c": cores, "t": threads,
        "b": base, "tu": turbo}).first().id


def _insert_gpu(session, model, mfr, vram_gb=None, vram_type=None, tdp=None) -> int:
    return session.execute(_t(
        "INSERT INTO gpus (model, manufacturer, vram_gb, vram_type, tdp_watts) "
        "VALUES (:m, :mfr, :v, :vt, :tdp) RETURNING id"
    ), {"m": model, "mfr": mfr, "v": vram_gb, "vt": vram_type,
        "tdp": tdp}).first().id


def _insert_ram(session, model, mfr, size_gb=None, count=None,
                mem_type=None, freq=None) -> int:
    return session.execute(_t(
        "INSERT INTO rams (model, manufacturer, memory_type, form_factor, "
        "  module_size_gb, modules_count, frequency_mhz) "
        "VALUES (:m, :mfr, :mt, 'DIMM', :s, :c, :f) RETURNING id"
    ), {"m": model, "mfr": mfr, "mt": mem_type, "s": size_gb,
        "c": count, "f": freq}).first().id


def _component(cat, cid, model, supplier, price_usd, price_rub,
               supplier_sku=None, in_transit=False, mfr="Test") -> ComponentChoice:
    return ComponentChoice(
        category=cat,
        component_id=cid,
        model=model,
        sku=f"SKU-{cat.upper()}-{cid}",
        manufacturer=mfr,
        chosen=SupplierOffer(
            supplier=supplier,
            price_usd=price_usd,
            price_rub=price_rub,
            stock=5,
            in_transit=in_transit,
            supplier_sku=supplier_sku,
        ),
    )


def _submit_and_get_result(client, raw_text: str) -> tuple[int, str]:
    """POST /query → получаем qid → GET /query/{qid} → html."""
    r = client.get("/configurator/")
    token = extract_csrf(r.text)
    r = client.post("/configurator/query", data={
        "project_name": "Визуальный тест",
        "raw_text":     raw_text,
        "csrf_token":   token,
    })
    assert r.status_code == 302, r.text[:200]
    from tests.test_portal.conftest import qid_from_submit_redirect
    qid = qid_from_submit_redirect(r.headers["location"])
    r = client.get(f"/configurator/query/{qid}")
    assert r.status_code == 200
    return qid, r.text


# --------------- Сценарий А: один вариант, длинное имя GPU ---------------

def test_scenario_a_single_variant_long_gpu_name(
    manager_client, mock_process_query, db_session
):
    long_gpu = "ASUS ROG Strix GeForce RTX 4070 Ti SUPER OC Edition 16GB GDDR6X"
    cpu_id = _insert_cpu(db_session, "Ryzen 7 7700X", "AMD",
                         cores=8, threads=16, base=4.5, turbo=5.4,
                         socket="AM5")
    gpu_id = _insert_gpu(db_session, long_gpu, "ASUS",
                         vram_gb=16, vram_type="GDDR6X", tdp=285)
    ram_id = _insert_ram(db_session, "Kingston Fury Beast", "Kingston",
                         size_gb=16, count=2, mem_type="DDR5", freq=6000)
    db_session.commit()

    variant = Variant(
        manufacturer="AMD",
        components=[
            _component("cpu", cpu_id, "Ryzen 7 7700X",
                       "OCS", 389, 35100, supplier_sku="3000000789"),
            _component("gpu", gpu_id, long_gpu,
                       "OCS", 1100, 99000, supplier_sku="3000000790",
                       in_transit=True),
            _component("ram", ram_id, "Kingston Fury Beast 32GB",
                       "Merlion", 110, 9900),
        ],
        total_usd=1599, total_rub=143999,
        used_transit=True,
    )
    mock_process_query.return_value = FinalResponse(
        kind="ok",
        interpretation="Игровой ПК на AMD с RTX 4070 Ti SUPER.",
        formatted_text="",
        build_request=BuildRequest(),
        build_result=BuildResult(
            status="ok", variants=[variant],
            refusal_reason=None, usd_rub_rate=90.0, fx_source="fallback",
        ),
        parsed=ParsedRequest(is_empty=False, purpose="gaming", budget_usd=1600),
        resolved=[], warnings=[], cost_usd=0.0,
    )

    _, html = _submit_and_get_result(
        manager_client,
        "игровой ПК на AMD с RTX 4070 Ti SUPER до 150 тысяч",
    )
    _dump_html("scen_a_single_variant_long_gpu", html)

    # Этап 6.2: на /query/{id} теперь компактная таблица вместо карточек.
    assert "<table" in html
    assert "<thead>" in html
    # Колонки таблицы
    for col in ("Категория", "Название", "Артикул", "Поставщик"):
        assert col in html
    # Длинное имя GPU присутствует целиком
    assert long_gpu in html
    # Характеристики подтянуты (вторая строка в ячейке Название)
    assert "8C/16T · 4.5/5.4GHz · AM5" in html
    assert "16GB GDDR6X · 285W" in html
    assert "16GB × 2 · DDR5-6000" in html
    # Транзит отмечен
    assert "транзит" in html
    # Заголовок варианта
    assert "Вариант" in html and "AMD" in html


# --------------- Сценарий Б: два варианта друг под другом ----------------

def test_scenario_b_two_variants_stacked(
    manager_client, mock_process_query, db_session
):
    intel_cpu = _insert_cpu(db_session, "Core i5-12400", "Intel",
                            cores=6, threads=12, base=2.5, turbo=4.4,
                            socket="LGA1700")
    amd_cpu = _insert_cpu(db_session, "Ryzen 5 7600", "AMD",
                          cores=6, threads=12, base=3.8, turbo=5.1,
                          socket="AM5")
    ram_id = _insert_ram(db_session, "Crucial 16GB", "Crucial",
                         size_gb=16, count=1, mem_type="DDR5", freq=5200)
    db_session.commit()

    def mk(mfr: str, cpu_id: int, cpu_model: str,
           total_usd: float, total_rub: float) -> Variant:
        return Variant(
            manufacturer=mfr,
            components=[
                _component("cpu", cpu_id, cpu_model, "Merlion",
                           200, 18000),
                _component("ram", ram_id, "Crucial 16GB DDR5", "OCS",
                           45, 4050),
            ],
            total_usd=total_usd, total_rub=total_rub,
        )

    mock_process_query.return_value = FinalResponse(
        kind="ok",
        interpretation="ПК для дома, 16 ГБ.",
        formatted_text="",
        build_request=BuildRequest(),
        build_result=BuildResult(
            status="ok",
            variants=[
                mk("Intel", intel_cpu, "Intel Core i5-12400", 780, 70200),
                mk("AMD",   amd_cpu,   "AMD Ryzen 5 7600",    820, 73800),
            ],
            refusal_reason=None, usd_rub_rate=90.0, fx_source="fallback",
        ),
        parsed=ParsedRequest(is_empty=False, purpose="home", budget_usd=900),
        resolved=[], warnings=[], cost_usd=0.0,
    )

    _, html = _submit_and_get_result(
        manager_client,
        "ПК для дома, 16 ГБ памяти, бюджет до 80 тысяч",
    )
    _dump_html("scen_b_two_variants", html)

    # Оба варианта присутствуют и идут друг под другом.
    assert html.count("Вариант") >= 2
    assert "Intel" in html and "AMD" in html
    # Обе таблицы отрисованы.
    assert html.count("<table") >= 2
    # Характеристики подтянуты для обоих CPU.
    assert "6C/12T · 2.5/4.4GHz · LGA1700" in html
    assert "6C/12T · 3.8/5.1GHz · AM5" in html
    # Порядок: Intel блок идёт раньше AMD.
    intel_idx = html.find("Core i5-12400")
    amd_idx = html.find("Ryzen 5 7600")
    assert intel_idx != -1 and amd_idx != -1
    assert intel_idx < amd_idx


# --------------- Сценарий В: warnings + транзит --------------------------

def test_scenario_c_warnings_block(
    manager_client, mock_process_query, db_session
):
    cpu_id = _insert_cpu(db_session, "Core i7-13700", "Intel",
                         cores=16, threads=24, base=2.1, turbo=5.2,
                         socket="LGA1700")
    gpu_id = _insert_gpu(db_session, "GeForce RTX 4060", "NVIDIA",
                         vram_gb=8, vram_type="GDDR6", tdp=115)
    ram_id = _insert_ram(db_session, "G.Skill Trident Z5", "G.Skill",
                         size_gb=16, count=2, mem_type="DDR5", freq=6000)
    db_session.commit()

    variant = Variant(
        manufacturer="Intel",
        components=[
            _component("cpu", cpu_id, "Intel Core i7-13700",
                       "OCS", 400, 36000),
            _component("gpu", gpu_id, "NVIDIA GeForce RTX 4060",
                       "Merlion", 320, 28800, in_transit=True),
            _component("ram", ram_id, "G.Skill Trident Z5 32GB",
                       "OCS", 170, 15300),
        ],
        total_usd=1290, total_rub=116100,
        warnings=[
            "Точная модель памяти 32 ГБ DDR5 6400 не найдена — "
            "взят близкий вариант G.Skill Trident Z5 DDR5-6000.",
            "Позиция «NVIDIA GeForce RTX 4060» взята из транзита.",
        ],
        used_transit=True,
    )
    mock_process_query.return_value = FinalResponse(
        kind="ok",
        interpretation="Мощный ПК для видеомонтажа.",
        formatted_text="",
        build_request=BuildRequest(),
        build_result=BuildResult(
            status="ok", variants=[variant],
            refusal_reason=None, usd_rub_rate=90.0, fx_source="fallback",
        ),
        parsed=ParsedRequest(is_empty=False, purpose="video", budget_usd=1300),
        resolved=[], warnings=[], cost_usd=0.0,
    )

    _, html = _submit_and_get_result(
        manager_client,
        "мощный ПК для видеомонтажа с RTX 4060 и 32 ГБ памяти",
    )
    _dump_html("scen_c_warnings", html)

    # Блок предупреждений с жёлтой рамкой.
    # Этап 9А.1: amber → семантический warning из дизайн-токенов.
    assert "Предупреждения по сборке" in html
    assert "alert-warning" in html
    assert "warning-500" in html  # рамка/текст #
    # Оба предупреждения попали в вывод.
    assert "близкий вариант" in html
    assert "из транзита" in html
    # Транзит отмечен и на уровне карточки
    assert "транзит" in html
