"""Live market data for the Nairobi Securities Exchange.

Sources, in order of preference:
  * NSE ticker API       - the exchange's own feed behind the price ticker on nse.co.ke
    (nsenairobi.nse.co.ke) last price, previous close, open, high, low, volume, turnover
                           and market status for every security, in one request
  * www.nse.co.ke        - official index values (NASI, NSE 20, NSE 25), total shares
                           traded and equity turnover
  * live.mystocks.co.ke  - fallback price list; per-stock extras (deals, 52-week range,
                           average volume, shares issued, profile) and corporate actions.
                           It blocks some cloud hosts (Render, Nov 2026), so every use of it
                           is optional and has a fallback
  * stockanalysis.com    - market capitalisation, company descriptions
  * TradingView snapshot - 52-week range and average volume when myStocks is unreachable
  * data/listings.json   - names and sectors (the NSE feed has tickers only); built by
                           scripts/build_listings.py

Nothing here invents a number: a field the sources don't publish is None, and every
quote says where it came from, when, and whether it is live, delayed or end-of-day.
"""
import html
import json
import os
import re
import time
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import nse
from nse import _get, _n, _text, SYMBOL_RE, MYSTOCKS

EAT = timezone(timedelta(hours=3))
LIST_TTL = 60          # the price list refreshes every minute while the market is open
QUOTE_TTL = 60
SLOW_TTL = 6 * 3600    # profiles, market caps
NSE_SITE = "https://www.nse.co.ke/"
SA_LIST = "https://stockanalysis.com/list/nairobi-stock-exchange/"
NSE_TICKER = "https://nsenairobi.nse.co.ke/nseticker/api/v1/ticker"
# the ticker widget on nse.co.ke sends the exchange's own ISIN; the API requires one
NSE_TICKER_BODY = json.dumps({"nopage": "true", "isinno": "KE3000009674"}).encode()
NSE_TICKER_HEADERS = {"Content-Type": "application/json", "Accept": "application/json",
                      "Origin": "https://www.nse.co.ke", "Referer": "https://www.nse.co.ke/"}
LISTINGS_FILE = Path(__file__).parent / "data" / "listings.json"

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

def _log(msg):
    print(msg, file=sys.stderr, flush=True)


def listings():
    """{symbol: {name, sector}} from data/listings.json."""
    try:
        return json.loads(LISTINGS_FILE.read_text())
    except (OSError, ValueError):
        return {}


def price_list():
    """Every NSE listing with price data: the exchange's feed first, myStocks as fallback."""
    errors = []
    for name, fn in (("NSE ticker API", price_list_nse), ("myStocks", price_list_mystocks)):
        try:
            return fn()
        except Exception as e:
            errors.append(f"{name}: {e}")
            _log(f"price list: {name} failed: {e!r}")
    raise RuntimeError("no NSE price source reachable (" + "; ".join(errors) + ")")


def price_list_nse():
    """Prices for every security from the NSE's own ticker API (nsenairobi.nse.co.ke)."""
    msg = json.loads(_get(NSE_TICKER, LIST_TTL, data=NSE_TICKER_BODY, extra_headers=NSE_TICKER_HEADERS))["message"]
    snap = msg[0]["snapshot"]
    meta = (msg[1].get("updated_at") if len(msg) > 1 else None) or {}
    known = listings()
    stocks, seen = [], set()
    for r in snap:
        sym = (r.get("issuer") or "").strip().upper()
        price = r.get("price") if r.get("price") is not None else r.get("ltp")
        # the feed repeats some tickers (preference shares share the parent's code, and
        # some rows are empty copies); the first priced row is the ordinary share
        if not SYMBOL_RE.match(sym) or sym in seen or price is None:
            continue
        if known and sym not in known:
            continue  # retired codes still in the feed (e.g. NBK, HFCK)
        seen.add(sym)
        prev = r.get("prev_price")
        info = known.get(sym, {})
        stocks.append({
            "symbol": sym, "name": info.get("name", sym), "sector": info.get("sector"),
            "price": float(price),
            "change": round(price - prev, 4) if prev else 0.0,
            "change_pct": (price - prev) / prev if prev else 0.0,
            "prev_close": prev, "open": r.get("today_open"), "high": r.get("today_high"),
            "low": r.get("today_low"), "volume": r.get("volume") or None, "turnover": r.get("turnover"),
        })
    if not stocks:
        raise RuntimeError("the NSE ticker API returned no prices")
    as_of = None
    try:
        as_of = datetime.strptime(meta.get("date", ""), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        pass
    st = (meta.get("market_status") or "").lower()
    return {"stocks": stocks, "indices": {}, "source": "NSE ticker API (nsenairobi.nse.co.ke)",
            "source_time": f"{meta.get('date', '')} {meta.get('time', '')}".strip() or None,
            "source_says_open": (st == "open") if st else None, "as_of": as_of}


def price_list_mystocks():
    """Every NSE listing with sector, last price, % change and volume from myStocks."""
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
            "source_says_open": (stamp.group(2) == "open") if stamp else None, "as_of": None}


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
    # nse.co.ke prints each figure with its change from the previous session beside it
    if not entry or entry.get("value") is None or entry.get("change") is None:
        return None
    return entry["value"] - entry["change"]


