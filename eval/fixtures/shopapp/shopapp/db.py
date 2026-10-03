"""SQLite access helpers. All queries go through `query`/`execute` with bound parameters."""
import sqlite3
from contextlib import contextmanager

_DB_PATH = "shop.db"


@contextmanager
def connect(path: str = _DB_PATH):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def query(sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def execute(sql: str, params: tuple = ()) -> int:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid
