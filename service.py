"""Application services: combine market data, history, indicators and fundamentals.

Kept separate from the HTTP layer so the analyst, jobs and API all share one view of a stock.
"""
import statistics
import threading
import time
from datetime import date, timedelta

import fundamentals
import history
import market
import ta
import tvdata

MIN_HISTORY = 60  # days of stored closes needed before we compute indicators ourselves
_cache, _lock = {}, threading.Lock()


def cached(key, ttl, fn):
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _lock:
        _cache[key] = (now, val)
    return val


def snapshot():
    return cached("snapshot", 60, market.market_snapshot)


def prices():
    """{symbol: list-row} from the live price list."""
    return {s["symbol"]: s for s in snapshot()["stocks"]}


def technicals(sym, quote=None, years=10):
    """Indicators from stored history when we have enough; TradingView's values otherwise."""
    since = (date.today() - timedelta(days=int(365.25 * years))).isoformat()
    pts = history.series(sym, since)
    tv, meta = tvdata.get(sym)
    q = quote or {}
    if q.get("price") and q.get("as_of") and (not pts or pts[-1][0] < q["as_of"]):
        pts = pts + [(q["as_of"], q["price"], q.get("volume"))]  # include today's session
    if len(pts) >= MIN_HISTORY:
        dates = [p[0] for p in pts]
        closes = [p[1] for p in pts]
        vols = [p[2] for p in pts]
        out = ta.analyze(dates, closes, vols, avg_volume=q.get("avg_volume"), today_volume=q.get("volume"))
        out["source"] = "computed from stored daily closes"
        if tv:  # TradingView's 5-year performance needs more history than we may hold
            for k, tk in (("5Y", "perf_5y"),):
                if out["performance"].get(k) is None and tv.get(tk) is not None:
                    out["performance"][k] = tv[tk]
        return out
    if not tv:
        return {"available": False,
                "reason": f"only {len(pts)} day{'s' if len(pts) != 1 else ''} of price history stored so far; indicators need {MIN_HISTORY}",
                "history_points": len(pts),
                "series": {"dates": [p[0] for p in pts], "close": [p[1] for p in pts]}}
    px = q.get("price") or tv.get("close")
    up, lo = tv.get("bb_upper"), tv.get("bb_lower")
    vol_ratio = None
    if q.get("volume") and (q.get("avg_volume") or tv.get("avg_volume_30d")):
        vol_ratio = q["volume"] / (q.get("avg_volume") or tv["avg_volume_30d"])
    s50, s200 = tv.get("sma50"), tv.get("sma200")
    if s50 and s200 and px:
        label = ("strong uptrend" if px > s50 > s200 else "uptrend (pulling back)" if px > s200 and s50 > s200
                 else "strong downtrend" if px < s50 < s200 else "downtrend (bouncing)" if px < s200 and s50 < s200
                 else "sideways / transitioning")
    else:
        label = "insufficient data"
    sup = [{"level": round(v, 2), "touches": None} for v in (tv.get("pivot_s1"), tv.get("pivot_s2")) if v]
    res = [{"level": round(v, 2), "touches": None} for v in (tv.get("pivot_r1"), tv.get("pivot_r2")) if v]
    return {
        "available": True,
        "as_of": (meta.get("fetched_at") or "")[:10] or None,
        "points": len(pts),
        "source": f"{meta.get('source') or 'TradingView'}; stored history too short ({len(pts)} day{'s' if len(pts) != 1 else ''}) to compute locally",
        "latest": {
            "close": px, "sma20": tv.get("sma20"), "sma50": s50, "sma200": s200,
            "ema12": None, "ema26": None, "ema20": tv.get("ema20"), "ema50": tv.get("ema50"),
            "rsi14": tv.get("rsi14"), "macd": tv.get("macd"), "macd_signal": tv.get("macd_signal"),
            "macd_hist": (tv["macd"] - tv["macd_signal"]) if tv.get("macd") is not None and tv.get("macd_signal") is not None else None,
            "bb_upper": up, "bb_lower": lo, "bb_middle": tv.get("sma20"),
            "bb_position": (px - lo) / (up - lo) if px and up and lo and up != lo else None,
            # TradingView's Volatility.M is the average daily high-low range over a month,
            # not the standard deviation of returns, so it gets its own field
            "volatility_30d": None, "avg_daily_range_1m": tv.get("volatility_m"),
            "volatility_1y": None, "max_drawdown_1y": None, "volume_vs_avg": vol_ratio,
        },
        "trend": {"label": label, "detail": "price vs. 50- and 200-day moving averages (TradingView values)"},
        "support": sup, "resistance": res,
        "support_method": "monthly classic pivot levels (TradingView)",
        "performance": {"1D": q.get("change_pct"), "1W": tv.get("perf_1w"), "1M": tv.get("perf_1m"),
                        "3M": tv.get("perf_3m"), "6M": tv.get("perf_6m"), "YTD": tv.get("perf_ytd"),
                        "1Y": tv.get("perf_1y"), "3Y": None, "5Y": tv.get("perf_5y")},
        "crosses_50_200": [], "macd_crosses": [],
        "series": {"dates": [p[0] for p in pts], "close": [p[1] for p in pts]},
    }


