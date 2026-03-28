import os
import sys

import pytest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)


def _is_truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _ensure_safe_test_database(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        row = cur.fetchone()
    db_name = str((row[0] if row else "") or "")
    if _is_truthy(os.environ.get("XXBOT_TEST_ALLOW_DB_RESET")):
        return db_name
    if "test" not in db_name.lower():
        raise RuntimeError(
            f"Refuse to reset non-test database: '{db_name}'. "
            "Use a dedicated test DB (name contains 'test') or set XXBOT_TEST_ALLOW_DB_RESET=1 explicitly."
        )
    return db_name


def _reset_public_tables(conn) -> None:
    """Truncate all public tables for deterministic PostgreSQL test runs."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT tablename
            FROM pg_tables
            WHERE schemaname = 'public'
            """
        )
        names = [str(row[0]) for row in (cur.fetchall() or []) if row and row[0]]
        if not names:
            conn.commit()
            return
        quoted = ", ".join(f'"{name}"' for name in names)
        cur.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
    conn.commit()


@pytest.fixture()
def test_db():
    from core.database.connection import connect_db, create_tables, close_db
    conn = connect_db()
    _ensure_safe_test_database(conn)
    _reset_public_tables(conn)
    create_tables(conn)
    yield conn
    _reset_public_tables(conn)
    close_db()


def create_user(user_id: str, username: str, rank: int = 1, element: str = "火"):
    from core.database.connection import execute
    import time
    execute(
        "INSERT INTO users (user_id, in_game_username, rank, element, created_at, exp, copper, gold) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (user_id, username, rank, element, int(time.time()), 1000, 1000, 0),
    )
