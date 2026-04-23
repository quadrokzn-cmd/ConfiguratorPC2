# Тесты миграции 008 (этап 6.2).
#
# БД проливается однократно фикстурой db_engine — миграции 001-008
# уже применены. Здесь мы только:
#   1) проверяем, что таблица specification_items создана и имеет
#      нужные ограничения (UNIQUE, CHECK, FK);
#   2) проверяем каскадные удаления.

from __future__ import annotations

import pytest
from sqlalchemy import text as _t


# --------------------------- helpers ------------------------------------

def _seed_minimal(conn) -> tuple[int, int, int]:
    """Создаёт пользователя, проект и запрос, возвращает (uid, pid, qid).
    Работает с объектом connection (db_engine.begin())."""
    uid = conn.execute(_t(
        "INSERT INTO users (login, password_hash, role, name) "
        "VALUES ('mig-user', 'x', 'manager', 'Mig') RETURNING id"
    )).scalar()
    pid = conn.execute(_t(
        "INSERT INTO projects (user_id, name) "
        "VALUES (:uid, 'Проект') RETURNING id"
    ), {"uid": uid}).scalar()
    qid = conn.execute(_t(
        "INSERT INTO queries (project_id, user_id, raw_text, status) "
        "VALUES (:pid, :uid, 'raw', 'ok') RETURNING id"
    ), {"pid": pid, "uid": uid}).scalar()
    return int(uid), int(pid), int(qid)


def _insert_spec(conn, *, pid: int, qid: int, manufacturer: str = "Intel",
                 position: int = 1, quantity: int = 1) -> int:
    row_id = conn.execute(_t(
        "INSERT INTO specification_items "
        "  (project_id, query_id, variant_manufacturer, quantity, position, "
        "   auto_name, unit_usd, unit_rub, total_usd, total_rub) "
        "VALUES (:pid, :qid, :mfg, :q, :pos, 'Test', 100, 9000, 100, 9000) "
        "RETURNING id"
    ), {
        "pid": pid, "qid": qid, "mfg": manufacturer,
        "q": quantity, "pos": position,
    }).scalar()
    return int(row_id)


# --------------------------- тесты --------------------------------------

def test_specification_items_table_exists(db_engine):
    with db_engine.begin() as conn:
        row = conn.execute(_t(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' "
            "  AND table_name = 'specification_items'"
        )).first()
        assert row is not None


def test_unique_project_query_manufacturer(db_engine):
    """UNIQUE (project_id, query_id, variant_manufacturer)."""
    from psycopg2.errors import UniqueViolation  # type: ignore
    from sqlalchemy.exc import IntegrityError

    with db_engine.begin() as conn:
        _, pid, qid = _seed_minimal(conn)
        _insert_spec(conn, pid=pid, qid=qid, manufacturer="Intel", position=1)

    with pytest.raises(IntegrityError):
        with db_engine.begin() as conn:
            _insert_spec(conn, pid=pid, qid=qid, manufacturer="Intel", position=2)


def test_quantity_check_positive(db_engine):
    """CHECK (quantity > 0)."""
    from sqlalchemy.exc import IntegrityError

    with db_engine.begin() as conn:
        _, pid, qid = _seed_minimal(conn)

    with pytest.raises(IntegrityError):
        with db_engine.begin() as conn:
            conn.execute(_t(
                "INSERT INTO specification_items "
                "  (project_id, query_id, variant_manufacturer, quantity, position, "
                "   auto_name, unit_usd, unit_rub, total_usd, total_rub) "
                "VALUES (:pid, :qid, 'Intel', 0, 1, 'T', 1, 1, 1, 1)"
            ), {"pid": pid, "qid": qid})


def test_cascade_on_project_delete(db_engine):
    """Удаление проекта → specification_items и queries уходят каскадом."""
    with db_engine.begin() as conn:
        _, pid, qid = _seed_minimal(conn)
        _insert_spec(conn, pid=pid, qid=qid, manufacturer="Intel")

    with db_engine.begin() as conn:
        conn.execute(_t("DELETE FROM projects WHERE id = :pid"), {"pid": pid})

    with db_engine.begin() as conn:
        cnt_q = conn.execute(_t(
            "SELECT COUNT(*) FROM queries WHERE project_id = :pid"
        ), {"pid": pid}).scalar()
        cnt_s = conn.execute(_t(
            "SELECT COUNT(*) FROM specification_items WHERE project_id = :pid"
        ), {"pid": pid}).scalar()
        assert cnt_q == 0
        assert cnt_s == 0


def test_cascade_on_query_delete(db_engine):
    """Удаление запроса → связанные строки спецификации уходят каскадом."""
    with db_engine.begin() as conn:
        _, pid, qid = _seed_minimal(conn)
        _insert_spec(conn, pid=pid, qid=qid, manufacturer="Intel")

    with db_engine.begin() as conn:
        conn.execute(_t("DELETE FROM queries WHERE id = :qid"), {"qid": qid})

    with db_engine.begin() as conn:
        cnt_s = conn.execute(_t(
            "SELECT COUNT(*) FROM specification_items WHERE query_id = :qid"
        ), {"qid": qid}).scalar()
        assert cnt_s == 0
