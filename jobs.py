"""Background work: refresh prices, evaluate alerts, record daily history.

Runs in a thread inside the web server. Render's free plan sleeps the app after 15 minutes
without traffic, which pauses this thread; point a free external scheduler (e.g.
cron-job.org) at /api/cron?key=CRON_SECRET every 10 minutes on weekdays to keep alerts
and history running.
"""
import os
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

import alerts
import auth
import db
import history
import market
import news
import service

INTERVAL = int(os.environ.get("JOB_INTERVAL", "300"))
CRON_SECRET = os.environ.get("CRON_SECRET", "")
_run_lock = threading.Lock()
state = {"last_run": None, "last_error": None, "last_eod": None, "alerts_fired": 0, "seeded_rows": None}


def log(msg):
    print(f"jobs: {msg}", file=sys.stderr, flush=True)


def _new_dividend_events():
    """Corporate-calendar events not seen before. The very first run only records them."""
    events = news.corporate_calendar()
    first_run = not db.one("SELECT 1 AS x FROM corporate_actions LIMIT 1")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fresh = {}
    cutoff = (datetime.now(timezone.utc) - timedelta(days=21)).strftime("%Y-%m-%d")
    for e in events:
        n = db.run("INSERT INTO corporate_actions (symbol, event, d, first_seen) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT (symbol, event, d) DO NOTHING", (e["symbol"], e["event"], e["date"], now))
        if n and not first_run and "dividend" in e["event"].lower() and e["date"] >= cutoff:
            fresh.setdefault(e["symbol"], []).append(f"{e['event']} ({e['date']})")
    return fresh


def run_once(force_eod=False):
    """One pass. Safe to call from the timer or the cron endpoint."""
    if not _run_lock.acquire(blocking=False):
        return {"skipped": "already running"}
    try:
        status = market.market_status()
        snap = market.market_snapshot()
        service._cache["snapshot"] = (time.time(), snap)  # share the fresh snapshot with the API
        prices = {s["symbol"]: s for s in snap["stocks"]}
        # average volumes only for stocks with volume alerts (one quote request each)
        avg = {}
        for sym in alerts.symbols_with("volume_spike"):
            try:
                avg[sym] = market.quote(sym).get("avg_volume")
            except Exception as e:
                log(f"avg volume {sym}: {e!r}")
        try:
            divs = _new_dividend_events()
        except Exception as e:
            log(f"corporate calendar: {e!r}")
            divs = {}
        label = f"{snap['data_status']} data, {snap['as_of']}"
        fired = alerts.evaluate(prices, avg, divs, label)
        state["alerts_fired"] += fired
        # end-of-day: once the session is over (or on any later run, e.g. after the host
        # slept through the close), store that session's closes once
        session_over = status["state"] == "closed" and not (
            market.last_trading_day() == market.now_eat().strftime("%Y-%m-%d")
            and (market.now_eat().hour, market.now_eat().minute) < (market.CLOSE_AT[0], market.CLOSE_AT[1] + 15))
        day = snap["as_of"]  # the exchange's own "statistics as of" date for these prices
        if day and (force_eod or session_over) and not db.kv_get("eod:" + day):
            n = history.record_day(day, snap["stocks"])
            history.record_indices(day, snap["indices"])
            db.kv_set("eod:" + day, str(n), datetime.now(timezone.utc).isoformat())
            state["last_eod"] = day
            log(f"recorded {n} closes for {day}")
        auth.purge_expired()
        state["last_run"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        state["last_error"] = None
        return {"ok": True, "alerts_fired": fired, "market": status["state"], "eod": state["last_eod"]}
    except Exception as e:
        state["last_error"] = repr(e)
        log("run failed: " + traceback.format_exc())
        return {"ok": False, "error": repr(e)}
    finally:
        _run_lock.release()


def _loop():
    try:
        db.init()
        n = history.load_seed()
        state["seeded_rows"] = n
        if n:
            log(f"imported {n} history rows from the bundled file")
    except Exception:
        log("startup failed: " + traceback.format_exc())
    while True:
        st = market.market_status()["state"]
        if st in ("open", "pre-open") or market.now_eat().hour in (15, 16):
            run_once()
        elif not state["last_run"]:
            run_once()  # once at startup even when closed, to arm alerts / record a missed close
        time.sleep(INTERVAL)


def start():
    threading.Thread(target=_loop, name="jobs", daemon=True).start()
