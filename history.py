"""Daily price history.

The Render host can't reach afx.kwayisi.org, the only free source of NSE price history we
found, so history comes from two places:
  1. data/history.csv.gz, built from afx with `python scripts/build_history.py` on a
     machine that can reach it and committed to the repo (10 years of daily closes);
  2. a daily end-of-day snapshot of every stock that the app itself records from the
     live price list (see jobs.py), which also stores volume.
Both go into the price_history table. Index values recorded each day go into index_history.
"""
import csv
import gzip
import io
from pathlib import Path

import db

SEED = Path(__file__).parent / "data" / "history.csv.gz"


def load_seed(force=False):
    """Import the bundled history file into the database (idempotent)."""
    if not SEED.exists():
        return 0
    marker = db.kv_get("history_seed")
    stamp = str(int(SEED.stat().st_mtime)) + ":" + str(SEED.stat().st_size)
    if marker and marker["v"] == stamp and not force:
        return 0
    with gzip.open(SEED, "rt", newline="") as f:
        rows = [(r["symbol"], r["date"], float(r["close"])) for r in csv.DictReader(f)]
    with db.tx() as t:
        t.many("INSERT INTO price_history (symbol, d, close) VALUES (?, ?, ?) "
               "ON CONFLICT (symbol, d) DO NOTHING", rows)
    from market import now_eat
    db.kv_set("history_seed", stamp, now_eat().isoformat())
    return len(rows)


def record_day(day, stocks):
    """Store one session's closes (and volume / OHLC where known) for every stock."""
    rows = [(s["symbol"], day, s["price"], s.get("volume"), s.get("open"), s.get("high"), s.get("low"))
            for s in stocks if s.get("price")]
    with db.tx() as t:
        t.many("INSERT INTO price_history (symbol, d, close, volume, open, high, low) "
               "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (symbol, d) DO UPDATE SET "
               "close = excluded.close, volume = excluded.volume, open = COALESCE(excluded.open, price_history.open), "
               "high = COALESCE(excluded.high, price_history.high), low = COALESCE(excluded.low, price_history.low)",
               rows)
    return len(rows)


def record_indices(day, indices):
    rows = [(code, day, v["value"]) for code, v in indices.items() if v.get("value") is not None]
    with db.tx() as t:
        t.many("INSERT INTO index_history (code, d, value) VALUES (?, ?, ?) "
               "ON CONFLICT (code, d) DO UPDATE SET value = excluded.value", rows)


def series(symbol, since=None):
    """[(date, close, volume)] oldest first."""
    q = "SELECT d, close, volume FROM price_history WHERE symbol = ?"
    args = [symbol.upper()]
    if since:
        q += " AND d >= ?"
        args.append(since)
    return [(r["d"], r["close"], r["volume"]) for r in db.all(q + " ORDER BY d", args)]


def many(symbols, since):
    """{symbol: [(date, close)]} for several stocks at once."""
    if not symbols:
        return {}
    marks = ",".join("?" * len(symbols))
    out = {s: [] for s in symbols}
    for r in db.all(f"SELECT symbol, d, close FROM price_history WHERE symbol IN ({marks}) AND d >= ? "
                    "ORDER BY d", [*symbols, since]):
        out[r["symbol"]].append((r["d"], r["close"]))
    return out


def index_series(code, since=None):
    q = "SELECT d, value FROM index_history WHERE code = ?"
    args = [code]
    if since:
        q += " AND d >= ?"
        args.append(since)
    return [(r["d"], r["value"]) for r in db.all(q + " ORDER BY d", args)]


def latest_dates():
    return {r["symbol"]: r["d"] for r in db.all("SELECT symbol, MAX(d) AS d FROM price_history GROUP BY symbol")}


def to_csv_gz(rows):
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        w = io.TextIOWrapper(gz, encoding="utf-8", newline="")
        cw = csv.writer(w)
        cw.writerow(["symbol", "date", "close"])
        cw.writerows(rows)
        w.flush()
        w.detach()
    return buf.getvalue()
