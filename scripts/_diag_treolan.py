"""[scratch] Диагностический вызов Treolan API ВНЕ pipeline'а
(этап 12.3 — расследование run #17, total_rows=0/disappeared=1391).

Делает:
  1) POST /v1/auth/token (или /v1/auth/login fallback) — получить JWT
  2) POST /v1/Catalog/Get с теми же body, что и адаптер
  3) Распечатать структуру ответа БЕЗ значений (только ключи и типы),
     первые 3 позиции (только структура), первые ключи top-level.

В БД ничего не пишет. Запускать локально (с .env) или через
`railway run python scripts/_diag_treolan.py`.
"""
from __future__ import annotations

import json
import os
import sys

import httpx


BASE_URL = (os.environ.get("TREOLAN_API_BASE_URL") or "https://api.treolan.ru/api").rstrip("/")
LOGIN = os.environ.get("TREOLAN_API_LOGIN") or ""
PASSWORD = os.environ.get("TREOLAN_API_PASSWORD") or ""

if not LOGIN or not PASSWORD:
    print("ERR: TREOLAN_API_LOGIN/TREOLAN_API_PASSWORD не заданы.", file=sys.stderr)
    sys.exit(2)

print(f"[diag] BASE_URL = {BASE_URL}")


# ---- 1. Auth ------------------------------------------------------------
def _extract_token(text: str) -> str:
    body = (text or "").strip()
    if body.startswith('"') and body.endswith('"'):
        try:
            body = json.loads(body)
        except Exception:
            body = body.strip('"')
    return body


token = None
auth_url = f"{BASE_URL}/v1/auth/token"
print(f"[diag] POST {auth_url} (json body)")
try:
    with httpx.Client(timeout=httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=15.0)) as c:
        r = c.post(auth_url, json={"login": LOGIN, "password": PASSWORD},
                   headers={"Accept": "application/json", "Content-Type": "application/json"})
    print(f"[diag] -> HTTP {r.status_code}, body {len(r.text)} bytes")
    if r.status_code == 200:
        token = _extract_token(r.text)
except httpx.RequestError as exc:
    print(f"[diag] primary auth network error: {exc}")

if not token:
    fb_url = f"{BASE_URL}/v1/auth/login"
    print(f"[diag] FALLBACK POST {fb_url} (query params)")
    try:
        with httpx.Client(timeout=httpx.Timeout(connect=15.0, read=30.0, write=15.0, pool=15.0)) as c:
            r = c.post(fb_url, params={"login": LOGIN, "password": PASSWORD},
                       headers={"Accept": "application/json"})
        print(f"[diag] -> HTTP {r.status_code}, body {len(r.text)} bytes")
        if r.status_code == 200:
            token = _extract_token(r.text)
    except httpx.RequestError as exc:
        print(f"[diag] fallback auth network error: {exc}")

if not token or "." not in token:
    print("[diag] FATAL: не получили JWT.")
    sys.exit(3)
print(f"[diag] token OK ({len(token)} chars, starts {token[:8]}…)")
print()


# ---- 2. Catalog/Get -----------------------------------------------------
catalog_url = f"{BASE_URL}/v1/Catalog/Get"
body = {
    "category": "", "vendorid": 0, "keywords": "", "criterion": "Contains",
    "inArticul": True, "inName": False, "inMark": False, "showNc": 0,
    "freeNom": True, "withoutLocalization": False,
}
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "Accept": "application/json"}
print(f"[diag] POST {catalog_url}")
print(f"[diag] body = {body}")
with httpx.Client(timeout=httpx.Timeout(connect=30.0, read=120.0, write=60.0, pool=30.0)) as c:
    r = c.post(catalog_url, json=body, headers=headers)
print(f"[diag] -> HTTP {r.status_code}, body {len(r.content)} bytes ({len(r.text)} chars)")

if r.status_code != 200:
    print(f"[diag] ERROR. First 500 chars of body: {r.text[:500]!r}")
    sys.exit(4)

# ---- 3. Структура (без значений) ---------------------------------------
try:
    data = r.json()
except Exception as exc:
    print(f"[diag] FATAL: тело не JSON: {exc}")
    print(f"[diag] First 500 chars: {r.text[:500]!r}")
    sys.exit(5)

print()
print("=== TOP-LEVEL ===")
if isinstance(data, dict):
    for k in data.keys():
        v = data[k]
        type_name = type(v).__name__
        size = len(v) if hasattr(v, "__len__") else "n/a"
        print(f"  {k!r:<30} type={type_name:<10} len={size}")
else:
    print(f"  Тело — НЕ dict, а {type(data).__name__} (len={len(data) if hasattr(data,'__len__') else 'n/a'})")
    if isinstance(data, list) and data:
        print(f"  data[0] keys: {list(data[0].keys()) if isinstance(data[0], dict) else type(data[0]).__name__}")

