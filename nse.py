"""Nairobi Securities Exchange (Kenya) data.

Two public sources, combined into one record per stock:
  * afx.kwayisi.org    - live price, 52-week range (from 10y of daily closes), the
                         full list of NSE tickers
  * stockanalysis.com  - fundamentals: ratios, margins, balance sheet, 5 years of
                         financial statements, analyst targets

Pages are cached and only fetched when someone asks for a ticker.
"""
import html
import re
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

AFX = "https://afx.kwayisi.org"
SA = "https://stockanalysis.com/quote/nase"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}
PRICE_TTL = 30 * 60          # prices update once per trading session
FUNDAMENTALS_TTL = 6 * 3600  # statements change a few times a year
_cache = {}
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,10}$")


# ---------- helpers ----------

def _get(url, ttl):
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = r.read().decode("utf-8", errors="replace")
    _cache[url] = (time.time(), body)
    return body


def _text(s):
    s = re.sub(r"<!--.*?-->", "", s or "", flags=re.S)
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).split()


def _n(s, scale=1.0):
    """Parse '1,507', '3.77M', '6.69%', '-80,150', '1.38T' -> float (percent -> fraction)."""
    if isinstance(s, list):
        s = s[0] if s else ""
    s = (s or "").strip().split(" ")[0].replace(",", "")
    if not s or s in ("-", "—", "n/a", "N/A"):
        return None
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100
        except ValueError:
            return None
    mult = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(s[-1].upper())
    if mult:
        s = s[:-1]
    try:
        return float(s) * (mult or scale)
    except ValueError:
        return None


def _rows(page):
    """Every table row as a list of cell texts (th and td)."""
    page = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>", "", page, flags=re.S)
    out = []
    for tr in re.split(r"<tr", page)[1:]:
        tr = tr.split("</tr>")[0]
        cells = re.split(r"<t[dh][^>]*>", tr)[1:]
        if cells:
            out.append([" ".join(_text(c)) for c in cells])
    return out


def cagr(values):
    """CAGR from a newest-first list of annual values."""
    vals = [v for v in values if v is not None]
    if len(vals) < 2 or vals[-1] <= 0 or vals[0] <= 0:
        return None
    return (vals[0] / vals[-1]) ** (1 / (len(vals) - 1)) - 1


# ---------- market list (afx.kwayisi.org) ----------

def list_stocks():
    page = _get(f"{AFX}/nse/", PRICE_TTL)
    i = page.find("Listed companies")
    rows = []
    for tr in re.findall(r"<tr>(.*?)(?=<tr>|</table>)", page[i:], re.S):
        cells = re.split(r"<td[^>]*>", tr)[1:]
        if len(cells) < 5:
            continue
        sym = " ".join(_text(cells[0]))
        if not SYMBOL_RE.match(sym):
            continue
        rows.append({
            "symbol": sym,
            "name": " ".join(_text(cells[1])),
            "volume": _n(_text(cells[2])),
            "price": _n(_text(cells[3])),
            "change": _n(_text(cells[4])),
        })
    if not rows:
        raise RuntimeError("could not read the NSE listing page (layout may have changed)")
    return rows


# ---------- price (afx.kwayisi.org) ----------

def _price(sym):
    try:
        page = _get(f"{AFX}/nse/{sym.lower()}.html", PRICE_TTL)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(f"'{sym}' is not listed on the NSE") from None
        raise
    m = re.search(r'<abbr title="([^"]+)">' + re.escape(sym) + r"</abbr>.*?display:inline-block>([\d,.]+)", page, re.S)
    if not m:
        raise ValueError(f"no data found for '{sym}'")
    name, price = html.unescape(m.group(1)), _n(m.group(2))
    kv = {k.strip(): v for k, v in re.findall(r"<tr><td>([^<]+)<td[^>]*>([^<]*)", page)}

    def fact(label):
        f = re.search(rf"<dt>{label}<dd>(.*?)</div>", page, re.S)
        return " ".join(_text(f.group(1))) if f else None

    # 52-week range from daily closes
    js = _get(f"{AFX}/chart/nse/{sym.lower()}", PRICE_TTL)
    hist = re.findall(r'd\("(\d{4}-\d\d-\d\d)"\),([-\d.]+)\]', js)
    hi = lo = None
    if hist:
        cut = (date.fromisoformat(hist[-1][0]) - timedelta(days=365)).isoformat()
        yr = [float(v) for d, v in hist if d >= cut] + ([price] if price else [])
        hi, lo = max(yr), min(yr)
    return {
        "name": name, "price": price, "52w_high": hi, "52w_low": lo,
        "market_cap": _n(kv.get("Market Capitalization")),
        "eps": _n(kv.get("Earnings Per Share")), "pe": _n(kv.get("Price/Earning Ratio")),
        "dps": _n(kv.get("Dividend Per Share")),
        "sector": fact("Sector"), "industry": fact("Industry"),
    }


# ---------- fundamentals (stockanalysis.com) ----------