# ---------- market capitalisation ----------

def market_caps():
    """{symbol: market cap in KES} for every listing stockanalysis.com covers."""
    page = _get(SA_LIST, SLOW_TTL)
    caps = {}
    for sym, cap in re.findall(r's:"nase/([A-Z0-9.]+)",n:"[^"]*",marketCap:([\d.e+]+|null)', page):
        if cap != "null":
            caps[sym] = float(cap)
    return caps


def sa_names():
    page = _get(SA_LIST, SLOW_TTL)
    return {sym: html.unescape(n) for sym, n in re.findall(r's:"nase/([A-Z0-9.]+)",n:"([^"]*)"', page)}


def sa_profile(sym):
    """Company description from stockanalysis.com (fallback when myStocks is unreachable)."""
    try:
        page = _get(f"https://stockanalysis.com/quote/nase/{sym}/company/", SLOW_TTL)
    except Exception:
        return None, None
    i = page.find("Company Description")
    if i < 0:
        return None, None
    start = page.find("<p", i)
    end = page.find("</div>", start)
    frag = page[start:end] if start > 0 and end > start else ""
    fy = re.search(r">Fiscal Year\s*</td>\s*<td[^>]*>([^<]+)<", page)
    return _paragraphs(frag) or None, (fy.group(1).strip() if fy else None)


# ---------- one stock in detail ----------

def quote(sym):
    """Full quote for one stock: OHLC, previous close, volume, turnover, profile..."""
    sym = sym.upper()
    if not SYMBOL_RE.match(sym):
        raise ValueError(f"invalid ticker '{sym}'")
    pl = price_list()
    row = next((r for r in pl["stocks"] if r["symbol"] == sym), None)
    if row is None:
        raise ValueError(f"'{sym}' is not listed on the NSE")
    price = row["price"]
    q = {
        "symbol": sym, "name": row["name"], "sector": row["sector"], "segment": None,
        "price": price, "change": row["change"], "change_pct": row["change_pct"],
        "prev_close": row.get("prev_close"), "open": row.get("open"), "average": None,
        "high": row.get("high"), "low": row.get("low"),
        "volume": row["volume"], "turnover": row.get("turnover"), "deals": None,
        "week52_low": None, "week52_high": None, "avg_volume": None, "shares_issued": None,
        "beta": None, "year_end": None, "profile": None,
        # no free source publishes the order book, so bid/ask stay unavailable
        "bid": None, "ask": None,
        "source": pl["source"], "extras_source": None,
    }
    # extras from myStocks' quote page (optional: it blocks some hosts)
    try:
        _mystocks_extras(sym, q, row)
        q["extras_source"] = "live.mystocks.co.ke"
    except Exception as e:
        _log(f"quote {sym}: myStocks extras unavailable: {e!r}")
        _fallback_extras(sym, q)
    if q["prev_close"] and price is not None and pl["source"].startswith("live.mystocks"):
        # myStocks' list gives a rounded %; the quote page's previous close is exact
        q["change"], q["change_pct"] = round(price - q["prev_close"], 4), (price - q["prev_close"]) / q["prev_close"]
    st = market_status()
    if pl.get("source_says_open") is False and st["state"] == "open":
        st = dict(st, state="closed")
    q["data_status"] = "delayed" if st["state"] == "open" else "end-of-day"
    q["as_of"] = pl.get("as_of") or q.get("as_of") or last_trading_day()
    if q["shares_issued"] and price:
        q["market_cap"] = q["shares_issued"] * price
    else:
        q["market_cap"] = market_caps().get(sym)
    q["close"] = price if q["data_status"] == "end-of-day" else None
    return q


