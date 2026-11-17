"""Rule-based stock analyst.

Produces educational research write-ups from the app's own data, with no language model:
each conclusion comes from an explicit rule, listed with the number it was based on, so a
reader can check every step. Every report carries its data date, assumptions and risk
warnings. Signals are educational research signals, never recommendations or guarantees.
"""
import re
import statistics

import market
import news
import service
import ta

DISCLAIMER = ("Educational research only, not investment advice. Signals come from fixed rules applied to "
              "public data that may be delayed, incomplete or wrong. Past performance does not predict future "
              "returns, and no profit is guaranteed. Consider your own circumstances and a licensed adviser "
              "(CMA-licensed) before investing.")

BASE_ASSUMPTIONS = [
    "Prices are the latest published by the data sources (delayed during trading, end-of-day otherwise).",
    "Indicators use daily closing prices with standard settings: SMA 20/50/200, RSI 14, MACD 12/26/9, Bollinger 20/2.",
    "Fundamentals are the latest reported (trailing twelve months or last fiscal year); they change only when "
    "companies publish results.",
    "Valuation is compared with the median of other NSE stocks in the same sector; sector norms differ a lot.",
]


def pct(x, d=1):
    return "n/a" if x is None else f"{x * 100:+.{d}f}%"


def pct0(x, d=1):
    return "n/a" if x is None else f"{x * 100:.{d}f}%"


def num(x, d=2):
    return "n/a" if x is None else f"{x:,.{d}f}"


def kes(x):
    if x is None:
        return "n/a"
    for u, dv in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if abs(x) >= dv:
            return f"KES {x / dv:,.2f}{u}"
    return f"KES {x:,.2f}"


# ---------- single stock ----------

