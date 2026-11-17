"""Live market data for the Nairobi Securities Exchange.

Sources (all public, all reachable from the Render host as of Nov 2026):
  * live.mystocks.co.ke  - price list with sectors, per-stock quote details (open, previous
                           close, day range, volume, turnover, deals, 52-week range,
                           average volume, shares issued, profile), corporate actions
  * www.nse.co.ke        - official index values (NASI, NSE 20, NSE 25), total shares
                           traded and equity turnover
  * stockanalysis.com    - market capitalisation for every listing in one request

Nothing here invents a number: a field the sources don't publish is None, and every
quote says where it came from, when, and whether it is live, delayed or end-of-day.
"""
import html
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

import nse
from nse import _get, _n, _text, SYMBOL_RE, MYSTOCKS

EAT = timezone(timedelta(hours=3))
LIST_TTL = 60          # the price list refreshes every minute while the market is open
QUOTE_TTL = 60
SLOW_TTL = 6 * 3600    # profiles, market caps
NSE_SITE = "https://www.nse.co.ke/"
SA_LIST = "https://stockanalysis.com/list/nairobi-stock-exchange/"

INDEX_NAMES = {"NASI": "NSE All Share Index", "N20I": "NSE 20 Share Index",
               "N25I": "NSE 25 Share Index", "N10I": "NSE 10 Share Index",
               "NBSI": "NSE Banking Sector Index", "NBDI": "NSE Bonds Index"}

# Kenyan public holidays when the NSE does not trade. Moveable feasts (Easter, Idd)
# follow the official gazette; extend this list each year.
HOLIDAYS = {
    "2026-01-01", "2026-03-20", "2026-04-03", "2026-04-06", "2026-05-01", "2026-05-27",
    "2026-06-01", "2026-10-10", "2026-10-20", "2026-12-12", "2026-12-25", "2026-12-26",
    "2027-01-01", "2027-03-10", "2027-03-26", "2027-03-29", "2027-05-01", "2027-05-17",
    "2027-06-01", "2027-10-10", "2027-10-20", "2027-12-13", "2027-12-25", "2027-12-26",
}
# Continuous trading, EAT. The NSE has announced trading until 4:00 PM from January 2027;
# set NSE_CLOSE=16:00 on the host once the new hours take effect.
OPEN_AT = (9, 30)
CLOSE_AT = tuple(int(x) for x in os.environ.get("NSE_CLOSE", "15:00").split(":"))


def now_eat():
    return datetime.now(EAT)


def market_status(at=None):
    """Whether the NSE is trading now, from the published schedule."""
    t = at or now_eat()
    day = t.strftime("%Y-%m-%d")
    hm = (t.hour, t.minute)
    if t.weekday() >= 5:
        state, why = "closed", "weekend"
    elif day in HOLIDAYS:
        state, why = "closed", "public holiday"
    elif hm < (9, 0):
        state, why = "closed", "opens 9:30 AM EAT"
    elif hm < OPEN_AT:
        state, why = "pre-open", "pre-open session; trading starts 9:30 AM EAT"
    elif hm < CLOSE_AT:
        state, why = "open", f"continuous trading until {_hm(CLOSE_AT)} EAT"
    else:
        state, why = "closed", f"closed for the day at {_hm(CLOSE_AT)} EAT"
    nxt = _next_open(t)
    return {"state": state, "note": why, "now": t.isoformat(timespec="minutes"),
            "next_open": nxt.isoformat(timespec="minutes") if state != "open" else None}


def _hm(hm):
    h, m = hm
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


def _next_open(t):
    d = t
    if (t.hour, t.minute) >= OPEN_AT:
        d = t + timedelta(days=1)
    d = d.replace(hour=OPEN_AT[0], minute=OPEN_AT[1], second=0, microsecond=0)
    while d.weekday() >= 5 or d.strftime("%Y-%m-%d") in HOLIDAYS:
        d += timedelta(days=1)
    return d


