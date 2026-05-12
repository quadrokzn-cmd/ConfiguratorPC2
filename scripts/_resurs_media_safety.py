# Общий sanity-check для skрiptов Resurs Media (smoke + bootstrap).
#
# Идея простая: ни один CLI не должен случайно выстрелить по prod-стенду
# Ресурс-Медиа. Защиту делаем двойную:
#   1) по URL: если в WSDL нет подстроки "test" — считаем это prod;
#   2) по флагу: prod допускается ТОЛЬКО при явном --allow-prod И
#      интерактивном подтверждении (input("YES")).
#
# Импортируется обоими скриптами:
#   scripts/resurs_media_smoke.py
#   scripts/resurs_media_bootstrap_catalog.py
#
# Поведение:
#   * test-URL, allow_prod=False → проходит молча.
#   * test-URL, allow_prod=True  → проходит молча (флаг безвреден).
#   * prod-URL, allow_prod=False → печатает ошибку и SystemExit(2).
#   * prod-URL, allow_prod=True  → печатает WARNING, спрашивает 'YES';
#     при любом другом вводе — SystemExit(0).

from __future__ import annotations

import sys
from typing import Callable


def check_prod_safety(
    wsdl_url: str,
    allow_prod: bool,
    *,
    input_fn: Callable[[str], str] = input,
    out=sys.stdout,
) -> None:
    """Падает через SystemExit, если попытка выстрелить по prod-URL не
    разрешена/не подтверждена. На test-URL — молча возвращается.

    Параметры input_fn/out параметризованы для тестов (monkeypatch не
    требуется — можно подменить аргументами)."""
    is_test = "test" in (wsdl_url or "").lower()

    if is_test:
        # Тестовый стенд — никаких подтверждений не требуется.
        return

    if not allow_prod:
        print(
            f"ERROR: WSDL URL не содержит 'test' ({wsdl_url}).",
            file=out,
        )
        print(
            "Передайте --allow-prod явно, если действительно хотите "
            "стрелять по prod-API Resurs Media.",
            file=out,
        )
        raise SystemExit(2)

    # prod-URL + allow_prod=True — последний рубеж: интерактивное YES.
    bar = "=" * 60
    print(bar, file=out)
    print("*** ВНИМАНИЕ: РАБОТА ПРОТИВ PRODUCTION RESURS MEDIA ***", file=out)
    print(f"WSDL URL: {wsdl_url}", file=out)
    print(bar, file=out)
    answer = input_fn("Введите 'YES' (заглавными) для продолжения: ").strip()
    if answer != "YES":
        print("Прервано пользователем.", file=out)
        raise SystemExit(0)