def _factors(q, t, f, peers):
    """Score each factor from -2 (bearish) to +2 (bullish) with a plain-English reason."""
    out = []
    L = t.get("latest", {}) if t.get("available") else {}
    m = f["metrics"]
    px = q.get("price")

    def add(group, name, score, value, why):
        out.append({"group": group, "name": name, "score": score, "value": value, "explanation": why})

    # --- technical ---
    trend = (t.get("trend") or {}).get("label")
    if trend and trend != "insufficient data":
        s = {"strong uptrend": 2, "uptrend (pulling back)": 1, "sideways / transitioning": 0,
             "downtrend (bouncing)": -1, "strong downtrend": -2}.get(trend, 0)
        add("Technical", "Trend", s, trend,
            f"Price {num(px)} vs 50-day average {num(L.get('sma50'))} and 200-day average {num(L.get('sma200'))}. "
            + ("Prices above both rising averages usually mean buyers are in control." if s > 0 else
               "Prices below both averages usually mean sellers are in control." if s < 0 else
               "Mixed signals: the averages don't agree on direction."))
    r = L.get("rsi14")
    if r is not None:
        if r >= 70:
            s, why = -1, "RSI above 70 is 'overbought': the price rose fast and often pauses or pulls back."
        elif r <= 30:
            s, why = 1, "RSI below 30 is 'oversold': selling may be stretched and a rebound is common, though not certain."
        elif r >= 55:
            s, why = 1, "RSI between 55 and 70 shows healthy upward momentum."
        elif r <= 45:
            s, why = -1, "RSI between 30 and 45 shows weak momentum."
        else:
            s, why = 0, "RSI near 50 is neutral momentum."
        add("Technical", "Momentum (RSI 14)", s, f"{r:.1f}", why)
    if L.get("macd") is not None and L.get("macd_signal") is not None:
        above = L["macd"] > L["macd_signal"]
        add("Technical", "MACD", 1 if above else -1, f"{L['macd']:.3f} vs signal {L['macd_signal']:.3f}",
            "MACD above its signal line points to strengthening upward momentum." if above else
            "MACD below its signal line points to weakening momentum.")
    bp = L.get("bb_position")
    if bp is not None:
        if bp > 1:
            s, why = -1, "Price is above the upper Bollinger band: unusually stretched upward."
        elif bp < 0:
            s, why = 1, "Price is below the lower Bollinger band: unusually stretched downward; mean reversion is common."
        else:
            s, why = 0, f"Price sits {bp * 100:.0f}% of the way from the lower to the upper Bollinger band."
        add("Technical", "Bollinger bands", s, f"{bp * 100:.0f}% of band", why)
    vr = L.get("volume_vs_avg")
    if vr is not None:
        chg = q.get("change_pct") or 0
        if vr >= 2:
            s = 1 if chg > 0 else -1 if chg < 0 else 0
            why = (f"Volume {vr:.1f}x the average confirms today's {'rise' if chg > 0 else 'fall' if chg < 0 else 'flat close'}"
                   "; heavy volume gives a move more weight.")
        elif vr < 0.3:
            s, why = 0, f"Volume only {vr:.1f}x the average: thin trading makes today's price less reliable."
        else:
            s, why = 0, f"Volume {vr:.1f}x the average: normal activity."
        add("Technical", "Volume", s, f"{vr:.2f}x avg", why)

    # --- fundamental ---
    pe, med = m.get("pe"), peers.get("pe")
    if pe is not None and pe > 0 and med:
        rel = pe / med
        s = 1 if rel < 0.8 else -1 if rel > 1.3 else 0
        add("Fundamental", "Valuation (P/E vs sector)", s, f"P/E {pe:.1f} vs sector median {med:.1f}",
            "Cheaper than sector peers on earnings." if s > 0 else
            "More expensive than sector peers on earnings; growth must justify it." if s < 0 else
            "Priced roughly in line with sector peers.")
    elif m.get("eps") is not None and m["eps"] <= 0:
        add("Fundamental", "Earnings", -2, f"EPS {num(m['eps'])}",
            "The company is loss-making, so P/E is meaningless and the share is valued on hopes of recovery.")
    roe = m.get("roe")
    if roe is not None:
        s = 1 if roe >= 0.15 else -1 if roe < 0.05 else 0
        add("Fundamental", "Return on equity", s, pct0(roe),
            "Earns a strong return on shareholders' money (15%+)." if s > 0 else
            "Earns a weak return on shareholders' money (under 5%)." if s < 0 else "Average return on equity.")
    g = m.get("revenue_growth")
    if g is not None:
        s = 1 if g >= 0.08 else -1 if g < 0 else 0
        add("Fundamental", "Revenue growth", s, pct(g),
            "Sales growing faster than Kenyan inflation." if s > 0 else
            "Sales are shrinking." if s < 0 else "Modest sales growth.")
    de = m.get("debt_to_equity")
    if de is not None:
        bank = (q.get("sector") or "").lower() in ("banking", "insurance", "investment services")
        if bank:
            add("Fundamental", "Leverage", 0, f"{de:.2f}",
                "Debt/equity is not meaningful for banks and insurers, whose business is borrowing and lending.")
        else:
            s = 1 if de < 0.5 else -1 if de > 1.5 else 0
            add("Fundamental", "Leverage", s, f"{de:.2f}",
                "Low debt relative to equity." if s > 0 else
                "High debt: rising interest rates hurt more." if s < 0 else "Moderate debt.")
    dy = m.get("dividend_yield")
    if dy is not None:
        s = 1 if dy >= 0.06 else 0
        add("Fundamental", "Dividend yield", s, pct0(dy),
            "Pays a high dividend yield (6%+) relative to the share price." if s else
            "Dividend yield is modest." if dy > 0 else "No current dividend.")
    return out