def last_trading_day(at=None):
    """The most recent date with a completed or in-progress session."""
    t = at or now_eat()
    d = t if (t.hour, t.minute) >= OPEN_AT else t - timedelta(days=1)
    while d.weekday() >= 5 or d.strftime("%Y-%m-%d") in HOLIDAYS:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def _freshness(stamp_text):
    """mystocks stamps quotes 'End of day - Nov 17, 2026' or '11:05 AM EAT'."""
    s = (stamp_text or "").strip()
    if not s:
        return "unknown", None
    m = re.match(r"End of day - (\w{3} \d{1,2}, \d{4})", s)
    if m:
        return "end-of-day", datetime.strptime(m.group(1), "%b %d, %Y").strftime("%Y-%m-%d")
    # intraday stamp: mystocks shows non-members quotes with a delay
    return "delayed", now_eat().strftime("%Y-%m-%d") + " " + s


# ---------- price list (all stocks, one request) ----------

def price_list():
    """Every NSE listing with sector, last price, % change and volume."""
    page = _get(f"{MYSTOCKS}/m/pricelist", LIST_TTL)
    names = {v: html.unescape(n).strip()
             for v, n in re.findall(r"<option value=([A-Z0-9-]+)>([^<\n]+)", page)}
    stocks, indices = [], {}
    sector = None
    # walk rows in order so each stock picks up the sector heading above it
    for m in re.finditer(r'<h2>([^<]+)</h2>|<a href="/m/stock=([^"]+)">[^<]*</a><br>\s*'
                         r'<td class=n>([^<]*)<td class="n x([-\d]+)">([^<]*)<td class=n>([^<]*)', page):
        if m.group(1):
            sector = html.unescape(m.group(1)).strip()
            continue
        sym = html.unescape(m.group(2)).replace("%5E", "^").replace("%255E", "^")
        price = _n(m.group(3))
        chg = html.unescape(m.group(5)).replace("▲", "").replace("▼", "-").replace("\xa0", "").replace(" ", "")
        pct = _n(chg)
        if sym.startswith("^"):
            indices[sym[1:]] = {"value": price, "change_pct": pct}
            continue
        if not SYMBOL_RE.match(sym):
            continue
        prev = price / (1 + pct) if price and pct is not None and pct != -1 else None
        stocks.append({
            "symbol": sym, "name": names.get(sym, sym), "sector": sector,
            "price": price, "change_pct": pct if pct is not None else 0.0,
            # approximate: implied by a % change the source rounds to 2 decimals; the stock
            # page shows the exact previous close and change
            "change": round(price - prev, 2) if prev else 0.0,
            "prev_close_approx": round(prev, 2) if prev else price,
            "volume": _n(m.group(6)),
        })
    if not stocks:
        raise RuntimeError("could not read the myStocks price list (layout may have changed)")
    stamp = re.search(r"(\d{1,2}:\d\d [AP]M EAT)\s*-\s*market (open|closed)", page)
    return {"stocks": stocks, "indices": indices, "source": "live.mystocks.co.ke",
            "source_time": stamp.group(1) if stamp else None,
            "source_says_open": (stamp.group(2) == "open") if stamp else None}


# ---------- official exchange statistics ----------

def exchange_stats():
    """NASI / NSE 20 / NSE 25, shares traded and equity turnover from nse.co.ke."""
    page = _get(NSE_SITE, 15 * 60)
    asof = re.search(r"Statistics As of (\d{2}-[A-Z]{3}-\d{2})", page)
    out = {}
    for name, val, chg in re.findall(
            r"<h3>([^<]+)</h3>\s*</div>\s*<div class=\"stat_figures\">\s*<p>([^<]+)<span[^>]*>([^<]*)</span>", page):
        out[name.strip()] = {"value": _n(val), "change": _n(chg)}
    if not out:
        raise RuntimeError("could not read nse.co.ke statistics (layout may have changed)")
    d = datetime.strptime(asof.group(1), "%d-%b-%y").strftime("%Y-%m-%d") if asof else None
    idx = {}
    for code, key in (("NASI", "N.A.S.I"), ("N20I", "NSE 20"), ("N25I", "NSE 25")):
        if key in out and out[key]["value"] is not None:
            v, c = out[key]["value"], out[key]["change"]
            idx[code] = {"name": INDEX_NAMES[code], "value": v, "change": c,
                         "change_pct": c / (v - c) if c is not None and v != c else None}
    return {
        "as_of": d, "source": "www.nse.co.ke", "indices": idx,
        "shares_traded": out.get("T.SHARE", {}).get("value"),
        "shares_traded_prev": _prev(out.get("T.SHARE")),
        "equity_turnover": out.get("E.TURNOVER", {}).get("value"),
        "equity_turnover_prev": _prev(out.get("E.TURNOVER")),
    }


