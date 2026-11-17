"""Watchlists, price alerts and in-app notifications.

Alert kinds
  price_above   last price >= value (KES)
  price_below   last price <= value (KES)
  pct_up        today's change >= value (%)
  pct_down      today's change <= -value (%)
  volume_spike  today's volume >= value x the stock's average daily volume
  dividend      a new dividend event (declaration, book closure, payment) is listed

Alerts fire once, then switch off; the user can re-arm them. Each firing creates an in-app
notification and, if the user allows it and SMTP is configured, an email.
"""
import html as _html
import sys
from datetime import datetime, timezone

import db
import mailer

KINDS = {
    "price_above": "Price rises to or above",
    "price_below": "Price falls to or below",
    "pct_up": "Daily gain of at least",
    "pct_down": "Daily loss of at least",
    "volume_spike": "Volume at least (x average)",
    "dividend": "Dividend announcement",
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------- watchlist ----------

def watchlist(uid):
    return [r["symbol"] for r in db.all("SELECT symbol FROM watchlist WHERE user_id = ? ORDER BY added_at", (uid,))]


def watch(uid, symbol):
    db.run("INSERT INTO watchlist (user_id, symbol, added_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
           (uid, symbol.upper(), _now()))


def unwatch(uid, symbol):
    db.run("DELETE FROM watchlist WHERE user_id = ? AND symbol = ?", (uid, symbol.upper()))


# ---------- alerts ----------

def list_alerts(uid):
    return db.all("SELECT id, symbol, kind, value, active, email, created_at, triggered_at, triggered_value "
                  "FROM alerts WHERE user_id = ? ORDER BY active DESC, created_at DESC", (uid,))


def create_alert(uid, symbol, kind, value=None, email=True):
    if kind not in KINDS:
        raise ValueError("unknown alert type")
    if kind != "dividend":
        if value is None or value <= 0:
            raise ValueError("enter a positive value for this alert")
    if db.one("SELECT COUNT(*) AS n FROM alerts WHERE user_id = ? AND active = 1", (uid,))["n"] >= 100:
        raise ValueError("you can have at most 100 active alerts")
    return db.one("INSERT INTO alerts (user_id, symbol, kind, value, email, created_at) VALUES (?, ?, ?, ?, ?, ?) "
                  "RETURNING id", (uid, symbol.upper(), kind, value, 1 if email else 0, _now()))["id"]


def delete_alert(uid, aid):
    return db.run("DELETE FROM alerts WHERE id = ? AND user_id = ?", (aid, uid))


def rearm_alert(uid, aid):
    return db.run("UPDATE alerts SET active = 1, triggered_at = NULL, triggered_value = NULL "
                  "WHERE id = ? AND user_id = ?", (aid, uid))


# ---------- notifications ----------

def notify(uid, title, body, link=None):
    db.run("INSERT INTO notifications (user_id, title, body, link, created_at) VALUES (?, ?, ?, ?, ?)",
           (uid, title[:200], body[:2000], link, _now()))


def notifications(uid, limit=50):
    rows = db.all("SELECT id, title, body, link, created_at, read FROM notifications WHERE user_id = ? "
                  "ORDER BY id DESC LIMIT ?", (uid, limit))
    unread = db.one("SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND read = 0", (uid,))["n"]
    return {"items": rows, "unread": unread}


def mark_read(uid, nid=None):
    if nid is None:
        db.run("UPDATE notifications SET read = 1 WHERE user_id = ?", (uid,))
    else:
        db.run("UPDATE notifications SET read = 1 WHERE user_id = ? AND id = ?", (uid, nid))


# ---------- evaluation ----------

def describe(a):
    v, s, k = a["value"], a["symbol"], a["kind"]
    if k == "price_above":
        return f"{s} at or above KES {v:,.2f}"
    if k == "price_below":
        return f"{s} at or below KES {v:,.2f}"
    if k == "pct_up":
        return f"{s} up {v:g}% or more today"
    if k == "pct_down":
        return f"{s} down {v:g}% or more today"
    if k == "volume_spike":
        return f"{s} volume at least {v:g}x its average"
    return f"New dividend news for {s}"


def evaluate(prices, avg_volumes=None, new_dividend_events=None, data_label=""):
    """Check every active alert against current data; fire the ones that hit.

    prices: {symbol: {price, change_pct, volume}}
    avg_volumes: {symbol: average daily volume}
    new_dividend_events: {symbol: [event text, ...]} first seen in this run
    """
    avg_volumes = avg_volumes or {}
    new_dividend_events = new_dividend_events or {}
    fired = 0
    rows = db.all("SELECT a.id, a.user_id, a.symbol, a.kind, a.value, a.email, u.email AS user_email, "
                  "u.name, u.email_alerts FROM alerts a JOIN users u ON u.id = a.user_id WHERE a.active = 1")
    for a in rows:
        q = prices.get(a["symbol"]) or {}
        px, pct, vol = q.get("price"), q.get("change_pct"), q.get("volume")
        hit, val, detail = False, None, ""
        k, v = a["kind"], a["value"]
        if k == "price_above" and px is not None and px >= v:
            hit, val, detail = True, px, f"Last price KES {px:,.2f}"
        elif k == "price_below" and px is not None and px <= v:
            hit, val, detail = True, px, f"Last price KES {px:,.2f}"
        elif k == "pct_up" and pct is not None and pct * 100 >= v:
            hit, val, detail = True, pct * 100, f"Up {pct * 100:.2f}% today at KES {px:,.2f}"
        elif k == "pct_down" and pct is not None and -pct * 100 >= v:
            hit, val, detail = True, pct * 100, f"Down {abs(pct) * 100:.2f}% today at KES {px:,.2f}"
        elif k == "volume_spike" and vol and avg_volumes.get(a["symbol"]):
            ratio = vol / avg_volumes[a["symbol"]]
            if ratio >= v:
                hit, val, detail = True, ratio, f"Volume {vol:,.0f} shares, {ratio:.1f}x the average"
        elif k == "dividend" and new_dividend_events.get(a["symbol"]):
            hit, val, detail = True, None, "; ".join(new_dividend_events[a["symbol"]])
        if not hit:
            continue
        fired += 1
        db.run("UPDATE alerts SET active = 0, triggered_at = ?, triggered_value = ? WHERE id = ?",
               (_now(), val, a["id"]))
        title = f"Alert: {describe(a)}"
        body = detail + (f" ({data_label})" if data_label else "")
        link = f"#/stock/{a['symbol']}"
        notify(a["user_id"], title, body, link)
        if a["email"] and a["email_alerts"] and mailer.enabled():
            url = f"{mailer.APP_URL}/{link}"
            mailer.send_async(
                a["user_email"], title,
                f"Hi {a['name']},\n\n{title}\n{body}\n\nView: {url}\n\n"
                "This alert has now switched off; re-arm it in the app if you want it again.\n"
                "Not investment advice.",
                f"<p>Hi {_html.escape(a['name'])},</p><p><b>{_html.escape(title)}</b><br>{_html.escape(body)}</p>"
                f"<p><a href=\"{_html.escape(url)}\">Open {_html.escape(a['symbol'])} in Kenya Stock Analyzer</a></p>"
                "<p style=\"color:#666;font-size:12px\">This alert has now switched off; re-arm it in the app "
                "if you want it again. Not investment advice.</p>")
    if fired:
        print(f"alerts: fired {fired}", file=sys.stderr, flush=True)
    return fired


def symbols_with(kind):
    return sorted({r["symbol"] for r in db.all("SELECT DISTINCT symbol FROM alerts WHERE active = 1 AND kind = ?", (kind,))})
