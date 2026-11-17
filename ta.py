"""Technical indicators computed from daily closing prices.

Everything works on a plain list of closes (oldest first) and returns lists aligned with
it (None where the indicator isn't defined yet). Standard parameter choices:
SMA 20/50/200, EMA 12/26, RSI 14 (Wilder), MACD 12/26/9, Bollinger 20 / 2 sd.
"""
import math
from datetime import date, timedelta


def sma(xs, n):
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def ema(xs, n):
    out, k, prev = [], 2 / (n + 1), None
    for i, x in enumerate(xs):
        if i < n - 1:
            out.append(None)
            continue
        prev = sum(xs[:n]) / n if prev is None else x * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(xs, n=14):
    out = [None] * len(xs)
    if len(xs) <= n:
        return out
    gains = [max(xs[i] - xs[i - 1], 0) for i in range(1, len(xs))]
    losses = [max(xs[i - 1] - xs[i], 0) for i in range(1, len(xs))]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for i in range(n, len(xs)):
        if i > n:
            ag = (ag * (n - 1) + gains[i - 1]) / n
            al = (al * (n - 1) + losses[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def macd(xs, fast=12, slow=26, signal=9):
    f, s = ema(xs, fast), ema(xs, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    start = next((i for i, v in enumerate(line) if v is not None), len(line))
    sig_part = ema([v for v in line[start:]], signal) if len(line) - start >= signal else []
    sig = [None] * start + sig_part + [None] * (len(line) - start - len(sig_part))
    hist = [a - b if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def bollinger(xs, n=20, k=2):
    mid = sma(xs, n)
    up, lo = [], []
    for i, m in enumerate(mid):
        if m is None:
            up.append(None)
            lo.append(None)
            continue
        w = xs[i - n + 1:i + 1]
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / n)
        up.append(m + k * sd)
        lo.append(m - k * sd)
    return up, mid, lo


def daily_returns(xs):
    return [xs[i] / xs[i - 1] - 1 for i in range(1, len(xs)) if xs[i - 1]]


def volatility(xs, days=None):
    """Annualised standard deviation of daily returns (252 trading days)."""
    r = daily_returns(xs[-(days + 1):] if days else xs)
    if len(r) < 10:
        return None
    m = sum(r) / len(r)
    return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(252)


def max_drawdown(xs):
    peak, worst = None, 0.0
    for x in xs:
        peak = x if peak is None or x > peak else peak
        worst = min(worst, x / peak - 1)
    return worst


def support_resistance(closes, lookback=120, window=5, tolerance=0.02):
    """Price levels where swing lows / highs cluster in the recent past.

    A swing low is a close lower than the `window` closes on each side. Nearby swings
    (within `tolerance`) are merged; levels touched more often rank higher.
    """
    xs = closes[-lookback:]
    if len(xs) < window * 2 + 1:
        return [], []
    lows, highs = [], []
    for i in range(window, len(xs) - window):
        seg = xs[i - window:i + window + 1]
        if xs[i] == min(seg):
            lows.append(xs[i])
        if xs[i] == max(seg):
            highs.append(xs[i])

    def cluster(pts):
        groups = []
        for p in sorted(pts):
            if groups and abs(p - groups[-1][-1]) / groups[-1][-1] <= tolerance:
                groups[-1].append(p)
            else:
                groups.append([p])
        return [{"level": round(sum(g) / len(g), 2), "touches": len(g)} for g in groups]

    price = xs[-1]
    sup = sorted([c for c in cluster(lows) if c["level"] <= price], key=lambda c: -c["level"])[:3]
    res = sorted([c for c in cluster(highs) if c["level"] >= price], key=lambda c: c["level"])[:3]
    # fall back to the range extremes when no swing sits on that side of the price
    if not sup:
        sup = [{"level": round(min(xs), 2), "touches": 1}]
    if not res:
        res = [{"level": round(max(xs), 2), "touches": 1}]
    return sup, res


def performance(dates, closes):
    """% change over standard windows, measured from the close on or before each start date."""
    if not closes:
        return {}
    last_d = date.fromisoformat(dates[-1])
    windows = {"1D": None, "1W": 7, "1M": 30, "3M": 91, "6M": 182, "YTD": "ytd", "1Y": 365, "3Y": 1095, "5Y": 1826}
    out = {}
    for k, days in windows.items():
        if days is None:
            base = closes[-2] if len(closes) > 1 else None
        else:
            start = date(last_d.year, 1, 1) - timedelta(days=1) if days == "ytd" else last_d - timedelta(days=days)
            idx = None
            for i in range(len(dates) - 1, -1, -1):
                if date.fromisoformat(dates[i]) <= start:
                    idx = i
                    break
            base = closes[idx] if idx is not None else None
        out[k] = closes[-1] / base - 1 if base else None
    return out


def trend(closes):
    """Classify the trend from where the price sits against its 50- and 200-day averages."""
    s50, s200 = sma(closes, 50)[-1], sma(closes, 200)[-1]
    p = closes[-1]
    if s50 is None:
        return {"label": "insufficient data", "detail": "fewer than 50 days of prices"}
    slope50 = None
    if len(closes) >= 70:
        prev50 = sma(closes, 50)[-21]
        slope50 = s50 / prev50 - 1 if prev50 else None
    if s200 is None:
        label = "uptrend" if p > s50 and (slope50 or 0) > 0 else "downtrend" if p < s50 and (slope50 or 0) < 0 else "sideways"
        return {"label": label, "detail": "based on the 50-day average only (under 200 days of data)",
                "sma50_slope_1m": slope50}
    if p > s50 > s200:
        label = "strong uptrend"
    elif p > s200 and s50 > s200:
        label = "uptrend (pulling back)"
    elif p < s50 < s200:
        label = "strong downtrend"
    elif p < s200 and s50 < s200:
        label = "downtrend (bouncing)"
    else:
        label = "sideways / transitioning"
    return {"label": label, "detail": "price vs. 50- and 200-day moving averages", "sma50_slope_1m": slope50}


def cross_events(fast, slow, dates, lookback=60):
    """Golden / death crosses of two series within the last `lookback` points."""
    out = []
    for i in range(max(1, len(fast) - lookback), len(fast)):
        a0, b0, a1, b1 = fast[i - 1], slow[i - 1], fast[i], slow[i]
        if None in (a0, b0, a1, b1):
            continue
        if a0 <= b0 and a1 > b1:
            out.append({"date": dates[i], "type": "bullish"})
        elif a0 >= b0 and a1 < b1:
            out.append({"date": dates[i], "type": "bearish"})
    return out


def analyze(dates, closes, volumes=None, avg_volume=None, today_volume=None):
    """All indicators plus a latest-value summary for one stock."""
    n = len(closes)
    if n < 2:
        return {"available": False, "reason": "not enough price history"}
    s20, s50, s200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    e12, e26 = ema(closes, 12), ema(closes, 26)
    r = rsi(closes)
    m_line, m_sig, m_hist = macd(closes)
    bb_up, bb_mid, bb_lo = bollinger(closes)
    sup, res = support_resistance(closes)
    last = lambda xs: xs[-1] if xs else None  # noqa: E731
    p = closes[-1]
    bb_pos = None
    if last(bb_up) is not None and last(bb_up) != last(bb_lo):
        bb_pos = (p - last(bb_lo)) / (last(bb_up) - last(bb_lo))
    vol_ratio = None
    if today_volume and avg_volume:
        vol_ratio = today_volume / avg_volume
    elif volumes:
        vv = [v for v in volumes if v]
        if len(vv) >= 6:
            vol_ratio = vv[-1] / (sum(vv[-21:-1]) / len(vv[-21:-1]))
    yr = closes[-252:]
    return {
        "available": True,
        "as_of": dates[-1],
        "points": n,
        "latest": {
            "close": p,
            "sma20": last(s20), "sma50": last(s50), "sma200": last(s200),
            "ema12": last(e12), "ema26": last(e26),
            "rsi14": last(r),
            "macd": last(m_line), "macd_signal": last(m_sig), "macd_hist": last(m_hist),
            "bb_upper": last(bb_up), "bb_middle": last(bb_mid), "bb_lower": last(bb_lo),
            "bb_position": bb_pos,
            "volatility_30d": volatility(closes, 30),
            "volatility_1y": volatility(closes, 252),
            "max_drawdown_1y": max_drawdown(yr),
            "volume_vs_avg": vol_ratio,
        },
        "trend": trend(closes),
        "support": sup,
        "resistance": res,
        "performance": performance(dates, closes),
        "crosses_50_200": cross_events(s50, s200, dates, 120),
        "macd_crosses": cross_events(m_line, m_sig, dates, 30),
        "series": {
            "dates": dates, "close": closes,
            "sma20": s20, "sma50": s50, "sma200": s200, "ema12": e12, "ema26": e26,
            "rsi14": r, "macd": m_line, "macd_signal": m_sig, "macd_hist": m_hist,
            "bb_upper": bb_up, "bb_middle": bb_mid, "bb_lower": bb_lo,
            "volume": volumes or [None] * n,
        },
    }
