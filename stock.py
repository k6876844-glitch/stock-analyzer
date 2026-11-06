#!/usr/bin/env python3
"""Fundamental stock analyzer for the Nairobi Securities Exchange (Kenya).

Usage:
    python stock.py SCOM
    python stock.py EQTY KCB COOP        # analyze several
    python stock.py SCOM --json          # raw metrics as JSON

Prices from afx.kwayisi.org, fundamentals from stockanalysis.com. Not investment advice.
"""
import argparse
import json
import math
import sys

from nse import fetch

# ---------- formatting helpers ----------

def _num(x):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def money(x):
    x = _num(x)
    if x is None:
        return "n/a"
    sign = "-" if x < 0 else ""
    x = abs(x)
    for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if x >= div:
            return f"{sign}KES {x / div:.2f}{unit}"
    return f"{sign}KES {x:.2f}"


def pct(x, already_pct=False):
    x = _num(x)
    if x is None:
        return "n/a"
    return f"{x if already_pct else x * 100:.1f}%"


def ratio(x):
    x = _num(x)
    return "n/a" if x is None else f"{x:.2f}"


# ---------- data ----------
# All data comes from nse.py (Nairobi Securities Exchange).


# ---------- scoring ----------
# Each check: (category, label, value_key, test, pass_note). Simple rule-of-thumb
# thresholds; they're starting points, not truths. Sector norms vary a lot.

def _lt(a):
    return lambda v: v is not None and 0 < v < a


def _gt(a):
    return lambda v: v is not None and v > a


CHECKS = [
    ("Valuation", "Forward P/E < 25", "pe_forward", _lt(25)),
    ("Valuation", "PEG < 1.5", "peg", _lt(1.5)),
    ("Valuation", "FCF yield > 4%", "fcf_yield", _gt(0.04)),
    ("Growth", "Revenue growth > 8% YoY", "revenue_growth_yoy", _gt(0.08)),
    ("Growth", "Revenue CAGR > 8%", "revenue_cagr", _gt(0.08)),
    ("Growth", "EPS CAGR > 10%", "eps_cagr", _gt(0.10)),
    ("Profitability", "Operating margin > 15%", "operating_margin", _gt(0.15)),
    ("Profitability", "Net margin > 10%", "net_margin", _gt(0.10)),
    ("Profitability", "ROE > 15%", "roe", _gt(0.15)),
    ("Health", "Debt/Equity < 1.0", "debt_to_equity", lambda v: v is not None and v < 1.0),
    ("Health", "Current ratio > 1.2", "current_ratio", _gt(1.2)),
    ("Health", "Positive free cash flow", "free_cash_flow", _gt(0)),
]


def score(m):
    results = []
    for cat, label, key, test in CHECKS:
        v = m.get(key)
        results.append((cat, label, None if v is None else bool(test(v))))
    return results


# ---------- report ----------

BOLD, DIM, GREEN, RED, YELLOW, RESET = "\033[1m", "\033[2m", "\033[32m", "\033[31m", "\033[33m", "\033[0m"


def color_enabled():
    return sys.stdout.isatty()


def c(text, code):
    return f"{code}{text}{RESET}" if color_enabled() else text


def section(title):
    print("\n" + c(title, BOLD))


def line(label, value):
    print(f"  {label:<24}{value}")