def _risks(q, t, f, factors):
    risks = []
    L = t.get("latest", {}) if t.get("available") else {}
    m = f["metrics"]
    av = q.get("avg_volume")
    if av is not None and q.get("price") and av * q["price"] < 1_000_000:
        risks.append(f"Liquidity: average daily trading is about {kes(av * q['price'])}; large orders may be hard "
                     "to fill without moving the price.")
    if q.get("volume") in (None, 0):
        risks.append("The stock did not trade in the latest session, so the price may be stale.")
    vol = L.get("volatility_1y") or L.get("volatility_30d")
    if vol and vol > 0.4:
        risks.append(f"High volatility: annualised swings of about {vol * 100:.0f}%.")
    if L.get("max_drawdown_1y") is not None and L["max_drawdown_1y"] < -0.3:
        risks.append(f"The share fell {abs(L['max_drawdown_1y']) * 100:.0f}% from a peak within the last year.")
    if m.get("eps") is not None and m["eps"] <= 0:
        risks.append("Loss-making: dividends and the share price depend on a return to profit.")
    if m.get("debt_to_equity") and m["debt_to_equity"] > 1.5 and (q.get("sector") or "").lower() not in ("banking", "insurance"):
        risks.append("High debt makes earnings sensitive to interest rates (CBK's rate decisions).")
    if m.get("payout_ratio") and m["payout_ratio"] > 1:
        risks.append("Dividends exceed earnings (payout ratio over 100%), which is hard to sustain.")
    sector = (q.get("sector") or "").lower()
    if sector in ("banking", "insurance"):
        risks.append("Banks and insurers are sensitive to CBK rate changes, loan defaults and regulation.")
    if sector == "telecommunication":
        risks.append("Telecoms face regulatory, tax (excise duty) and foreign-currency risks, including the Ethiopia business.")
    if sector in ("agricultural",):
        risks.append("Agricultural firms depend on weather and global commodity prices.")
    risks.append("Market-wide risks: Kenyan inflation, shilling exchange rate, interest rates, foreign investor "
                 "flows and political events affect all NSE stocks.")
    missing = [k for k in ("pe", "roe", "revenue_growth", "debt_to_equity", "dividend_yield") if m.get(k) is None]
    if missing:
        risks.append("Some fundamentals are unavailable (" + ", ".join(missing).replace("_", " ")
                     + "), so this analysis covers fewer factors.")
    return risks


def analyze(sym):
    d = service.stock(sym)
    q, t, f = d["quote"], d["technicals"], d["fundamentals"]
    rows = service.screener_rows()["rows"]
    sector = q.get("sector")
    peers = {"pe": service.sector_medians(rows, "pe").get(sector)}
    factors = _factors(q, t, f, peers)
    total = sum(x["score"] for x in factors)
    tech = [x for x in factors if x["group"] == "Technical"]
    fund = [x for x in factors if x["group"] == "Fundamental"]
    if total >= 4:
        signal = "Buy"
    elif total <= -3:
        signal = "Sell"
    else:
        signal = "Hold"
    coverage = len(factors) / 10
    confidence = "high" if coverage >= 0.8 else "medium" if coverage >= 0.5 else "low"
    L = t.get("latest", {}) if t.get("available") else {}
    perf = t.get("performance") or {}
    m = f["metrics"]
    name = q.get("name") or sym
    summary = [
        f"{name} ({sym}) last traded at {kes(q.get('price'))}, {pct(q.get('change_pct'))} on the day "
        f"({q.get('data_status')} data as of {q.get('as_of')}).",
    ]
    if t.get("available"):
        summary.append(
            f"The trend is {t['trend']['label']}. Over one month the share moved {pct(perf.get('1M'))}, "
            f"year-to-date {pct(perf.get('YTD'))} and over one year {pct(perf.get('1Y'))}. "
            f"RSI is {num(L.get('rsi14'), 1)}.")
    else:
        summary.append("Technical indicators are unavailable: " + t.get("reason", "not enough price history") + ".")
    fin_bits = []
    if m.get("revenue") is not None:
        fin_bits.append(f"revenue of {kes(m['revenue'])}" + (f" ({pct(m.get('revenue_growth'))} year on year)" if m.get("revenue_growth") is not None else ""))
    if m.get("net_income") is not None:
        fin_bits.append(f"net profit of {kes(m['net_income'])}")
    if m.get("roe") is not None:
        fin_bits.append(f"return on equity of {pct0(m['roe'])}")
    if fin_bits:
        summary.append("Financially, the latest figures show " + ", ".join(fin_bits) + ".")
    if m.get("pe") is not None:
        summary.append(f"It trades at {num(m['pe'], 1)} times earnings"
                       + (f" against a sector median of {num(peers['pe'], 1)}" if peers.get("pe") else "")
                       + (f", with a dividend yield of {pct0(m['dividend_yield'])}" if m.get("dividend_yield") is not None else "")
                       + ".")
    pos = [x["name"] for x in factors if x["score"] > 0]
    neg = [x["name"] for x in factors if x["score"] < 0]
    reasoning = (f"Research signal: {signal} (score {total:+d} across {len(factors)} factors). "
                 + (f"Supporting: {', '.join(pos)}. " if pos else "")
                 + (f"Against: {', '.join(neg)}. " if neg else "")
                 + "A score of +4 or more reads as Buy, -3 or less as Sell, anything between as Hold.")
    return {
        "type": "stock",
        "symbol": sym, "name": name,
        "signal": signal, "score": total, "confidence": confidence,
        "headline": f"{signal} (educational research signal) — {name}",
        "summary": summary, "reasoning": reasoning,
        "technical_factors": tech, "fundamental_factors": fund,
        "support": t.get("support"), "resistance": t.get("resistance"),
        "risks": _risks(q, t, f, factors),
        "data_date": {"prices": q.get("as_of"), "price_status": q.get("data_status"),
                      "indicators": t.get("as_of"), "indicator_source": t.get("source"),
                      "fundamentals": "latest reported period (stockanalysis.com / TradingView)"},
        "assumptions": BASE_ASSUMPTIONS + [f"Sector peers: other NSE stocks in '{sector}'."],
        "disclaimer": DISCLAIMER,
    }


