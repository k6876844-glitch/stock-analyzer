"""Technical and fundamental values for every NSE stock from TradingView's public scanner.

One request returns, per stock: RSI, MACD, SMA/EMA 20-50-200, Bollinger bands, volatility,
performance over 1W..5Y, average volume, monthly pivot levels, and fundamentals (EPS, P/E,
P/B, dividend yield, ROE, debt/equity, revenue, net income, total assets & liabilities).

The scanner rate-limits some shared cloud IPs (e.g. Render's), so results are kept in three
layers, newest wins: a live fetch -> the last successful fetch saved in the database ->
data/tv_snapshot.json, refreshed daily by the GitHub Actions collector (scripts/collect.py).
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import db
import nse

SNAPSHOT_FILE = Path(__file__).parent / "data" / "tv_snapshot.json"
LIVE_TTL = 15 * 60

COLUMNS = {
    "name": "symbol", "description": "name", "sector": "tv_sector", "industry": "tv_industry",
    "close": "close", "open": "open", "high": "high", "low": "low", "change": "change_pct_100",
    "volume": "volume", "average_volume_30d_calc": "avg_volume_30d", "average_volume_90d_calc": "avg_volume_90d",
    "market_cap_basic": "market_cap", "price_52_week_high": "week52_high", "price_52_week_low": "week52_low",
    "RSI": "rsi14", "MACD.macd": "macd", "MACD.signal": "macd_signal",
    "SMA20": "sma20", "SMA50": "sma50", "SMA200": "sma200",
    "EMA20": "ema20", "EMA50": "ema50", "EMA200": "ema200",
    "BB.upper": "bb_upper", "BB.lower": "bb_lower",
    "Volatility.D": "volatility_d_100", "Volatility.W": "volatility_w_100", "Volatility.M": "volatility_m_100",
    "Perf.W": "perf_1w_100", "Perf.1M": "perf_1m_100", "Perf.3M": "perf_3m_100", "Perf.6M": "perf_6m_100",
    "Perf.YTD": "perf_ytd_100", "Perf.Y": "perf_1y_100", "Perf.5Y": "perf_5y_100",
    "Pivot.M.Classic.S1": "pivot_s1", "Pivot.M.Classic.S2": "pivot_s2",
    "Pivot.M.Classic.R1": "pivot_r1", "Pivot.M.Classic.R2": "pivot_r2", "Pivot.M.Classic.Middle": "pivot",
    "earnings_per_share_basic_ttm": "eps", "price_earnings_ttm": "pe", "price_book_ratio": "pb",
    "dividends_yield": "dividend_yield_100", "dividends_per_share_fq": "dps_fq",
    "return_on_equity": "roe_100", "debt_to_equity": "debt_to_equity",
    "total_revenue": "revenue", "net_income": "net_income",
    "total_assets": "total_assets", "total_liabilities_fy": "total_liabilities",
    "gross_margin": "gross_margin_100", "net_margin": "net_margin_100",
}

_mem = {"at": 0.0, "data": None, "fetched_at": None, "source": None}


def _clean(row):
    out = {}
    for tv, key in COLUMNS.items():
        v = row.get(tv)
        if key.endswith("_100"):
            key = key[:-4]
            v = v / 100 if isinstance(v, (int, float)) else None
        out[key] = v
    return out


def fetch_live():
    """One scanner request for every NSE listing. Raises on failure (e.g. HTTP 429)."""
    cols = list(COLUMNS)
    body = json.dumps({"filter": [{"left": "exchange", "operation": "equal", "right": "NSEKE"}],
                       "columns": cols, "range": [0, 500]}).encode()
    raw = nse._get(nse.TV, LIVE_TTL, data=body, extra_headers={
        "Content-Type": "application/json", "Origin": "https://www.tradingview.com",
        "Referer": "https://www.tradingview.com/"})
    rows = {}
    for r in json.loads(raw)["data"]:
        rec = _clean(dict(zip(cols, r["d"])))
        if rec["symbol"] and nse.SYMBOL_RE.match(rec["symbol"]):
            rows[rec["symbol"]] = rec
    if not rows:
        raise RuntimeError("TradingView returned no NSE rows")
    return rows


def snapshot():
    """{symbol: values} plus where/when they came from. Never raises; may be empty."""
    import time
    now = time.time()
    if _mem["data"] is not None and now - _mem["at"] < LIVE_TTL:
        return _mem
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        data = fetch_live()
        _mem.update(at=now, data=data, fetched_at=stamp, source="TradingView (live request)")
        try:
            db.kv_set("tv_snapshot", json.dumps({"fetched_at": stamp, "data": data}), stamp)
        except Exception as e:
            print(f"tv snapshot not cached: {e!r}", file=sys.stderr, flush=True)
        return _mem
    except Exception as e:
        print(f"TradingView live fetch failed: {e!r}", file=sys.stderr, flush=True)
    best = None
    try:
        row = db.kv_get("tv_snapshot")
        if row:
            best = json.loads(row["v"])
            best["source"] = "TradingView (last successful request, cached)"
    except Exception:
        pass
    if SNAPSHOT_FILE.exists():
        f = json.loads(SNAPSHOT_FILE.read_text())
        if not best or f["fetched_at"] > best["fetched_at"]:
            best = f
            best["source"] = "TradingView (daily snapshot from the data collector)"
    # retry the live request in 5 minutes rather than every call
    _mem.update(at=now - LIVE_TTL + 300, data=(best or {}).get("data", {}),
                fetched_at=(best or {}).get("fetched_at"), source=(best or {}).get("source"))
    return _mem


def get(symbol):
    s = snapshot()
    rec = (s["data"] or {}).get(symbol.upper())
    return rec, {"fetched_at": s["fetched_at"], "source": s["source"]}


def write_snapshot_file(data):
    SNAPSHOT_FILE.parent.mkdir(exist_ok=True)
    payload = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "data": data}
    SNAPSHOT_FILE.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True))
    return SNAPSHOT_FILE