def report(m):
    title = f"{m['name']} ({m['symbol']})"
    print(c(title, BOLD))
    print(c(f"{m['sector'] or '?'} · {m['industry'] or '?'}", DIM))

    section("Overview")
    line("Price", money(m["price"]))
    line("Market cap", money(m["market_cap"]))
    if m["52w_low"] and m["52w_high"]:
        line("52-week range", f"{money(m['52w_low'])} – {money(m['52w_high'])}")

    section("Valuation")
    line("P/E (trailing)", ratio(m["pe_trailing"]))
    line("P/E (forward)", ratio(m["pe_forward"]))
    line("PEG", ratio(m["peg"]))
    line("Price / Sales", ratio(m["price_to_sales"]))
    line("Price / Book", ratio(m["price_to_book"]))
    line("EV / EBITDA", ratio(m["ev_to_ebitda"]))
    line("FCF yield", pct(m["fcf_yield"]))

    n_years = len([v for v in m["history"]["revenue"] if v is not None])
    section("Growth")
    line("Revenue growth (TTM)", pct(m["revenue_growth_yoy"]))
    line("EPS growth (TTM)", pct(m["earnings_growth_yoy"]))
    line(f"Revenue CAGR ({max(n_years - 1, 0)}y)", pct(m["revenue_cagr"]))
    n_eps = len([v for v in m["history"]["eps"] if v is not None])
    line(f"EPS CAGR ({max(n_eps - 1, 0)}y)", pct(m["eps_cagr"]))

    section("Profitability")
    line("Gross margin", pct(m["gross_margin"]))
    line("Operating margin", pct(m["operating_margin"]))
    line("Net margin", pct(m["net_margin"]))
    line("Return on equity", pct(m["roe"]))
    line("Return on assets", pct(m["roa"]))

    section("Financial health")
    line("Cash", money(m["total_cash"]))
    line("Debt", money(m["total_debt"]))
    line("Debt / Equity", ratio(m["debt_to_equity"]))
    line("Current ratio", ratio(m["current_ratio"]))
    line("Quick ratio", ratio(m["quick_ratio"]))
    line("Free cash flow (FY)", money(m["free_cash_flow"]))

    if m["dividend_yield_pct"]:
        section("Dividend")
        line("Yield", pct(m["dividend_yield_pct"], already_pct=True))
        line("Payout ratio", pct(m["payout_ratio"]))

    h = m["history"]
    if h["years"]:
        section("Annual history")
        print(c(f"  {'Year':<8}{'Revenue':>12}{'Net income':>13}{'EPS':>9}{'FCF':>12}", DIM))
        for i, yr in enumerate(h["years"]):
            def g(k):
                return h[k][i] if i < len(h[k]) else None
            if all(g(k) is None for k in ("revenue", "net_income", "eps", "free_cash_flow")):
                continue
            eps = g("eps")
            print(f"  {yr:<8}{money(g('revenue')):>12}{money(g('net_income')):>13}"
                  f"{('n/a' if eps is None else f'{eps:.2f}'):>9}{money(g('free_cash_flow')):>12}")

    if m["analyst_target"]:
        section("Analysts")
        upside = (m["analyst_target"] / m["price"] - 1) if m["price"] else None
        line("Mean target", f"{money(m['analyst_target'])} ({pct(upside)} vs. price)")
        line("Consensus", f"{(m['analyst_rating'] or 'n/a').replace('_', ' ')} "
                          f"({m['analyst_count'] or 0} analysts)")

    results = score(m)
    passed = sum(1 for *_, r in results if r)
    known = sum(1 for *_, r in results if r is not None)
    section(f"Scorecard: {passed}/{known} checks passed")
    for cat, label, r in results:
        mark = c("✓", GREEN) if r else c("✗", RED) if r is False else c("?", YELLOW)
        print(f"  {mark} {c(cat + ':', DIM)} {label}")
    print(c("\n  Rule-of-thumb thresholds; norms differ by sector. Not investment advice.", DIM))


def compare(ms):
    rows = [
        ("Price", "price", money), ("Market cap", "market_cap", money),
        ("P/E fwd", "pe_forward", ratio), ("PEG", "peg", ratio),
        ("P/S", "price_to_sales", ratio), ("FCF yield", "fcf_yield", pct),
        ("Rev growth", "revenue_growth_yoy", pct), ("Rev CAGR", "revenue_cagr", pct),
        ("Op margin", "operating_margin", pct), ("Net margin", "net_margin", pct),
        ("ROE", "roe", pct), ("Debt/Eq", "debt_to_equity", ratio),
        ("Current", "current_ratio", ratio),
    ]
    w = 14
    print(c(f"{'':<12}" + "".join(f"{m['symbol']:>{w}}" for m in ms), BOLD))
    for label, key, fmt in rows:
        print(f"{label:<12}" + "".join(f"{fmt(m[key]):>{w}}" for m in ms))
    score_row = []
    for m in ms:
        r = score(m)
        score_row.append(f"{sum(1 for *_, x in r if x)}/{sum(1 for *_, x in r if x is not None)}")
    print(c(f"{'Score':<12}" + "".join(f"{s:>{w}}" for s in score_row), BOLD))


def main():
    p = argparse.ArgumentParser(description="Fundamental stock analyzer for Nairobi Securities Exchange stocks.")
    p.add_argument("symbols", nargs="+", help="NSE ticker symbols, e.g. SCOM EQTY")
    p.add_argument("--json", action="store_true", help="print raw metrics as JSON")
    args = p.parse_args()

    results, failed = [], False
    for s in args.symbols:
        try:
            results.append(fetch(s))
        except Exception as e:  # network errors, bad tickers
            print(f"error: {s}: {e}", file=sys.stderr)
            failed = True

    if args.json:
        print(json.dumps(results if len(results) != 1 else results[0], indent=2, default=str))
    else:
        for i, m in enumerate(results):
            if i:
                print("\n" + "─" * 60 + "\n")
            report(m)
        if len(results) > 1:
            print("\n" + "─" * 60 + "\n")
            compare(results)
    sys.exit(1 if failed or not results else 0)


if __name__ == "__main__":
    main()