# ---------- comparison ----------

COMPARE_FIELDS = [
    ("price", "Price", "kes", None), ("market_cap", "Market cap", "kes", "high"),
    ("perf_1y", "1-year return", "pct", "high"), ("perf_ytd", "Year-to-date", "pct", "high"),
    ("pe", "P/E", "num", "low"), ("eps", "EPS", "num", "high"), ("pb", "P/B", "num", "low"),
    ("dividend_yield", "Dividend yield", "pct", "high"), ("roe", "ROE", "pct", "high"),
    ("debt_to_equity", "Debt/equity", "num", "low"), ("rsi14", "RSI 14", "num", None),
]


def compare(symbols):
    symbols = [s.upper() for s in symbols][:6]
    rows = {r["symbol"]: r for r in service.screener_rows()["rows"]}
    missing = [s for s in symbols if s not in rows]
    syms = [s for s in symbols if s in rows]
    if len(syms) < 2:
        raise ValueError("pick at least two listed stocks to compare")
    table, leaders = [], {}
    for key, label, fmt, better in COMPARE_FIELDS:
        vals = {s: rows[s].get(key) for s in syms}
        best = None
        have = {s: v for s, v in vals.items() if v is not None and not (key == "pe" and v <= 0)}
        if better and len(have) >= 2:
            best = (max if better == "high" else min)(have, key=have.get)
            leaders.setdefault(best, []).append(label)
        table.append({"key": key, "label": label, "format": fmt, "values": vals, "best": best})
    analyses = {}
    for s in syms:
        try:
            a = analyze(s)
            analyses[s] = {"signal": a["signal"], "score": a["score"], "trend": next(
                (x["value"] for x in a["technical_factors"] if x["name"] == "Trend"), "n/a")}
        except Exception as e:
            analyses[s] = {"signal": "n/a", "score": None, "trend": "n/a", "error": str(e)}
    ranked = sorted(syms, key=lambda s: -(analyses[s]["score"] if analyses[s]["score"] is not None else -99))
    lines = []
    for s in syms:
        lead = leaders.get(s)
        lines.append(f"{s} ({rows[s]['name']}): {analyses[s]['signal']} signal, score {analyses[s]['score']:+d}, "
                     f"trend {analyses[s]['trend']}" if analyses[s]["score"] is not None else f"{s}: analysis unavailable")
        if lead:
            lines[-1] += f"; leads on {', '.join(lead)}"
        lines[-1] += "."
    sectors = {rows[s]["sector"] for s in syms}
    note = ("These companies are in different sectors, so compare valuation ratios with care."
            if len(sectors) > 1 else f"All are in {next(iter(sectors))}, so ratios are directly comparable.")
    return {
        "type": "compare", "symbols": syms, "missing": missing, "table": table,
        "analyses": analyses, "ranking": ranked,
        "summary": [f"Ranked by research score: {', '.join(ranked)}.", *lines, note],
        "risks": ["A higher score is not a prediction; it only means more rules point the same way.",
                  "Different fiscal year-ends mean fundamentals may cover different periods.",
                  "Less-traded stocks can show large price moves on small volumes."],
        "data_date": {"prices": service.snapshot()["as_of"], "indicators": service.screener_rows()["indicators_as_of"]},
        "assumptions": BASE_ASSUMPTIONS,
        "disclaimer": DISCLAIMER,
    }


