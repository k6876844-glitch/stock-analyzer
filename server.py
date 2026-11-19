#!/usr/bin/env python3
"""Web server and JSON API for Kenya Stock Analyzer.

    ./serve            # then open http://localhost:8000
    ./serve 9000       # custom port

Market data (public)
  GET  /api/market                         dashboard: indices, breadth, movers, all prices
  GET  /api/market/chart?years=5           computed market composite + recorded index values
  GET  /api/stock/{SYM}                    quote, indicators summary, fundamentals
  GET  /api/stock/{SYM}/chart?range=1Y     price series with SMA/EMA/RSI/MACD/Bollinger
  GET  /api/stock/{SYM}/news               headlines about the company
  GET  /api/screener                       one row per stock for filtering
  GET  /api/compare?symbols=A,B&range=1Y   side-by-side table + normalised price chart
  GET  /api/news?topic=market|economy|cbk|earnings|dividends
  GET  /api/calendar                       dividend declarations, book closures, payments
Analyst (rule-based)
  GET  /api/analyst/stock/{SYM} | /api/analyst/market | /api/analyst/compare?symbols=A,B
  GET  /api/analyst/explain?term=rsi       POST /api/analyst/ask {"question": "..."}
Accounts (cookie session; POST/PATCH/DELETE need header X-Requested-With: ksa)
  POST /api/auth/register|login|logout     GET /api/auth/me     POST /api/auth/password
  PATCH /api/auth/settings {"email_alerts": true}
  GET/POST /api/portfolios                 GET/PATCH/DELETE /api/portfolios/{id}
  GET /api/portfolios/{id}/performance     POST /api/portfolios/{id}/holdings  DELETE .../holdings/{hid}
  POST /api/portfolios/{id}/dividends      DELETE .../dividends/{did}
  GET/POST /api/watchlist                  DELETE /api/watchlist/{SYM}
  GET/POST /api/alerts                     DELETE /api/alerts/{id}   POST /api/alerts/{id}/rearm
  GET /api/notifications                   POST /api/notifications/read {"id": optional}
Ops
  GET  /api/health                         deployed commit, database, data-source reachability
  GET  /api/cron?key=CRON_SECRET           run the background job once (for an external pinger)
Legacy (CLI / old page): GET /api/analyze?symbols=SCOM,EQTY   GET /api/nse/list
"""
import json
import math
import os
import re
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import alerts
import analyst
import auth
import db
import history
import jobs
import mailer
import market
import news
import nse
import portfolio
import service
import tvdata
from stock import CHECKS, score

STATIC = Path(__file__).parent / "web"
SYM = r"([A-Za-z0-9]{1,10})"
RANGES = {"1M": 31, "3M": 92, "6M": 183, "YTD": None, "1Y": 366, "3Y": 1096, "5Y": 1827, "MAX": 99999}


class HTTPError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg)
        self.code = code


