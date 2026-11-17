"""Database access: Postgres when DATABASE_URL is set (production, e.g. Neon), SQLite otherwise.

Write SQL with `?` placeholders; they are converted for Postgres. Keep statements portable:
both engines support ON CONFLICT upserts and RETURNING.
"""
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
SQLITE_PATH = os.environ.get("SQLITE_PATH", str(Path(__file__).parent / "data" / "app.db"))

_local = threading.local()
_pool = None
_init_lock = threading.Lock()
_ready = False

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id {ID},
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    pw_hash TEXT NOT NULL,
    email_alerts INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolios (
    id {ID},
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holdings (
    id {ID},
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    quantity REAL NOT NULL,
    price REAL NOT NULL,
    fees REAL NOT NULL DEFAULT 0,
    bought_on TEXT NOT NULL,
    note TEXT
);
CREATE TABLE IF NOT EXISTS dividends_received (
    id {ID},
    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    amount REAL NOT NULL,
    paid_on TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS watchlist (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, symbol)
);
CREATE TABLE IF NOT EXISTS alerts (
    id {ID},
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    value REAL,
    active INTEGER NOT NULL DEFAULT 1,
    email INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    triggered_at TEXT,
    triggered_value REAL
);
CREATE TABLE IF NOT EXISTS notifications (
    id {ID},
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    link TEXT,
    created_at TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS price_history (
    symbol TEXT NOT NULL,
    d TEXT NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    open REAL,
    high REAL,
    low REAL,
    PRIMARY KEY (symbol, d)
);
CREATE TABLE IF NOT EXISTS index_history (
    code TEXT NOT NULL,
    d TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (code, d)
);
CREATE TABLE IF NOT EXISTS corporate_actions (
    symbol TEXT NOT NULL,
    event TEXT NOT NULL,
    d TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (symbol, event, d)
);
CREATE TABLE IF NOT EXISTS kv (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_holdings_portfolio ON holdings(portfolio_id);
CREATE INDEX IF NOT EXISTS ix_alerts_active ON alerts(active);
CREATE INDEX IF NOT EXISTS ix_notifications_user ON notifications(user_id, read);
"""


def _sql(q):
    return q.replace("?", "%s") if PG else q


def _connect():
    if PG:
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, autocommit=False, connect_timeout=10)
    Path(SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(SQLITE_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    c.execute("PRAGMA journal_mode = WAL")
    return c


def _conn():
    c = getattr(_local, "conn", None)
    if c is not None and PG and c.closed:
        c = None
    if c is None:
        c = _local.conn = _connect()
    return c


@contextmanager
def tx():
    """A transaction on this thread's connection; yields a cursor-like runner."""
    init()
    c = _conn()
    try:
        yield _Runner(c)
        c.commit()
    except Exception:
        try:
            c.rollback()
        except Exception:
            _local.conn = None
        raise


class _Runner:
    def __init__(self, c):
        self.c = c

    def all(self, q, args=()):
        cur = self.c.cursor()
        cur.execute(_sql(q), tuple(args))
        rows = cur.fetchall() if cur.description else []
        return [dict(r) for r in rows]

    def one(self, q, args=()):
        rows = self.all(q, args)
        return rows[0] if rows else None

    def run(self, q, args=()):
        cur = self.c.cursor()
        cur.execute(_sql(q), tuple(args))
        return cur.rowcount

    def many(self, q, rows):
        cur = self.c.cursor()
        cur.executemany(_sql(q), [tuple(r) for r in rows])


def all(q, args=()):
    with tx() as t:
        return t.all(q, args)


def one(q, args=()):
    with tx() as t:
        return t.one(q, args)


def run(q, args=()):
    with tx() as t:
        return t.run(q, args)


def init():
    global _ready
    if _ready:
        return
    with _init_lock:
        if _ready:
            return
        c = _conn()
        ident = "SERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
        cur = c.cursor()
        for stmt in SCHEMA.replace("{ID}", ident).split(";"):
            if stmt.strip():
                cur.execute(stmt)
        c.commit()
        _ready = True


def kind():
    return "postgres" if PG else "sqlite"


def kv_get(k):
    r = one("SELECT v, updated_at FROM kv WHERE k = ?", (k,))
    return r


def kv_set(k, v, now):
    run("INSERT INTO kv (k, v, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (k) DO UPDATE SET v = excluded.v, updated_at = excluded.updated_at", (k, v, now))