def stock(sym):
    """Quote + technicals + fundamentals for one stock."""
    sym = sym.upper()
    q = cached(("quote", sym), 60, lambda: market.quote(sym))
    t = cached(("tech", sym), 300, lambda: technicals(sym, q))
    f = cached(("fund", sym), 3600, lambda: fundamentals.full(sym, q.get("price")))
    return {"quote": q, "technicals": t, "fundamentals": f}


def screener_rows():
    """One row per stock with the fields the screener filters on. Two upstream requests."""
    snap = snapshot()
    tv = tvdata.snapshot()
    tvd = tv["data"] or {}
    rows = []
    for s in snap["stocks"]:
        t = tvd.get(s["symbol"], {})
        pe = t.get("pe")
        rows.append({
            "symbol": s["symbol"], "name": s["name"], "sector": s["sector"],
            "price": s["price"], "change_pct": s["change_pct"], "volume": s["volume"],
            "market_cap": s.get("market_cap") or t.get("market_cap"),
            "pe": pe if pe and pe > 0 else None, "eps": t.get("eps"),
            "pb": t.get("pb") if t.get("pb") else None,  # TradingView reports 0 when book value is missing
            "dividend_yield": t.get("dividend_yield"), "roe": t.get("roe"),
            "debt_to_equity": t.get("debt_to_equity"), "rsi14": t.get("rsi14"),
            "perf_1m": t.get("perf_1m"), "perf_ytd": t.get("perf_ytd"), "perf_1y": t.get("perf_1y"),
            "avg_volume": t.get("avg_volume_30d"),
            "above_sma50": (s["price"] > t["sma50"]) if s["price"] and t.get("sma50") else None,
            "above_sma200": (s["price"] > t["sma200"]) if s["price"] and t.get("sma200") else None,
            "net_margin": t.get("net_margin"), "revenue": t.get("revenue"),
        })
    return {"rows": rows, "prices_as_of": snap["as_of"], "data_status": snap["data_status"],
            "indicators_as_of": tv["fetched_at"], "indicators_source": tv["source"]}


def sector_medians(rows, field):
    by = {}
    for r in rows:
        if r.get(field) is not None and r.get("sector"):
            by.setdefault(r["sector"], []).append(r[field])
    return {k: statistics.median(v) for k, v in by.items() if len(v) >= 2}


def composite_index(years=5):
    """Market-cap-weighted composite of stored closes (NOT an official index).

    Uses today's shares outstanding for every past day, so it approximates NASI's shape;
    the app labels it as computed.
    """
    snap = snapshot()
    caps = {s["symbol"]: s["market_cap"] / s["price"] for s in snap["stocks"] if s.get("market_cap") and s.get("price")}
    if not caps:
        return {"dates": [], "value": []}
    since = (date.today() - timedelta(days=365 * years)).isoformat()
    hist = history.many(sorted(caps), since)
    last, idx = {}, {s: 0 for s in hist}
    dates = sorted({d for pts in hist.values() for d, _ in pts})
    vals = []
    base = None
    for d in dates:
        for s, pts in hist.items():
            while idx[s] < len(pts) and pts[idx[s]][0] <= d:
                last[s] = pts[idx[s]][1]
                idx[s] += 1
        if len(last) < len(caps) * 0.6:
            vals.append(None)
            continue
        total = sum(caps[s] * p for s, p in last.items())
        base = base or total
        vals.append(round(total / base * 100, 2))
    keep = [(d, v) for d, v in zip(dates, vals) if v is not None]
    return {"dates": [d for d, _ in keep], "value": [v for _, v in keep]}
