from contextlib import contextmanager
from psycopg2 import pool
from .config import DATABASE_URL

connection_pool = pool.ThreadedConnectionPool(
    minconn=1,
    maxconn=10,
    dsn=DATABASE_URL
)

@contextmanager
def get_db():
    """Get a DB connection that bypasses RLS (for admin/internal operations).

    Sets app.is_admin='true' so RLS policies allow full access, then
    clears it before returning the connection to the pool.
    """
    conn = connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.is_admin', 'true', false)"
            )
        conn.commit()
        yield conn
    finally:
        try:
            conn.rollback()
        except Exception:
            pass
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.is_admin', 'false', false)"
                )
            conn.commit()
        except Exception:
            pass
        connection_pool.putconn(conn)


@contextmanager
def get_db_for_user(user: dict):
    """Get a DB connection with RLS session variables set for the given user.

    Sets app.current_user_id and app.is_admin at the session level using
    set_config(..., false), then clears them before the connection returns
    to the pool. This is safe for pooled connections: the cleanup in the
    finally block ensures no context leaks between requests.
    """
    conn = connection_pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT set_config('app.current_user_id', %s, false),"
                "       set_config('app.is_admin', %s, false)",
                (str(user["student_id"]), 'true' if user.get("is_admin") else 'false')
            )
        conn.commit()
        yield conn
    finally:
        # Roll back any uncommitted work left by the caller (no-op if already committed)
        try:
            conn.rollback()
        except Exception:
            pass
        # Clear the session vars before the connection goes back to the pool
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('app.current_user_id', '', false),"
                    "       set_config('app.is_admin', 'false', false)"
                )
            conn.commit()
        except Exception:
            pass
        connection_pool.putconn(conn)
