"""Nairobi Securities Exchange (Kenya) data.

Public sources, combined into one record per stock:
  * TradingView scanner - price, day change, 52-week range, sector and the full list
                          of NSE tickers, all in one request
  * afx.kwayisi.org     - fallback for prices and the list when TradingView fails or
                          lacks a ticker (it drops connections from some cloud hosts,
                          e.g. Render, so after a network failure it is skipped a while)
  * stockanalysis.com  - fundamentals: ratios, margins, balance sheet, 5 years of
                         financial statements, analyst targets

Pages are cached and only fetched when someone asks for a ticker.
"""
import html
import http.client
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from datetime import date, timedelta

AFX = "https://afx.kwayisi.org"
TV = "https://scanner.tradingview.com/kenya/scan"
TV_COLS = ["name", "description", "close", "change", "volume", "price_52_week_high",
           "price_52_week_low", "market_cap_basic", "sector", "industry",
           "price_earnings_ttm", "earnings_per_share_basic_ttm"]
AFX_RETRY = 30 * 60  # after a network failure, leave afx alone this long
_afx_down_until = 0.0
SA = "https://stockanalysis.com/quote/nase"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"}
PRICE_TTL = 30 * 60          # prices update once per trading session
FUNDAMENTALS_TTL = 6 * 3600  # statements change a few times a year
_cache = {}
SYMBOL_RE = re.compile(r"^[A-Z0-9]{1,10}$")


# ---------- helpers ----------

CONNECT_TIMEOUT = 5   # per address; the source hosts publish 4-6 addresses each
READ_TIMEOUT = 20
CONNECT_BUDGET = 12  # seconds across all addresses of one host


def _connect(address, timeout=None, source_address=None, **kw):
    """Like socket.create_connection, but IPv4 first, a short timeout per address and
    a cap on the total time spent connecting.

    The stock version tries every DNS address in turn with the full timeout, so a host
    with six addresses that the server can't reach (common for IPv6 on cloud hosts)
    stalls a request for minutes.
    """
    host, port = address
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    infos.sort(key=lambda i: i[0] != socket.AF_INET)
    deadline = time.time() + CONNECT_BUDGET
    errors = []
    for family, socktype, proto, _, addr in infos:
        left = deadline - time.time()
        if left <= 0:
            break
        sock = socket.socket(family, socktype, proto)
        try:
            sock.settimeout(min(CONNECT_TIMEOUT, left))
            if source_address:
                sock.bind(source_address)
            sock.connect(addr)
            sock.settimeout(timeout)
            return sock
        except OSError as e:
            errors.append(f"{addr[0]}: {e}")
            sock.close()
    raise OSError(f"can't connect to {host}: " + "; ".join(errors or ["no addresses"]))


def diagnose(hosts=("afx.kwayisi.org", "stockanalysis.com")):
    """Test DNS and a TCP connect to every address of each source host."""
    out = {}
    for host in hosts:
        t = time.time()
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except OSError as e:
            out[host] = {"dns_error": str(e), "dns_s": round(time.time() - t, 2)}
            continue
        res = {"dns_s": round(time.time() - t, 2), "addresses": {}}
        for family, socktype, proto, _, addr in infos:
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(4)
            t = time.time()
            try:
                sock.connect(addr)
                res["addresses"][addr[0]] = f"ok {time.time() - t:.2f}s"
            except OSError as e:
                res["addresses"][addr[0]] = f"{e!r} after {time.time() - t:.1f}s"
            finally:
                sock.close()
        out[host] = res
    return out


class _HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self._create_connection = _connect  # http.client sets this per instance in __init__


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPSConnection, req, context=self._context)


_opener = urllib.request.build_opener(_HTTPSHandler)


def _get(url, ttl, data=None):
    key = (url, data)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    headers = dict(UA, **({"Content-Type": "application/json"} if data else {}))
    req = urllib.request.Request(url, data=data, headers=headers)
    t = time.time()
    try:
        with _opener.open(req, timeout=READ_TIMEOUT) as r:
            body = r.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"fetch FAILED {url} after {time.time() - t:.1f}s: {e!r}", file=sys.stderr, flush=True)
        raise
    print(f"fetch {url} {time.time() - t:.1f}s", file=sys.stderr, flush=True)
    _cache[key] = (time.time(), body)
    return body


def _afx(path):
    """GET an afx.kwayisi.org page, skipping the site for a while after network failures."""
    global _afx_down_until
    if time.time() < _afx_down_until:
        raise ConnectionError("afx.kwayisi.org is unreachable from this server")
    try:
        return _get(AFX + path, PRICE_TTL)
    except urllib.error.HTTPError:
        raise
    except (urllib.error.URLError, OSError):
        _afx_down_until = time.time() + AFX_RETRY
        raise ConnectionError("afx.kwayisi.org is unreachable from this server") from None


def _tradingview():
    """Every NSE listing from TradingView's scanner: {symbol: {column: value}}."""
    body = json.dumps({"filter": [{"left": "exchange", "operation": "equal", "right": "NSEKE"}],
                       "columns": TV_COLS, "range": [0, 500]}).encode()
    rows = json.loads(_get(TV, PRICE_TTL, data=body))["data"]
    return {r["d"][0]: dict(zip(TV_COLS, r["d"])) for r in rows if SYMBOL_RE.match(r["d"][0] or "")}


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
    try:
        tv = _tradingview()
    except Exception as e:
        print(f"TradingView list failed, trying afx: {e!r}", file=sys.stderr, flush=True)
        return _afx_list()
    if not tv:
        return _afx_list()
    rows = [{"symbol": sym, "name": r["description"], "volume": r["volume"], "price": r["close"],
             # TradingView gives % change; the page expects the absolute change
             "change": round(r["close"] - r["close"] / (1 + r["change"] / 100), 2)
             if r["close"] and r["change"] is not None else None}
            for sym, r in tv.items()]
    return sorted(rows, key=lambda r: r["symbol"])


def _afx_list():
    page = _afx("/nse/")
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


# ---------- price (TradingView, afx.kwayisi.org as fallback) ----------

def _price(sym):
    try:
        r = _tradingview().get(sym)
    except Exception as e:
        print(f"TradingView price failed for {sym}, trying afx: {e!r}", file=sys.stderr, flush=True)
        r = None
    if r and r["close"]:
        return {
            "name": r["description"], "price": r["close"],
            "52w_high": r["price_52_week_high"], "52w_low": r["price_52_week_low"],
            "market_cap": r["market_cap_basic"], "eps": r["earnings_per_share_basic_ttm"],
            "pe": r["price_earnings_ttm"], "dps": None,  # dividends come from stockanalysis.com
            "sector": r["sector"], "industry": r["industry"],
        }
    return _afx_price(sym)


def _afx_price(sym):
    try:
        page = _afx(f"/nse/{sym.lower()}.html")
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
    js = _afx(f"/chart/nse/{sym.lower()}")
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