def clean(o):
    """JSON-safe: NaN/inf -> None, tuples -> lists."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def analyze_legacy(symbol):
    m = nse.fetch(symbol)
    m["scorecard"] = [{"category": cat, "label": label, "key": chk[2], "passed": r}
                      for (cat, label, r), chk in zip(score(m), CHECKS)]
    return m


def _range_start(rng, last):
    rng = (rng or "1Y").upper()
    if rng == "YTD":
        return f"{last[:4]}-01-01"
    days = RANGES.get(rng, 366)
    return (date.fromisoformat(last) - timedelta(days=days)).isoformat()


def _slice_series(series, start):
    dates = series.get("dates") or []
    i = next((k for k, d in enumerate(dates) if d >= start), len(dates))
    return {k: (v[i:] if isinstance(v, list) else v) for k, v in series.items()}


# ---------- route table ----------

ROUTES = []


def route(method, pattern, auth_required=False):
    rx = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, rx, fn, auth_required))
        return fn
    return deco


@route("GET", "/api/market")
def r_market(h, q, user):
    snap = service.snapshot()
    return snap


@route("GET", "/api/market/chart")
def r_market_chart(h, q, user):
    years = min(int(q.get("years", ["5"])[0]), 10)
    comp = service.cached(("composite", years), 3600, lambda: service.composite_index(years))
    since = (date.today() - timedelta(days=365 * years)).isoformat()
    idx = {}
    for code in ("NASI", "N20I", "N25I"):
        pts = history.index_series(code, since)
        idx[code] = {"dates": [d for d, _ in pts], "value": [v for _, v in pts]}
    return {"composite": comp, "indices": idx,
            "composite_note": "Computed by this app: market-cap-weighted average of stored closing prices, "
                              "rebased to 100. Not an official NSE index.",
            "indices_note": "Official index values recorded by this app each trading day since it started."}


@route("GET", "/api/stock/" + SYM)
def r_stock(h, q, user, sym):
    d = service.stock(sym)
    t = dict(d["technicals"])
    t.pop("series", None)
    watching = sym.upper() in alerts.watchlist(user["id"]) if user else False
    return {"quote": d["quote"], "technicals": t, "fundamentals": d["fundamentals"], "watching": watching}


@route("GET", "/api/stock/" + SYM + "/chart")
def r_stock_chart(h, q, user, sym):
    d = service.stock(sym)
    t = d["technicals"]
    s = t.get("series") or {"dates": []}
    if not s.get("dates"):
        return {"available": False, "reason": t.get("reason", "no stored price history yet"), "series": s}
    start = _range_start(q.get("range", ["1Y"])[0], s["dates"][-1])
    return {"available": t.get("available", False), "source": t.get("source"), "as_of": t.get("as_of"),
            "points_total": len(s["dates"]), "series": _slice_series(s, start)}


@route("GET", "/api/stock/" + SYM + "/news")
def r_stock_news(h, q, user, sym):
    qd = market.quote(sym)
    items = news.company(qd["name"], sym.upper())
    cal = [e for e in news.corporate_calendar() if e["symbol"] == sym.upper()]
    if not cal:
        try:
            cal = [dict(e, symbol=sym.upper()) for e in market.corporate_actions(sym)]
        except Exception:
            cal = news.dividend_events([sym.upper()])
    return {"news": items, "corporate_actions": cal}


@route("GET", "/api/screener")
def r_screener(h, q, user):
    return service.screener_rows()


@route("GET", "/api/compare")
def r_compare(h, q, user):
    syms = [s.strip().upper() for s in q.get("symbols", [""])[0].split(",") if s.strip()][:6]
    res = analyst.compare(syms)
    rng = q.get("range", ["1Y"])[0]
    start = _range_start(rng, date.today().isoformat())
    hist = history.many(res["symbols"], start)
    norm = {}
    for s, pts in hist.items():
        if pts:
            base = pts[0][1]
            norm[s] = {"dates": [d for d, _ in pts], "value": [round(c / base * 100, 2) for _, c in pts]}
    res["chart"] = norm
    res["chart_note"] = "Price rebased to 100 at the start of the period."
    return res


@route("GET", "/api/news")
def r_news(h, q, user):
    t = q.get("topic", ["market"])[0]
    if t == "cbk":
        items = news.cbk() + news.topic("cbk", 10)
    elif t in news.TOPICS:
        items = news.topic(t, 25)
    else:
        raise HTTPError(400, "unknown topic")
    seen, out = set(), []
    for i in sorted(items, key=lambda x: x["published"] or "", reverse=True):
        if i["title"] not in seen:
            seen.add(i["title"])
            out.append(i)
    return {"topic": t, "items": out}


@route("GET", "/api/calendar")
def r_calendar(h, q, user):
    ev = news.corporate_calendar()
    return {"events": ev, "source": ev[0]["source"] if ev else None}


@route("GET", "/api/analyst/stock/" + SYM)
def r_an_stock(h, q, user, sym):
    return analyst.analyze(sym.upper())


@route("GET", "/api/analyst/market")
def r_an_market(h, q, user):
    return service.cached("market_report", 300, analyst.market_report)


@route("GET", "/api/analyst/compare")
def r_an_compare(h, q, user):
    return analyst.compare([s for s in q.get("symbols", [""])[0].split(",") if s.strip()])


@route("GET", "/api/analyst/explain")
def r_an_explain(h, q, user):
    return analyst.explain(q.get("term", [""])[0])


@route("POST", "/api/analyst/ask")
def r_an_ask(h, q, user, body):
    return analyst.ask(body.get("question", ""))


# ---------- auth ----------

@route("POST", "/api/auth/register")
def r_register(h, q, user, body):
    token, uid = auth.register(body.get("email"), body.get("name"), body.get("password"), h.client_ip())
    h.set_session(token)
    return {"user": auth.user_for(token)}


@route("POST", "/api/auth/login")
def r_login(h, q, user, body):
    token, uid = auth.login(body.get("email"), body.get("password"), h.client_ip())
    h.set_session(token)
    return {"user": auth.user_for(token)}


@route("POST", "/api/auth/logout")
def r_logout(h, q, user, body):
    auth.logout(h.session_token())
    h.set_session("", clear=True)
    return {"ok": True}


@route("GET", "/api/auth/me")
def r_me(h, q, user):
    return {"user": user, "email_available": mailer.enabled()}


@route("POST", "/api/auth/password", True)
def r_password(h, q, user, body):
    auth.change_password(user["id"], body.get("old"), body.get("new"))
    return {"ok": True}


@route("PATCH", "/api/auth/settings", True)
def r_settings(h, q, user, body):
    if "email_alerts" in body:
        db.run("UPDATE users SET email_alerts = ? WHERE id = ?", (1 if body["email_alerts"] else 0, user["id"]))
    if body.get("name"):
        db.run("UPDATE users SET name = ? WHERE id = ?", (str(body["name"]).strip()[:80], user["id"]))
    return {"user": auth.user_for(h.session_token())}


# ---------- portfolios ----------

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _num(body, k, required=True):
    v = body.get(k)
    if v in (None, ""):
        if required:
            raise HTTPError(400, f"{k} is required")
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        raise HTTPError(400, f"{k} must be a number")


def _listed(sym):
    sym = (sym or "").strip().upper()
    if sym not in service.prices():
        raise HTTPError(400, f"'{sym}' is not a listed NSE stock")
    return sym


@route("GET", "/api/portfolios", True)
def r_portfolios(h, q, user):
    return {"portfolios": portfolio.list_portfolios(user["id"])}


@route("POST", "/api/portfolios", True)
def r_portfolio_new(h, q, user, body):
    return {"id": portfolio.create(user["id"], body.get("name"), _now())}


@route("GET", r"/api/portfolios/(\d+)", True)
def r_portfolio(h, q, user, pid):
    return portfolio.valuation(user["id"], int(pid), service.prices())


@route("PATCH", r"/api/portfolios/(\d+)", True)
def r_portfolio_rename(h, q, user, body, pid):
    portfolio.rename(user["id"], int(pid), body.get("name"))
    return {"ok": True}


@route("DELETE", r"/api/portfolios/(\d+)", True)
def r_portfolio_delete(h, q, user, body, pid):
    portfolio.delete(user["id"], int(pid))
    return {"ok": True}


@route("GET", r"/api/portfolios/(\d+)/performance", True)
def r_portfolio_perf(h, q, user, pid):
    return portfolio.performance_series(user["id"], int(pid), int(q.get("days", ["365"])[0]))


@route("POST", r"/api/portfolios/(\d+)/holdings", True)
def r_holding_add(h, q, user, body, pid):
    hid = portfolio.add_holding(user["id"], int(pid), _listed(body.get("symbol")), _num(body, "quantity"),
                                _num(body, "price"), body.get("bought_on") or date.today().isoformat(),
                                _num(body, "fees", False) or 0.0, body.get("note"))
    return {"id": hid}


@route("DELETE", r"/api/portfolios/(\d+)/holdings/(\d+)", True)
def r_holding_del(h, q, user, body, pid, hid):
    portfolio.remove_holding(user["id"], int(pid), int(hid))
    return {"ok": True}


@route("POST", r"/api/portfolios/(\d+)/dividends", True)
def r_div_add(h, q, user, body, pid):
    did = portfolio.add_dividend(user["id"], int(pid), _listed(body.get("symbol")), _num(body, "amount"),
                                 body.get("paid_on") or date.today().isoformat())
    return {"id": did}


@route("DELETE", r"/api/portfolios/(\d+)/dividends/(\d+)", True)
def r_div_del(h, q, user, body, pid, did):
    portfolio.remove_dividend(user["id"], int(pid), int(did))
    return {"ok": True}


# ---------- watchlist, alerts, notifications ----------

@route("GET", "/api/watchlist", True)
def r_watchlist(h, q, user):
    syms = alerts.watchlist(user["id"])
    px = service.prices()
    return {"symbols": syms, "stocks": [px[s] for s in syms if s in px]}


@route("POST", "/api/watchlist", True)
def r_watch(h, q, user, body):
    alerts.watch(user["id"], _listed(body.get("symbol")))
    return {"ok": True}


@route("DELETE", "/api/watchlist/" + SYM, True)
def r_unwatch(h, q, user, body, sym):
    alerts.unwatch(user["id"], sym)
    return {"ok": True}


@route("GET", "/api/alerts", True)
def r_alerts(h, q, user):
    rows = alerts.list_alerts(user["id"])
    for a in rows:
        a["description"] = alerts.describe(a)
    return {"alerts": rows, "kinds": alerts.KINDS, "email_available": mailer.enabled()}


@route("POST", "/api/alerts", True)
def r_alert_new(h, q, user, body):
    kind = body.get("kind")
    val = _num(body, "value", required=kind != "dividend")
    return {"id": alerts.create_alert(user["id"], _listed(body.get("symbol")), kind, val, bool(body.get("email", True)))}


@route("DELETE", r"/api/alerts/(\d+)", True)
def r_alert_del(h, q, user, body, aid):
    alerts.delete_alert(user["id"], int(aid))
    return {"ok": True}


@route("POST", r"/api/alerts/(\d+)/rearm", True)
def r_alert_rearm(h, q, user, body, aid):
    alerts.rearm_alert(user["id"], int(aid))
    return {"ok": True}


@route("GET", "/api/notifications", True)
def r_notes(h, q, user):
    return alerts.notifications(user["id"])


@route("POST", "/api/notifications/read", True)
def r_notes_read(h, q, user, body):
    alerts.mark_read(user["id"], body.get("id"))
    return {"ok": True}


# ---------- ops ----------

@route("GET", "/api/health")
def r_health(h, q, user):
    try:
        db.init()
        rows = db.one("SELECT COUNT(*) AS n, COUNT(DISTINCT symbol) AS s, MAX(d) AS last FROM price_history")
        dbs = {"ok": True, "engine": db.kind(), "history_rows": rows["n"], "history_symbols": rows["s"],
               "history_last_date": rows["last"],
               "persistent": db.PG}
    except Exception as e:
        dbs = {"ok": False, "engine": db.kind(), "error": repr(e)}
    tv = tvdata.snapshot()
    return {"commit": os.environ.get("RENDER_GIT_COMMIT", "local"), "database": dbs,
            "email_configured": mailer.enabled(), "jobs": jobs.state,
            "tradingview": {"as_of": tv["fetched_at"], "source": tv["source"], "stocks": len(tv["data"] or {})},
            "prices": _price_health(),
            "network": nse.diagnose(("nsenairobi.nse.co.ke", "stockanalysis.com", "scanner.tradingview.com", "live.mystocks.co.ke",
                                     "www.nse.co.ke", "news.google.com", "www.centralbank.go.ke",
                                     "afx.kwayisi.org"))}


def _price_health():
    try:
        snap = service.snapshot()
        return {"ok": True, "source": snap.get("prices_source"), "time": snap.get("prices_time"),
                "stocks": len(snap["stocks"])}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@route("GET", "/api/cron")
def r_cron(h, q, user):
    if not jobs.CRON_SECRET or q.get("key", [""])[0] != jobs.CRON_SECRET:
        raise HTTPError(403, "bad or missing key")
    return jobs.run_once()


@route("GET", "/api/nse/list")
def r_legacy_list(h, q, user):
    return {"stocks": nse.list_stocks()}


@route("GET", "/api/analyze")
def r_legacy_analyze(h, q, user):
    raw = q.get("symbols", [""])[0]
    symbols = list(dict.fromkeys(s.strip().upper() for s in raw.split(",") if s.strip()))[:6]
    if not symbols:
        raise HTTPError(400, "pass ?symbols=SCOM,EQTY")
    results, errors = {}, {}

    def run(s):
        try:
            results[s] = analyze_legacy(s)
        except Exception as e:
            errors[s] = str(e)
    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(run, symbols))
    return {"results": [results[s] for s in symbols if s in results], "errors": errors}


# ---------- HTTP plumbing ----------

class Handler(SimpleHTTPRequestHandler):
    server_version = "KSA/2"

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC), **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))

    def client_ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def session_token(self):
        c = SimpleCookie(self.headers.get("Cookie") or "")
        return c[auth.COOKIE].value if auth.COOKIE in c else None

    def set_session(self, token, clear=False):
        secure = "; Secure" if self.headers.get("X-Forwarded-Proto") == "https" else ""
        age = 0 if clear else auth.SESSION_DAYS * 86400
        self._cookie = f"{auth.COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={age}{secure}"

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        if self.path.split("?")[0].endswith((".html", "/")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PATCH(self):
        self._dispatch("PATCH")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        self._cookie = None
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            if method != "GET":
                return self._json(405, {"error": "method not allowed"})
            return super().do_GET()
        for m, rx, fn, need_auth in ROUTES:
            match = rx.match(url.path)
            if not match or m != method:
                continue
            try:
                user = None
                tok = self.session_token()
                if tok:
                    user = auth.user_for(tok)
                if need_auth and not user:
                    raise HTTPError(401, "please log in")
                args = [self, parse_qs(url.query), user]
                if method != "GET":
                    if self.headers.get("X-Requested-With") != "ksa":
                        raise HTTPError(403, "missing X-Requested-With header")
                    n = int(self.headers.get("Content-Length") or 0)
                    if n > 65536:
                        raise HTTPError(413, "request too large")
                    raw = self.rfile.read(n) if n else b""
                    try:
                        body = json.loads(raw or b"{}")
                    except ValueError:
                        raise HTTPError(400, "body must be JSON")
                    if not isinstance(body, dict):
                        raise HTTPError(400, "body must be a JSON object")
                    args.append(body)
                return self._json(200, fn(*args, *match.groups()))
            except HTTPError as e:
                return self._json(e.code, {"error": str(e)})
            except auth.AuthError as e:
                return self._json(e.code, {"error": str(e)})
            except portfolio.NotFound as e:
                return self._json(404, {"error": str(e)})
            except ValueError as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:
                traceback.print_exc()
                return self._json(502, {"error": f"upstream data error: {e}"})
        return self._json(404, {"error": "not found"})

    def _json(self, code, obj):
        body = json.dumps(clean(obj), default=str, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "_cookie", None):
            self.send_header("Set-Cookie", self._cookie)
        self.end_headers()
        self.wfile.write(body)


def main():
    nse.LOG = True
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8000))
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    db.init()
    jobs.start()
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"Kenya Stock Analyzer running at http://localhost:{port}  (database: {db.kind()})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