def _prev(entry):
    # nse.co.ke shows the previous session's figure beside today's for volume/turnover
    return entry.get("change") if entry else None


# ---------- market capitalisation ----------

def market_caps():
    """{symbol: market cap in KES} for every listing stockanalysis.com covers."""
    page = _get(SA_LIST, SLOW_TTL)
    caps = {}
    for sym, cap in re.findall(r's:"nase/([A-Z0-9.]+)",n:"[^"]*",marketCap:([\d.e+]+|null)', page):
        if cap != "null":
            caps[sym] = float(cap)
    return caps


# ---------- one stock in detail ----------

def quote(sym):
    """Full quote for one stock: OHLC, previous close, volume, turnover, profile..."""
    sym = sym.upper()
    if not SYMBOL_RE.match(sym):
        raise ValueError(f"invalid ticker '{sym}'")
    # price, change and direction come from the price list: the mobile quote page omits
    # the change for unchanged stocks and truncates prices of 100 or more ("KES 100" for 100.25)
    row = next((r for r in price_list()["stocks"] if r["symbol"] == sym), None)
    if row is None:
        raise ValueError(f"'{sym}' is not listed on the NSE")
    m_page = _get(f"{MYSTOCKS}/m/stock={sym}", QUOTE_TTL)
    info = {k.replace("&nbsp;", " ").strip(": "): v for k, v in
            re.findall(r"<th>([^<]+)</th>\s*<td>([^<]*)</td>", m_page)}
    rng = re.findall(r"<th>(Day|52-week):</th>\s*<td>([\d.,]+) - ([\d.,]+)</td>", m_page)
    ranges = {k: (_n(lo), _n(hi)) for k, lo, hi in rng}
    stamp = re.search(r"<div id=mktTime>([^<]+)</div>", m_page)
    status, as_of = _freshness(stamp.group(1) if stamp else "")
    name = re.search(r"<title>(.*?) \(" + re.escape(sym) + r"\)", m_page)
    sector = re.search(r"<strong>Sector:</strong>\s*([^<]+)<br", m_page)
    segment = re.search(r"<strong>Sector:</strong>[^<]*<br />\s*([^<]+)</div>", m_page)
    # the profile is a run of <p>/<h3> paragraphs; stop at the first other element
    profile = re.search(r"<h2>Profile</h2>(.*?)(?:<div|<select|<script|<form|<h2>|$)", m_page, re.S)
    price, change, pct = row["price"], row["change"], row["change_pct"]
    prev = _n(info.get("Previous"))
    if prev and price is not None:  # exact, unlike the change implied by a rounded % figure
        change, pct = round(price - prev, 4), (price - prev) / prev
    q = {
        "symbol": sym,
        "name": html.unescape(name.group(1)).strip() if name else row["name"],
        "sector": html.unescape(sector.group(1)).strip() if sector else row["sector"],
        "segment": html.unescape(segment.group(1)).strip() if segment else None,
        "price": price, "change": change, "change_pct": pct,
        "prev_close": prev, "open": _n(info.get("Open")),
        "average": _n(info.get("Average")),
        "high": ranges.get("Day", (None, None))[1], "low": ranges.get("Day", (None, None))[0],
        "volume": row["volume"] if row["volume"] is not None else _n(info.get("Volume")),
        "turnover": _n(info.get("Turnover")),
        "deals": _n(info.get("Deals")),
        "week52_low": ranges.get("52-week", (None, None))[0],
        "week52_high": ranges.get("52-week", (None, None))[1],
        "avg_volume": _n(info.get("Average Volume")),
        "shares_issued": _n(info.get("Shares Issued")),
        "beta": _n(info.get("BETA")),
        "year_end": (info.get("Year End") or "").strip() or None,
        "profile": _paragraphs(profile.group(1)) if profile else None,
        # myStocks shows the order book only to logged-in members, so bid/ask are not public
        "bid": None, "ask": None,
        "data_status": status, "as_of": as_of,
        "source": "live.mystocks.co.ke",
    }
    if q["shares_issued"] and price:
        q["market_cap"] = q["shares_issued"] * price
    else:
        q["market_cap"] = market_caps().get(sym)
    # mystocks rounds the day range to the trade prices; close is the last price on EOD data
    q["close"] = price if status == "end-of-day" else None
    return q


