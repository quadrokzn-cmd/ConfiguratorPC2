"""Apply SQL migrations from ../migrations, tracking applied files in _migrations."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS _migrations (
    filename    TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        url = os.getenv("DATABASE_URL_LOCAL")
    if not url:
        print("ERROR: DATABASE_URL (or DATABASE_URL_LOCAL) is not set", file=sys.stderr)
        sys.exit(2)
    return url


def _checksum(text_content: str) -> str:
    return hashlib.sha256(text_content.encode("utf-8")).hexdigest()


def main() -> int:
    url = _resolve_database_url()
    engine = create_engine(url, future=True)

    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if p.is_file())
    if not files:
        print(f"No migrations found in {MIGRATIONS_DIR}")
        return 0

    with engine.begin() as conn:
        conn.execute(text(BOOTSTRAP_SQL))

    with engine.connect() as conn:
        applied = {
            row[0]
            for row in conn.execute(text("SELECT filename FROM _migrations")).fetchall()
        }

    for path in files:
        name = path.name
        sql = path.read_text(encoding="utf-8")
        if name in applied:
            print(f"[skip] {name} (already applied)")
            continue
        print(f"[apply] {name}")
        with engine.begin() as conn:
            conn.execute(text(sql))
            conn.execute(
                text(
                    "INSERT INTO _migrations (filename, checksum) VALUES (:f, :c)"
                ),
                {"f": name, "c": _checksum(sql)},
            )
    print("Migrations complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