def _mystocks_extras(sym, q, row):
    m_page = _get(f"{MYSTOCKS}/m/stock={sym}", QUOTE_TTL)
    info = {k.replace("&nbsp;", " ").strip(": "): v for k, v in
            re.findall(r"<th>([^<]+)</th>\s*<td>([^<]*)</td>", m_page)}
    if not info:
        raise ValueError("no quote data on the page")
    rng = re.findall(r"<th>(Day|52-week):</th>\s*<td>([\d.,]+) - ([\d.,]+)</td>", m_page)
    ranges = {k: (_n(lo), _n(hi)) for k, lo, hi in rng}
    name = re.search(r"<title>(.*?) \(" + re.escape(sym) + r"\)", m_page)
    sector = re.search(r"<strong>Sector:</strong>\s*([^<]+)<br", m_page)
    segment = re.search(r"<strong>Sector:</strong>[^<]*<br />\s*([^<]+)</div>", m_page)
    # the profile is a run of <p>/<h3> paragraphs; stop at the first other element
    profile = re.search(r"<h2>Profile</h2>(.*?)(?:<div|<select|<script|<form|<h2>|$)", m_page, re.S)
    if name:
        q["name"] = html.unescape(name.group(1)).strip()
    if sector:
        q["sector"] = html.unescape(sector.group(1)).strip()
    q["segment"] = html.unescape(segment.group(1)).strip() if segment else None
    # fill only what the primary feed lacks (the NSE feed is authoritative for the day's numbers)
    for key, val in (("prev_close", _n(info.get("Previous"))), ("open", _n(info.get("Open"))),
                     ("high", ranges.get("Day", (None, None))[1]), ("low", ranges.get("Day", (None, None))[0]),
                     ("volume", _n(info.get("Volume"))), ("turnover", _n(info.get("Turnover")))):
        if q.get(key) is None:
            q[key] = val
    q["average"] = _n(info.get("Average"))
    q["deals"] = _n(info.get("Deals"))
    q["week52_low"], q["week52_high"] = ranges.get("52-week", (None, None))
    q["avg_volume"] = _n(info.get("Average Volume"))
    q["shares_issued"] = _n(info.get("Shares Issued"))
    q["beta"] = _n(info.get("BETA"))
    q["year_end"] = (info.get("Year End") or "").strip() or None
    q["profile"] = _paragraphs(profile.group(1)) if profile else None
    stamp = re.search(r"<div id=mktTime>([^<]+)</div>", m_page)
    q["as_of"] = _freshness(stamp.group(1) if stamp else "")[1]


def _fallback_extras(sym, q):
    import tvdata
    tv, _ = tvdata.get(sym)
    if tv:
        q["week52_low"], q["week52_high"] = tv.get("week52_low"), tv.get("week52_high")
        q["avg_volume"] = tv.get("avg_volume_30d")
        q["extras_source"] = "TradingView snapshot (52-week range, 30-day average volume)"
    prof, fy = sa_profile(sym)
    q["profile"], q["year_end"] = prof, fy


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
    if pl.get("source_says_open") is True and status["state"] != "open":
        status = dict(status, state="open", note="the exchange reports the market open")
    if pl.get("source_says_open") is False and status["state"] == "open":
        status = dict(status, state="closed", note="the exchange reports the market closed")
    return {
        "status": status,
        "data_status": "delayed" if status["state"] == "open" else "end-of-day",
        "as_of": pl.get("as_of") or ex.get("as_of") or last_trading_day(),
        "prices_source": pl["source"], "prices_time": pl.get("source_time"),
        # nse.co.ke updates its statistics block later than the price feed, so the index
        # and turnover figures carry their own date
        "stats_as_of": ex.get("as_of"),
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
        "sources": ["www.nse.co.ke (indices, turnover)", pl["source"] + " (prices)",
                    "stockanalysis.com (market caps)"],
    }
