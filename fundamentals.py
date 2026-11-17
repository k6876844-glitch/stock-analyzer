"""Company fundamentals: valuation, profitability, balance sheet, dividends, history.

Primary source is stockanalysis.com (statements, statistics, dividends); TradingView's
scanner fills gaps. Every metric carries its source; a metric no source publishes is None
and the UI shows it as unavailable.
"""
import re
from datetime import datetime

import nse
import tvdata
from nse import SA, FUNDAMENTALS_TTL, _get, _n, _rows

BALANCE_KEYS = {"Total Assets": "total_assets", "Total Liabilities": "total_liabilities",
                "Shareholders' Equity": "equity", "Total Debt": "total_debt",
                "Book Value Per Share": "book_value_per_share"}


def balance_sheet(sym):
    try:
        page = _get(f"{SA}/{sym}/financials/balance-sheet/", FUNDAMENTALS_TTL)
    except Exception:
        return {"years": []}
    header, out = [], {"years": []}
    for r in _rows(page):
        if r[0] == "Fiscal Year":
            header = r[1:]
            continue
        key = BALANCE_KEYS.get(r[0])
        if not key or key in out:
            continue
        cols = [(h, v) for h, v in zip(header, r[1:]) if h.startswith("FY")]
        if not out["years"]:
            out["years"] = [int(re.search(r"\d{4}", h).group()) for h, _ in cols]
        scale = 1.0 if key == "book_value_per_share" else 1e6
        out[key] = [_n(v, scale) for _, v in cols]
        latest = [(h, v) for h, v in zip(header, r[1:]) if h in ("TTM", "Current")]
        if latest:
            out[key + "_latest"] = _n(latest[0][1], scale)
    return out


def dividends(sym):
    """Dividend history (ex-date, amount, record and pay dates) plus summary stats."""
    try:
        page = _get(f"{SA}/{sym}/dividend/", FUNDAMENTALS_TTL)
    except Exception:
        return {"history": [], "summary": {}}

    def iso(s):
        try:
            return datetime.strptime(s.strip(), "%b %d, %Y").strftime("%Y-%m-%d")
        except ValueError:
            return None

    hist = []
    for r in _rows(page):
        if len(r) >= 4 and iso(r[0]) and "KES" in r[1]:
            hist.append({"ex_date": iso(r[0]), "amount": _n(r[1]), "record_date": iso(r[2]),
                         "pay_date": iso(r[3])})
    text = " ".join(nse._text(page))
    summary = {}
    for label, key in (("Dividend Yield", "yield"), ("Annual Dividend", "annual"),
                       ("Payout Frequency", "frequency"), ("Payout Ratio", "payout_ratio"),
                       ("Dividend Growth (1Y)", "growth_1y")):
        m = re.search(re.escape(label) + r"\s+([^\s]+(?: KES)?)", text)
        if m:
            summary[key] = m.group(1) if key == "frequency" else _n(m.group(1))
    by_year = {}
    for h in hist:
        y = h["ex_date"][:4]
        by_year[y] = by_year.get(y, 0) + (h["amount"] or 0)
    return {"history": hist, "summary": summary,
            "by_year": [{"year": int(y), "total": round(v, 4)} for y, v in sorted(by_year.items())]}


def full(sym, price=None):
    """Everything fundamental for one stock, with per-field sources."""
    sym = sym.upper()
    base = {}
    try:
        base = nse.fetch(sym)  # stockanalysis.com statistics + income statement history
        from stock import CHECKS, score
        base["scorecard"] = [{"category": cat, "label": label, "key": chk[2], "passed": r}
                             for (cat, label, r), chk in zip(score(base), CHECKS)]
    except Exception as e:
        base = {"error": str(e)}
    tv, tv_meta = tvdata.get(sym)
    tv = tv or {}
    bs = balance_sheet(sym)
    dv = dividends(sym)
    src = {}

    def pick(key, *cands):
        for label, v in cands:
            if v is not None:
                src[key] = label
                return v
        src[key] = None
        return None

    SAc, TVc = "stockanalysis.com", "TradingView"
    px = price or base.get("price") or tv.get("close")
    eps = pick("eps", (SAc, (base.get("history") or {}).get("eps", [None])[0] if base.get("history") else None),
               (TVc, tv.get("eps")))
    m = {
        "price_used": px,
        "eps": eps,
        "pe": pick("pe", (SAc, base.get("pe_trailing")), (TVc, tv.get("pe") if (tv.get("pe") or 0) > 0 else None)),
        "pe_forward": pick("pe_forward", (SAc, base.get("pe_forward"))),
        "pb": pick("pb", (SAc, base.get("price_to_book")), (TVc, tv.get("pb"))),
        "ps": pick("ps", (SAc, base.get("price_to_sales"))),
        "peg": pick("peg", (SAc, base.get("peg"))),
        "ev_ebitda": pick("ev_ebitda", (SAc, base.get("ev_to_ebitda"))),
        "dividend_yield": pick("dividend_yield",
                               (SAc, dv["summary"].get("yield")),
                               (SAc, base["dividend_yield_pct"] / 100 if base.get("dividend_yield_pct") is not None else None),
                               (TVc, tv.get("dividend_yield"))),
        "annual_dividend": pick("annual_dividend", (SAc, dv["summary"].get("annual"))),
        "payout_ratio": pick("payout_ratio", (SAc, base.get("payout_ratio")), (SAc, dv["summary"].get("payout_ratio"))),
        "revenue": pick("revenue", (SAc, (base.get("history") or {}).get("revenue", [None])[0] if base.get("history") else None),
                        (TVc, tv.get("revenue"))),
        "net_income": pick("net_income", (SAc, (base.get("history") or {}).get("net_income", [None])[0] if base.get("history") else None),
                           (TVc, tv.get("net_income"))),
        "total_assets": pick("total_assets", (SAc, bs.get("total_assets_latest")),
                             (SAc, (bs.get("total_assets") or [None])[0]), (TVc, tv.get("total_assets"))),
        "total_liabilities": pick("total_liabilities", (SAc, bs.get("total_liabilities_latest")),
                                  (SAc, (bs.get("total_liabilities") or [None])[0]), (TVc, tv.get("total_liabilities"))),
        "equity": pick("equity", (SAc, bs.get("equity_latest")), (SAc, (bs.get("equity") or [None])[0])),
        "roe": pick("roe", (SAc, base.get("roe")), (TVc, tv.get("roe"))),
        "roa": pick("roa", (SAc, base.get("roa"))),
        "debt_to_equity": pick("debt_to_equity", (SAc, base.get("debt_to_equity")), (TVc, tv.get("debt_to_equity"))),
        "current_ratio": pick("current_ratio", (SAc, base.get("current_ratio"))),
        "gross_margin": pick("gross_margin", (SAc, base.get("gross_margin")), (TVc, tv.get("gross_margin"))),
        "operating_margin": pick("operating_margin", (SAc, base.get("operating_margin"))),
        "net_margin": pick("net_margin", (SAc, base.get("net_margin")), (TVc, tv.get("net_margin"))),
        "revenue_growth": pick("revenue_growth", (SAc, base.get("revenue_growth_yoy"))),
        "earnings_growth": pick("earnings_growth", (SAc, base.get("earnings_growth_yoy"))),
        "revenue_cagr": pick("revenue_cagr", (SAc, base.get("revenue_cagr"))),
        "eps_cagr": pick("eps_cagr", (SAc, base.get("eps_cagr"))),
        "free_cash_flow": pick("free_cash_flow", (SAc, base.get("free_cash_flow"))),
        "fcf_yield": pick("fcf_yield", (SAc, base.get("fcf_yield"))),
    }
    if m["pe"] is None and px and eps and eps > 0:
        m["pe"] = px / eps
        src["pe"] = "computed: price / EPS"
    return {
        "symbol": sym,
        "metrics": m,
        "sources": src,
        "history": base.get("history") or {"years": [], "revenue": [], "net_income": [], "eps": [], "free_cash_flow": []},
        "balance_history": bs,
        "dividends": dv,
        "analysts": {"target": base.get("analyst_target"), "rating": base.get("analyst_rating"),
                     "count": base.get("analyst_count")},
        "scorecard": base.get("scorecard"),
        "tradingview_as_of": tv_meta.get("fetched_at"),
        "error": base.get("error"),
    }