if isinstance(data, dict):
    positions = data.get("positions") or []
    categories = data.get("categories") or []
    print(f"\n=== positions: len = {len(positions)} ===")
    print(f"=== categories: len = {len(categories)} ===")

    if positions:
        print("\nПервые 3 позиции — ТОЛЬКО структура (ключи + типы):")
        for i, p in enumerate(positions[:3]):
            if not isinstance(p, dict):
                print(f"  [{i}] не dict: {type(p).__name__}")
                continue
            print(f"  [{i}] keys & types:")
            for k, v in p.items():
                t = type(v).__name__
                if isinstance(v, str):
                    marker = "<empty-str>" if v == "" else f"<str len={len(v)}>"
                elif isinstance(v, (int, float)):
                    marker = "<num>" if v != 0 else "<num=0>"
                elif v is None:
                    marker = "<None>"
                elif isinstance(v, bool):
                    marker = f"<bool={v}>"
                elif isinstance(v, (list, dict)):
                    marker = f"<{t} len={len(v)}>"
                else:
                    marker = f"<{t}>"
                print(f"      {k!r:<25} {marker}")

    if categories and not positions:
        print("\n[!] positions пустой, но есть categories. Первая категория — структура:")
        c0 = categories[0]
        if isinstance(c0, dict):
            for k, v in c0.items():
                t = type(v).__name__
                print(f"  {k!r:<25} type={t}")

    if not positions and not categories:
        print("\n[!] И positions, и categories пустые. Полный ответ (первые 1500 chars JSON):")
        print(json.dumps(data, ensure_ascii=False)[:1500])

    # ---- A. Корневые категории ----
    if categories:
        print("\n=== Корневые категории (top-level) ===")
        for c in categories:
            if not isinstance(c, dict):
                continue
            print(f"  id={c.get('id'):<8} name={c.get('name')!r}")

    # ---- A. DFS: найти первую категорию с products ----
    if categories:
        print("\n=== DFS: первая непустая категория ===")
        FIELDS_TO_SHOW = (
            "articul", "code", "rusName", "name", "description",
            "currentPrice", "price", "currency",
            "atStock", "inTransit", "transit",
            "vendor", "vendorId", "gtin",
            "id", "guid",
        )

        def walk(nodes, path):
            for n in nodes or []:
                if not isinstance(n, dict):
                    continue
                cur = path + [n.get("name") or n.get("rusName") or "?"]
                prods = n.get("products") or []
                if isinstance(prods, list) and prods:
                    yield cur, n, prods
                kids = n.get("children") or []
                if isinstance(kids, list):
                    yield from walk(kids, cur)

        total_products = 0
        nonempty_cats = 0
        first_hit = None
        for path, cat, prods in walk(categories, []):
            nonempty_cats += 1
            total_products += len(prods)
            if first_hit is None:
                first_hit = (path, cat, prods)

        print(f"  непустых категорий: {nonempty_cats}")
        print(f"  всего products в дереве (DFS-сумма): {total_products}")

        if first_hit:
            path, cat, prods = first_hit
            print(f"\n  путь: {' -> '.join(path)}")
            print(f"  category.id={cat.get('id')!r} name={cat.get('name')!r} "
                  f"productsQty={cat.get('productsQty')} "
                  f"totalProductsQty={cat.get('totalProductsQty')} "
                  f"len(products)={len(prods)}")

            print("\n  первые 3 товара — все ключи + типы + значения для важных полей:")
            for i, p in enumerate(prods[:3]):
                if not isinstance(p, dict):
                    print(f"    [{i}] не dict: {type(p).__name__}")
                    continue
                print(f"    --- [{i}] keys & types ---")
                for k, v in p.items():
                    t = type(v).__name__
                    if isinstance(v, str):
                        marker = "<empty-str>" if v == "" else f"<str len={len(v)}>"
                    elif isinstance(v, bool):
                        marker = f"<bool={v}>"
                    elif isinstance(v, (int, float)):
                        marker = f"<{t}={v}>" if v == 0 else f"<{t}>"
                    elif v is None:
                        marker = "<None>"
                    elif isinstance(v, (list, dict)):
                        marker = f"<{t} len={len(v)}>"
                    else:
                        marker = f"<{t}>"
                    print(f"      {k!r:<25} {marker}")
                print(f"    --- [{i}] real values for key fields ---")
                for k in FIELDS_TO_SHOW:
                    if k in p:
                        v = p[k]
                        if isinstance(v, str) and len(v) > 80:
                            v = v[:80] + "…"
                        print(f"      {k!r:<25} = {v!r}")
