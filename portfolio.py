"""Portfolios: holdings (lots), dividends received, valuation and performance."""
from collections import defaultdict
from datetime import date, timedelta

import db
import history


class NotFound(Exception):
    pass


def _own(uid, pid):
    p = db.one("SELECT id, name, created_at FROM portfolios WHERE id = ? AND user_id = ?", (pid, uid))
    if not p:
        raise NotFound("portfolio not found")
    return p


def list_portfolios(uid):
    return db.all("SELECT id, name, created_at FROM portfolios WHERE user_id = ? ORDER BY id", (uid,))


def create(uid, name, now):
    name = (name or "").strip()[:60] or "Portfolio"
    return db.one("INSERT INTO portfolios (user_id, name, created_at) VALUES (?, ?, ?) RETURNING id",
                  (uid, name, now))["id"]


def rename(uid, pid, name):
    _own(uid, pid)
    db.run("UPDATE portfolios SET name = ? WHERE id = ?", ((name or "").strip()[:60] or "Portfolio", pid))


def delete(uid, pid):
    _own(uid, pid)
    db.run("DELETE FROM portfolios WHERE id = ?", (pid,))


def add_holding(uid, pid, symbol, quantity, price, bought_on, fees=0.0, note=None):
    _own(uid, pid)
    if quantity <= 0 or price <= 0:
        raise ValueError("quantity and price must be positive")
    date.fromisoformat(bought_on)
    return db.one("INSERT INTO holdings (portfolio_id, symbol, quantity, price, fees, bought_on, note) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
                  (pid, symbol.upper(), quantity, price, fees or 0.0, bought_on, (note or "")[:200] or None))["id"]


def remove_holding(uid, pid, hid):
    _own(uid, pid)
    if not db.run("DELETE FROM holdings WHERE id = ? AND portfolio_id = ?", (hid, pid)):
        raise NotFound("holding not found")


def add_dividend(uid, pid, symbol, amount, paid_on):
    _own(uid, pid)
    if amount <= 0:
        raise ValueError("amount must be positive")
    date.fromisoformat(paid_on)
    return db.one("INSERT INTO dividends_received (portfolio_id, symbol, amount, paid_on) VALUES (?, ?, ?, ?) "
                  "RETURNING id", (pid, symbol.upper(), amount, paid_on))["id"]


def remove_dividend(uid, pid, did):
    _own(uid, pid)
    if not db.run("DELETE FROM dividends_received WHERE id = ? AND portfolio_id = ?", (did, pid)):
        raise NotFound("dividend not found")


def valuation(uid, pid, prices):
    """Positions, P/L and returns at current prices. `prices`: {symbol: {price, change_pct, name}}."""
    p = _own(uid, pid)
    lots = db.all("SELECT id, symbol, quantity, price, fees, bought_on, note FROM holdings "
                  "WHERE portfolio_id = ? ORDER BY bought_on, id", (pid,))
    divs = db.all("SELECT id, symbol, amount, paid_on FROM dividends_received WHERE portfolio_id = ? "
                  "ORDER BY paid_on DESC", (pid,))
    div_by = defaultdict(float)
    for d in divs:
        div_by[d["symbol"]] += d["amount"]
    pos = {}
    for lot in lots:
        q = pos.setdefault(lot["symbol"], {"symbol": lot["symbol"], "quantity": 0.0, "cost": 0.0, "lots": []})
        q["quantity"] += lot["quantity"]
        q["cost"] += lot["quantity"] * lot["price"] + (lot["fees"] or 0)
        q["lots"].append(lot)
    total_cost = total_value = day_change = 0.0
    missing = []
    for s, q in pos.items():
        info = prices.get(s) or {}
        px = info.get("price")
        q["name"] = info.get("name", s)
        q["avg_cost"] = q["cost"] / q["quantity"] if q["quantity"] else None
        q["price"] = px
        q["dividends"] = div_by.get(s, 0.0)
        if px is None:
            missing.append(s)
            q.update(value=None, gain=None, gain_pct=None, day_change=None, total_return_pct=None)
            continue
        q["value"] = px * q["quantity"]
        q["gain"] = q["value"] - q["cost"]
        q["gain_pct"] = q["gain"] / q["cost"] if q["cost"] else None
        q["total_return_pct"] = (q["gain"] + q["dividends"]) / q["cost"] if q["cost"] else None
        chg = info.get("change_pct")
        q["day_change"] = q["value"] - q["value"] / (1 + chg) if chg is not None else None
        total_cost += q["cost"]
        total_value += q["value"]
        day_change += q["day_change"] or 0
    for q in pos.values():
        q["weight"] = q["value"] / total_value if q.get("value") and total_value else None
    total_divs = sum(div_by.values())
    gain = total_value - total_cost
    return {
        "portfolio": p,
        "positions": sorted(pos.values(), key=lambda q: -(q.get("value") or 0)),
        "dividends": divs,
        "summary": {
            "cost": total_cost, "value": total_value, "gain": gain,
            "gain_pct": gain / total_cost if total_cost else None,
            "dividends": total_divs,
            "total_return": gain + total_divs,
            "total_return_pct": (gain + total_divs) / total_cost if total_cost else None,
            "day_change": day_change,
            "day_change_pct": day_change / (total_value - day_change) if total_value - day_change else None,
            "positions": len(pos),
            "unpriced": missing,
        },
    }


def performance_series(uid, pid, days=365):
    """Daily market value vs. money invested, from stored closing prices."""
    _own(uid, pid)
    lots = db.all("SELECT symbol, quantity, price, fees, bought_on FROM holdings WHERE portfolio_id = ?", (pid,))
    if not lots:
        return {"dates": [], "value": [], "invested": []}
    first = min(l["bought_on"] for l in lots)
    since = max(first, (date.today() - timedelta(days=days)).isoformat())
    hist = history.many(sorted({l["symbol"] for l in lots}), (date.fromisoformat(since) - timedelta(days=14)).isoformat())
    all_dates = sorted({d for pts in hist.values() for d, _ in pts if d >= since})
    last_px = {s: None for s in hist}
    idx = {s: 0 for s in hist}
    dates, value, invested = [], [], []
    for d in all_dates:
        for s, pts in hist.items():
            while idx[s] < len(pts) and pts[idx[s]][0] <= d:
                last_px[s] = pts[idx[s]][1]
                idx[s] += 1
        v = inv = 0.0
        for l in lots:
            if l["bought_on"] <= d:
                inv += l["quantity"] * l["price"] + (l["fees"] or 0)
                px = last_px.get(l["symbol"])
                v += l["quantity"] * (px if px is not None else l["price"])
        dates.append(d)
        value.append(round(v, 2))
        invested.append(round(inv, 2))
    return {"dates": dates, "value": value, "invested": invested}