# ---------- daily market report ----------

def market_report():
    snap = service.snapshot()
    stocks = [s for s in snap["stocks"] if s["volume"]]
    by_sector = {}
    for s in stocks:
        by_sector.setdefault(s["sector"] or "Other", []).append(s["change_pct"])
    sectors = sorted(({"sector": k, "avg_change": statistics.mean(v), "stocks": len(v)}
                      for k, v in by_sector.items()), key=lambda x: -x["avg_change"])
    idx = snap["indices"]
    nasi = idx.get("NASI", {})
    adv, dec = snap["advancers"], snap["decliners"]
    breadth = ("broadly positive" if adv > dec * 1.5 else "broadly negative" if dec > adv * 1.5 else "mixed")
    paras = [
        f"Market status: {snap['status']['state']} ({snap['status']['note']}). Data as of {snap['as_of']} "
        f"({snap['data_status']}).",
        (f"The NSE All Share Index (NASI) closed at {num(nasi.get('value'))}, {pct(nasi.get('change_pct'), 2)} on the day. "
         if nasi else "")
        + "; ".join(f"{v['name']} {num(v['value'])} ({pct(v.get('change_pct'), 2)})" for k, v in idx.items() if k in ("N20I", "N25I"))
        + ".",
        f"Breadth was {breadth}: {adv} stocks rose, {dec} fell and {snap['unchanged']} were unchanged; "
        f"{snap['not_traded']} did not trade. Equity turnover was {kes(snap.get('equity_turnover'))}"
        + (f" versus {kes(snap.get('equity_turnover_prev'))} in the previous session." if snap.get("equity_turnover_prev") else "."),
    ]
    if snap["gainers"]:
        paras.append("Top gainers: " + ", ".join(f"{s['symbol']} {pct(s['change_pct'])}" for s in snap["gainers"][:5]) + ".")
    if snap["losers"]:
        paras.append("Top losers: " + ", ".join(f"{s['symbol']} {pct(s['change_pct'])}" for s in snap["losers"][:5]) + ".")
    if snap["most_traded"]:
        paras.append("Most traded by volume: " + ", ".join(f"{s['symbol']} ({s['volume']:,.0f} shares)" for s in snap["most_traded"][:5]) + ".")
    if sectors:
        paras.append(f"Strongest sector on average: {sectors[0]['sector']} ({pct(sectors[0]['avg_change'])}); "
                     f"weakest: {sectors[-1]['sector']} ({pct(sectors[-1]['avg_change'])}).")
    heavy = snap["most_traded"][0] if snap["most_traded"] else None
    if heavy and heavy["symbol"] == "SCOM":
        paras.append("Safaricom is the largest NSE stock by far, so its moves dominate NASI.")
    try:
        headlines = news.topic("market", 6)
    except Exception:
        headlines = []
    try:
        cbk = news.cbk(3)
    except Exception:
        cbk = []
    return {
        "type": "market",
        "headline": f"NSE daily report for {snap['as_of']}: breadth {breadth}",
        "summary": paras, "sectors": sectors, "indices": idx,
        "explanation": ("Moves are described, not explained by cause. Headlines below may be related, "
                        "but a headline appearing here doesn't mean it moved the market."),
        "headlines": headlines, "cbk": cbk,
        "risks": ["One day's moves on the NSE are often driven by a few large trades.",
                  "Many NSE stocks trade thinly, so percentage moves can be large on small volume."],
        "data_date": {"prices": snap["as_of"], "status": snap["data_status"], "fetched_at": snap["fetched_at"]},
        "assumptions": ["Index levels and turnover are the exchange's published figures (nse.co.ke).",
                        "Sector averages are simple averages of traded stocks' daily % changes."],
        "disclaimer": DISCLAIMER,
    }


# ---------- education ----------