def _paragraphs(fragment):
    """HTML paragraphs/headings -> plain text with blank lines between blocks."""
    blocks = re.split(r"</?(?:p|h\d|br)\b[^>]*>", fragment)
    out = [" ".join(_text(b)) for b in blocks]
    return "\n\n".join(b for b in out if b) or None


def corporate_actions(sym):
    """Dividends, book closures, AGMs etc. that myStocks lists for a stock."""
    page = _get(f"{MYSTOCKS}/stock={sym.upper()}", SLOW_TTL)
    block = re.search(r"<div id=rtAnnouncements>(.*?)</div>\s*</div>", page, re.S)
    out = []
    for ev, d in re.findall(r"<span class=hpEvent>([^<]+)</span>\s*<small class=date>([^<]+)</small>",
                            block.group(1) if block else page):
        try:
            iso = datetime.strptime(d.strip(), "%b %d %Y").strftime("%Y-%m-%d")
        except ValueError:
            iso = d.strip()
        out.append({"event": html.unescape(ev).strip(), "date": iso})
    return out


def dividends_from_actions(actions):
    """Pull 'Payment of KES 1.15 final dividend' style events into amounts."""
    out = []
    for a in actions:
        m = re.search(r"Payment of KES ([\d.,]+) (.*?dividend)", a["event"], re.I)
        if m:
            out.append({"date": a["date"], "amount": _n(m.group(1)), "type": m.group(2)})
    return out


def market_snapshot():
    """Everything the dashboard needs, fetched with as few requests as possible."""
    pl = price_list()
    try:
        ex = exchange_stats()
    except Exception as e:
        ex = {"error": str(e), "indices": {}}
    try:
        caps = market_caps()
    except Exception:
        caps = {}
    for s in pl["stocks"]:
        s["market_cap"] = caps.get(s["symbol"])
    # indices: prefer the exchange's own figures, fall back to myStocks
    idx = dict(ex.get("indices") or {})
    for code, v in pl["indices"].items():
        if code not in idx and code in INDEX_NAMES and v["value"] is not None:
            pct = v["change_pct"]
            idx[code] = {"name": INDEX_NAMES[code], "value": v["value"],
                         "change": v["value"] - v["value"] / (1 + pct) if pct is not None else None,
                         "change_pct": pct, "source": "live.mystocks.co.ke"}
    traded = [s for s in pl["stocks"] if s["volume"]]
    status = market_status()
    if pl.get("source_says_open") is False and status["state"] == "open":
        status = dict(status, state="closed", note="the exchange reports the market closed")
    return {
        "status": status,
        "data_status": "delayed" if status["state"] == "open" else "end-of-day",
        "as_of": ex.get("as_of") or last_trading_day(),
        "fetched_at": now_eat().isoformat(timespec="seconds"),
        "indices": idx,
        "total_market_cap": sum(caps.values()) or None,
        "market_cap_coverage": f"{len(caps)} of {len(pl['stocks'])} listings",
        "equity_turnover": ex.get("equity_turnover"),
        "equity_turnover_prev": ex.get("equity_turnover_prev"),
        "shares_traded": ex.get("shares_traded"),
        "advancers": sum(1 for s in traded if s["change_pct"] > 0),
        "decliners": sum(1 for s in traded if s["change_pct"] < 0),
        "unchanged": sum(1 for s in traded if s["change_pct"] == 0),
        "not_traded": len(pl["stocks"]) - len(traded),
        "gainers": sorted([s for s in traded if s["change_pct"] > 0], key=lambda s: -s["change_pct"])[:8],
        "losers": sorted([s for s in traded if s["change_pct"] < 0], key=lambda s: s["change_pct"])[:8],
        "most_traded": sorted(traded, key=lambda s: -(s["volume"] or 0))[:8],
        "stocks": pl["stocks"],
        "sources": ["www.nse.co.ke (indices, turnover)", "live.mystocks.co.ke (prices)",
                    "stockanalysis.com (market caps)"],
    }
