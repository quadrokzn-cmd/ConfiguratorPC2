# Идемпотентный раннер plain-SQL миграций (этап 10.1).
#
# В проекте нет Alembic — миграции лежат как обычные .sql-файлы в
# каталоге migrations/. Чтобы при каждом старте контейнера на Railway
# не применять всё подряд (и не падать на повторном CREATE TABLE),
# заводим служебную таблицу `schema_migrations` и помечаем там, какие
# файлы уже применены.
#
# Поведение:
#   1. Создаёт schema_migrations(filename PK, applied_at), если её нет.
#   2. Если schema_migrations пуста, но в БД уже есть таблица `suppliers`
#      (т. е. это существующая dev/prod БД, накатанная вручную через psql
#      ДО появления раннера) — считает ВСЕ существующие *.sql в migrations/
#      применёнными и просто проставляет записи. Это безопасный режим
#      адаптации legacy-БД.
#   3. Применяет каждую неприменённую миграцию в одной транзакции;
#      на ошибке откатывает только её и падает с не-нулевым кодом.
#   4. Идемпотентен: повторный вызов ничего не делает, если новых файлов нет.
#
# Запуск:
#   python -m scripts.apply_migrations
# (используется в Procfile / railway.json как pre-start команда).

from __future__ import annotations

import sys
from pathlib import Path

# Чтобы можно было запускать `python scripts/apply_migrations.py` напрямую.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

from dotenv import load_dotenv

load_dotenv()

from sqlalchemy import text  # noqa: E402

from shared.db import engine  # noqa: E402


_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def _ensure_schema_migrations(conn) -> None:
    """Создаёт служебную таблицу-журнал, если её ещё нет."""
    conn.execute(text(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "    filename TEXT PRIMARY KEY,"
        "    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()"
        ")"
    ))


def _list_migration_files() -> list[Path]:
    """Все *.sql из каталога migrations/, отсортированы по имени файла."""
    if not _MIGRATIONS_DIR.is_dir():
        return []
    return sorted(p for p in _MIGRATIONS_DIR.iterdir() if p.suffix == ".sql")


def _already_applied(conn) -> set[str]:
    rows = conn.execute(text("SELECT filename FROM schema_migrations")).all()
    return {r.filename for r in rows}


def _legacy_db_with_suppliers(conn) -> bool:
    """True, если в БД уже есть таблица `suppliers` из миграции 001 —
    значит, это старая БД, накатанная до появления раннера."""
    row = conn.execute(text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'suppliers'"
    )).first()
    return row is not None


def _mark_applied(conn, filename: str) -> None:
    conn.execute(
        text(
            "INSERT INTO schema_migrations (filename) VALUES (:f) "
            "ON CONFLICT (filename) DO NOTHING"
        ),
        {"f": filename},
    )


def main() -> int:
    files = _list_migration_files()
    if not files:
        print("Каталог migrations/ пуст — нечего применять.")
        return 0

    with engine.begin() as conn:
        _ensure_schema_migrations(conn)
        applied = _already_applied(conn)

        # Адаптация legacy-БД: журнал пуст, но схема уже есть.
        if not applied and _legacy_db_with_suppliers(conn):
            print(
                "Обнаружена существующая БД (есть таблица suppliers), "
                "журнал миграций пуст — отмечаю все %d файлов как применённые."
                % len(files)
            )
            for f in files:
                _mark_applied(conn, f.name)
            return 0

    # Применяем по одной, каждая в своей транзакции — чтобы при ошибке
    # уже применённые записи остались в журнале.
    new_count = 0
    for f in files:
        if f.name in applied:
            continue
        sql = f.read_text(encoding="utf-8")
        print(f"Применяю миграцию: {f.name}")
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
                _mark_applied(conn, f.name)
        except Exception as exc:
            print(
                f"ОШИБКА при применении {f.name}: {exc}",
                file=sys.stderr,
            )
            return 1
        new_count += 1

    if new_count == 0:
        print("Все миграции уже применены — нечего делать.")
    else:
        print(f"Готово: применено {new_count} новых миграций.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