GLOSSARY = {
    "rsi": ("Relative Strength Index (RSI)",
            "Measures how fast and how far the price has moved over 14 days, on a 0-100 scale. Above 70 is "
            "called overbought (the price rose quickly and may pause), below 30 oversold (selling may be "
            "overdone). In strong trends RSI can stay extreme for weeks, so treat it as context, not a trigger."),
    "macd": ("MACD",
             "The difference between the 12-day and 26-day exponential moving averages, with a 9-day average of "
             "that difference as the 'signal line'. MACD crossing above the signal suggests improving momentum; "
             "crossing below suggests fading momentum. It lags the price."),
    "sma": ("Simple moving average (SMA)",
            "The average closing price over the last N days. The 50-day shows the medium-term trend and the "
            "200-day the long-term trend. The 50 crossing above the 200 is called a golden cross (bullish); "
            "below, a death cross (bearish)."),
    "ema": ("Exponential moving average (EMA)",
            "Like an SMA but weights recent prices more, so it reacts faster to new moves."),
    "bollinger": ("Bollinger bands",
                  "Bands two standard deviations above and below the 20-day average. About 95% of closes fall "
                  "inside them in normal conditions; touching a band means the move is unusually large. "
                  "Narrow bands often come before big moves."),
    "support": ("Support and resistance",
                "Support is a price level where buying has repeatedly stopped falls; resistance is where selling "
                "has repeatedly capped rises. They're found from past swing lows and highs and often, but not "
                "always, hold again."),
    "volatility": ("Volatility",
                   "How much the price swings, measured as the annualised standard deviation of daily returns. "
                   "Higher volatility means bigger potential gains and losses."),
    "pe": ("Price-to-earnings (P/E)",
           "Share price divided by earnings per share: how many years of current profit you pay for. Compare "
           "within a sector; a low P/E can mean good value or a business in trouble."),
    "pb": ("Price-to-book (P/B)",
           "Share price divided by net assets per share. Common for valuing banks; below 1 means the market "
           "values the company at less than its accounting net worth."),
    "dividend yield": ("Dividend yield",
                       "Dividends paid over the last year divided by the share price. Kenyan dividends are taxed "
                       "at source (withholding tax)."),
    "roe": ("Return on equity (ROE)",
            "Net profit divided by shareholders' equity: how well the company turns owners' money into profit."),
    "eps": ("Earnings per share (EPS)", "Net profit divided by the number of shares."),
    "debt to equity": ("Debt-to-equity", "Total debt divided by shareholders' equity: how much the company relies on borrowing."),
}


def explain(term):
    key = term.lower().strip()
    for k, (title, body) in GLOSSARY.items():
        if key == k or key in title.lower() or k in key:
            return {"type": "explain", "headline": title, "summary": [body], "risks": [],
                    "data_date": None, "assumptions": [], "disclaimer": DISCLAIMER}
    raise ValueError("I can explain: " + ", ".join(t for t, _ in GLOSSARY.values()))


# ---------- free-text questions ----------

def ask(question):
    """Route a plain-English question to the right analysis."""
    q = (question or "").strip()
    if not q:
        raise ValueError("Ask about a stock, a comparison, the market today, or an indicator.")
    known = service.prices()
    names = {s["symbol"]: s["name"].lower() for s in known.values()}
    found = []
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9&.-]+", q):
        t = tok.upper()
        if t in known and t not in found and len(t) >= 2 and t not in ("ON", "OR", "AND", "THE", "IS", "IT", "TO"):
            found.append(t)
    low = q.lower()
    for sym, nm in names.items():
        first = re.split(r"\s+", nm.replace("plc", "").strip())[0]
        if len(first) >= 4 and re.search(r"\b" + re.escape(first) + r"\b", low) and sym not in found:
            found.append(sym)
    if re.search(r"\b(compare|vs|versus|better)\b", low) and len(found) >= 2:
        return compare(found)
    if found:
        return analyze(found[0])
    if re.search(r"\b(market|today|nasi|report|summary|index)\b", low):
        return market_report()
    for k in GLOSSARY:
        if k in low:
            return explain(k)
    raise ValueError("I couldn't find a listed stock or topic in that. Try 'Analyze SCOM', "
                     "'Compare KCB and EQTY', 'Market report' or 'What is RSI?'.")