def _fundamentals(sym):
    try:
        stats_page = _get(f"{SA}/{sym}/statistics/", FUNDAMENTALS_TTL)
        fin_page = _get(f"{SA}/{sym}/financials/", FUNDAMENTALS_TTL)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {}, {}
        raise

    stats = {}
    for r in _rows(stats_page):
        if len(r) >= 2 and r[0] and r[0] not in stats:
            stats[r[0]] = r[1] or ""  # cell may carry trailing link text; _n() reads the first token

    # Overview tables: a "Fiscal Year" header row, then metric rows. Skip TTM/Current columns.
    hist = {"years": [], "revenue": [], "net_income": [], "eps": [], "free_cash_flow": []}
    want = {"Revenue": ("revenue", 1e6), "Net Income": ("net_income", 1e6),
            "Earnings Per Share": ("eps", 1.0), "Free Cash Flow": ("free_cash_flow", 1e6)}
    growth = {}
    header = []
    for r in _rows(fin_page):
        if r[0] == "Fiscal Year":
            header = r[1:]
            continue
        # first cell is "Revenue Revenue Growth" (label + tooltip) or "Revenue Growth"
        label = next((k for k in want if r[0] == k or r[0].startswith(k + " ")
                      and not r[0].startswith(k + " Growth")), None)
        if r[0] in ("Revenue Growth", "EPS Growth") and r[1:]:
            growth[r[0]] = _n(r[1])  # first column = TTM (or latest FY)
        if not label or hist[want[label][0]]:
            continue
        key, scale = want[label]
        cols = [(h, v) for h, v in zip(header, r[1:]) if h.startswith("FY")]
        if not hist["years"]:
            hist["years"] = [int(re.search(r"\d{4}", h).group()) for h, _ in cols]
        hist[key] = [_n(v, scale) for _, v in cols]  # scale: statements are in millions of KES
    n = len(hist["years"])
    for k in ("revenue", "net_income", "eps", "free_cash_flow"):
        hist[k] = (hist[k] + [None] * n)[:n]
    stats["_growth"] = growth
    return stats, hist


# ---------- combined record (same shape the analyzer/page expect) ----------

def fetch(symbol):
    sym = symbol.strip().upper()
    if not SYMBOL_RE.match(sym):
        raise ValueError(f"invalid ticker '{symbol}'")
    p = _price(sym)
    s, hist = _fundamentals(sym)
    g = s.get("_growth", {})

    def S(label):
        return _n(s.get(label))

    price, mcap = p["price"], p["market_cap"] or S("Market Cap")
    fcf_latest = next((v for v in hist.get("free_cash_flow", []) if v is not None), None)
    if fcf_latest is None:
        fcf_latest = S("Free Cash Flow")
    pe = S("PE Ratio") or p["pe"]
    rating = s.get("Analyst Consensus")
    rating = None if not rating or rating == "n/a" else rating

    return {
        "symbol": sym,
        "name": p["name"],
        "sector": p["sector"],
        "industry": p["industry"],
        "currency": "KES",
        "price": price,
        "market_cap": mcap,
        "52w_high": p["52w_high"],
        "52w_low": p["52w_low"],
        # valuation
        "pe_trailing": pe if pe and pe > 0 else None,
        "pe_forward": S("Forward PE"),
        "peg": S("PEG Ratio"),
        "price_to_sales": S("PS Ratio"),
        "price_to_book": S("PB Ratio"),
        "ev_to_ebitda": S("EV / EBITDA"),
        "fcf_yield": fcf_latest / mcap if fcf_latest is not None and mcap else None,
        # growth
        "revenue_growth_yoy": g.get("Revenue Growth"),
        "earnings_growth_yoy": g.get("EPS Growth"),
        "revenue_cagr": cagr(hist.get("revenue", [])),
        "eps_cagr": cagr(hist.get("eps", [])),
        # profitability
        "gross_margin": S("Gross Margin"),
        "operating_margin": S("Operating Margin"),
        "net_margin": S("Profit Margin"),
        "roe": S("Return on Equity (ROE)"),
        "roa": S("Return on Assets (ROA)"),
        # balance sheet
        "total_cash": S("Cash & Cash Equivalents"),
        "total_debt": S("Total Debt"),
        "debt_to_equity": S("Debt / Equity"),
        "current_ratio": S("Current Ratio"),
        "quick_ratio": S("Quick Ratio"),
        "free_cash_flow": fcf_latest,
        # dividends (yield in percent, like the old Yahoo field)
        "dividend_yield_pct": (p["dps"] / price * 100) if p["dps"] and price
                              else (S("Dividend Yield") * 100 if S("Dividend Yield") is not None else None),
        "payout_ratio": S("Payout Ratio"),
        # street
        "analyst_target": S("Price Target"),
        "analyst_rating": rating,
        "analyst_count": int(S("Analyst Count")) if S("Analyst Count") else None,
        "history": hist or {"years": [], "revenue": [], "net_income": [], "eps": [], "free_cash_flow": []},
    }
