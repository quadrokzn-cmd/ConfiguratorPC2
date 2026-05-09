"""One-off helper: dump raw_html for selected reg_numbers into tests/fixtures/raw_html/.

Использование:
    $env:DATABASE_URL_LOCAL = "postgresql+psycopg2://postgres@localhost:5432/quadrotech"
    python scripts/_dump_raw_html.py 0848300064126000162 0107300018926000042 0317100032926000169
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, text  # noqa: E402


def main(args: list[str]) -> int:
    if not args:
        print("usage: _dump_raw_html.py <reg_number> [<reg_number> ...]", file=sys.stderr)
        return 2
    url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_LOCAL")
    if not url:
        print("DATABASE_URL is not set", file=sys.stderr)
        return 2
    engine = create_engine(url, future=True)
    out_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "raw_html"
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
