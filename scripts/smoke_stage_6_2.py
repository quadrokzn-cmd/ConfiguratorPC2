# -*- coding: utf-8 -*-
"""Smoke-тест этапа 6.2 через TestClient.

Сценарий:
  1. Создать проект.
  2. Добавить две конфигурации через /project/{pid}/new_query.
  3. Выбрать Intel у первой и AMD у второй.
  4. Поменять количество у первой с 1 на 3.
  5. Снять Intel у первой, выбрать у второй оба варианта.
  6. Вывести итоговую таблицу спецификации и сумму.

process_query мокается — реальный OpenAI не вызывается.
БД — configurator_pc_test (настройка из TEST_DATABASE_URL).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Настраиваем окружение до импорта app.* (как в tests/conftest.py).
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/configurator_pc_test",
)
os.environ.setdefault("OPENAI_API_KEY", "sk-test-stub")
os.environ.setdefault("SESSION_SECRET_KEY", "smoke-secret")


def main() -> None:
    from sqlalchemy import create_engine, text
    from fastapi.testclient import TestClient
    import re

    from app.config import settings
    from app.auth import hash_password
    from portal.services.configurator.engine.schema import (
        BuildRequest, BuildResult, ComponentChoice, SupplierOffer, Variant,
    )
    from portal.services.configurator.nlu.schema import FinalResponse, ParsedRequest

    # На русской Windows консоль по умолчанию в cp1251 — принудительно
    # переключим stdout/stderr на utf-8, чтобы ₽/→ печатались корректно.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

    # ---- 1. Свежая БД --------------------------------------------------
    engine = create_engine(
        settings.test_database_url,
        connect_args={"client_encoding": "utf8"},
    )
    migrations = [
        "001_init.sql",
        "002_add_currency_and_relax_nullability.sql",
        "003_widen_model_column.sql",
        "004_add_component_field_sources.sql",
        "005_add_source_url_to_component_field_sources.sql",
        "006_add_api_usage_log.sql",
        "007_web_service.sql",
        "008_project_specification.sql",
    ]
    tables_to_drop = [
        "specification_items", "queries", "projects", "daily_budget_log",
        "users", "api_usage_log", "component_field_sources",
        "price_uploads", "supplier_prices", "suppliers",
        "cpus", "motherboards", "rams", "gpus", "storages",
        "cases", "psus", "coolers",
    ]
    with engine.begin() as conn:
        for t in tables_to_drop:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        for m in migrations:
            sql = (ROOT / "migrations" / m).read_text(encoding="utf-8")
            conn.execute(text(sql))

    # ---- 2. Менеджер ---------------------------------------------------
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO users (login, password_hash, role, name) "
            "VALUES ('smoke', :p, 'manager', 'Smoke Tester')"
        ), {"p": hash_password("pass")})

    # ---- 3. Мок process_query с двумя вариантами ----------------------
    def make_resp(intel_usd: float, intel_rub: float,
                  amd_usd: float, amd_rub: float) -> FinalResponse:
        variants = [
            Variant(
                manufacturer="Intel",
                components=[ComponentChoice(
                    category="cpu", component_id=1, model="Intel Core i5-12400F",
                    sku=None, manufacturer="Intel",
                    chosen=SupplierOffer(supplier="S", price_usd=180,
                                         price_rub=16200, stock=10),
                )],
                total_usd=intel_usd, total_rub=intel_rub,
            ),
            Variant(
                manufacturer="AMD",
                components=[ComponentChoice(
                    category="cpu", component_id=2, model="AMD Ryzen 5 7600",
                    sku=None, manufacturer="AMD",
                    chosen=SupplierOffer(supplier="S", price_usd=200,
                                         price_rub=18000, stock=5),
                )],
                total_usd=amd_usd, total_rub=amd_rub,
            ),
        ]
        return FinalResponse(
            kind="ok", interpretation="Smoke",
            formatted_text="", build_request=BuildRequest(),
            build_result=BuildResult(
                status="ok", variants=variants, refusal_reason=None,
                usd_rub_rate=90.0, fx_source="fallback",
            ),
            parsed=ParsedRequest(is_empty=False, purpose="office"),
            resolved=[], warnings=[], cost_usd=0.0,
        )

    # У первой конфигурации будут Intel=200$ / AMD=250$,
    # у второй конфигурации Intel=300$ / AMD=350$.
    responses = iter([
        make_resp(200, 18000, 250, 22500),
        make_resp(300, 27000, 350, 31500),
    ])

    # ---- 4. TestClient и сценарий -------------------------------------
    from app.main import app
    from app.routers import main_router, project_router

    mock = MagicMock(side_effect=lambda *_a, **_k: next(responses))

    def extract_csrf(html: str) -> str:
        m = re.search(r'name="csrf_token" value="([^"]+)"', html)
        assert m, "csrf not found"
        return m.group(1)

    def ajax(c, url, payload, csrf):
        r = c.post(url, json=payload, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, f"{url}: {r.status_code} {r.text[:200]}"
        return r.json()

    with patch.object(main_router, "process_query", mock), \
         patch.object(project_router, "process_query", mock):
        with TestClient(app, follow_redirects=False) as c:
            # логин
            r = c.get("/login")
            csrf = extract_csrf(r.text)
            r = c.post("/login", data={
                "login": "smoke", "password": "pass", "csrf_token": csrf,
            })
            assert r.status_code in (302, 303), r.text[:200]

            # создаём проект
            csrf = extract_csrf(c.get("/projects").text)
            r = c.post("/projects", data={"csrf_token": csrf})
            pid = int(r.headers["location"].rsplit("/", 1)[1])
            print(f"[1] Создан проект id={pid}")

            # две конфигурации
            qids: list[int] = []
            for i in range(2):
                r = c.get(f"/project/{pid}/new_query")
                csrf = extract_csrf(r.text)
                r = c.post(
                    f"/project/{pid}/new_query",
                    data={"raw_text": f"конфигурация {i+1}", "csrf_token": csrf},
                )
                loc = r.headers["location"]
                qid = int(loc.split("highlight=")[1])
                qids.append(qid)
                print(f"[2] Добавлена конфигурация id={qid}")

            # AJAX CSRF
            csrf = extract_csrf(c.get(f"/project/{pid}").text)

            # выбираем Intel у первой, AMD у второй
            d = ajax(c, f"/project/{pid}/select",
                     {"query_id": qids[0], "variant_manufacturer": "Intel", "quantity": 1},
                     csrf)
            d = ajax(c, f"/project/{pid}/select",
                     {"query_id": qids[1], "variant_manufacturer": "AMD", "quantity": 1},
                     csrf)
            print(f"[3] После двух select: позиций {len(d['items'])}, "
                  f"итого ${d['total_usd']}")

            # меняем кол-во первой с 1 на 3
            d = ajax(c, f"/project/{pid}/update_quantity",
                     {"query_id": qids[0], "variant_manufacturer": "Intel", "quantity": 3},
                     csrf)
            print(f"[4] После update_quantity(first Intel → 3): "
                  f"итого ${d['total_usd']}")

            # снимаем Intel у первой
            d = ajax(c, f"/project/{pid}/deselect",
                     {"query_id": qids[0], "variant_manufacturer": "Intel"},
                     csrf)
            print(f"[5] После deselect первой Intel: позиций {len(d['items'])}, "
                  f"итого ${d['total_usd']}")

            # выбираем у второй ОБА варианта (Intel рядом с уже выбранным AMD)
            d = ajax(c, f"/project/{pid}/select",
                     {"query_id": qids[1], "variant_manufacturer": "Intel", "quantity": 2},
                     csrf)
            print(f"[6] Итоговое состояние спецификации:")
            for it in d["items"]:
                print(
                    f"     #{it['position']} q{it['query_id']}/{it['variant_manufacturer']} "
                    f"× {it['quantity']} = ${it['total_usd']} "
                    f"({it['total_rub']:.0f} ₽)  |  {it['display_name']}"
                )
            print(f"     ИТОГО: ${d['total_usd']}  ({d['total_rub']:.0f} ₽)")

    print("\nSmoke-тест пройден.")


if __name__ == "__main__":
    main()
