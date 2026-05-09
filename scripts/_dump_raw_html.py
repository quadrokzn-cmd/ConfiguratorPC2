"""One-off helper: dump raw_html for selected reg_numbers into tests/fixtures/raw_html/.

Этап 8 слияния (2026-05-08): импорты переехали в C-PC2 (engine из app.database).

Использование:
    python scripts/_dump_raw_html.py 0848300064126000162 0107300018926000042
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from sqlalchemy import text  # noqa: E402

from app.database import engine  # noqa: E402


def main(args: list[str]) -> int:
    if not args:
        print("usage: _dump_raw_html.py <reg_number> [<reg_number> ...]", file=sys.stderr)
        return 2
    out_dir = Path(__file__).resolve().parent.parent / "tests" / "test_auctions" / "fixtures" / "raw_html"
    out_dir.mkdir(parents=True, exist_ok=True)
    with engine.connect() as conn:
        for rn in args:
            row = conn.execute(text("SELECT raw_html FROM tenders WHERE reg_number=:rn"), {"rn": rn}).first()
            if row is None or row.raw_html is None:
                print(f"  {rn}: not found", file=sys.stderr)
                continue
            path = out_dir / f"{rn}.html"
            path.write_text(row.raw_html, encoding="utf-8")
            print(f"  {rn}: {len(row.raw_html)} bytes -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
