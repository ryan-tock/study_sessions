import psycopg2
from contextlib import contextmanager
from .config import DATABASE_URL


@contextmanager
def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()
