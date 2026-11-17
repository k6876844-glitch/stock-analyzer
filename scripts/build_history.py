#!/usr/bin/env python3
"""Build or extend data/history.csv.gz: daily closes for every NSE stock from afx.kwayisi.org.

afx is the only free source of NSE price history we found. It blocks cloud hosts (Render)
and rate-limits heavy use, so this script is deliberately slow and resumable:
  * waits a few seconds between stocks,
  * skips stocks already up to date in the file,
  * stops after 3 consecutive network failures (you're probably being blocked; try later).

    python scripts/build_history.py            # fill in / update
    git add data/history.csv.gz && git commit -m "Refresh price history" && git push
"""
import csv
import gzip
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import history  # noqa: E402
import market  # noqa: E402
import nse  # noqa: E402

DELAY = 3.0


def load_existing():
    rows = {}
    if history.SEED.exists():
        with gzip.open(history.SEED, "rt", newline="") as f:
            for r in csv.DictReader(f):
                rows[(r["symbol"], r["date"])] = float(r["close"])
    return rows


def main():
    existing = load_existing()
    last = {}
    for (s, d) in existing:
        last[s] = max(last.get(s, ""), d)
    target = market.last_trading_day()
    symbols = sorted({s["symbol"] for s in market.price_list()["stocks"]})
    todo = [s for s in symbols if last.get(s, "") < target]
    print(f"{len(symbols)} stocks, {len(todo)} need updating (target {target})", flush=True)
    failures, added = 0, 0
    for i, sym in enumerate(todo, 1):
        try:
            js = nse._get(f"{nse.AFX}/chart/nse/{sym.lower()}", 3600)
            failures = 0
        except Exception as e:
            code = getattr(e, "code", None)
            print(f"[{i}/{len(todo)}] {sym}: {e}", flush=True)
            if code == 404:
                continue
            failures += 1
            if failures >= 3:
                print("afx is not responding; stopping. Try again later.", flush=True)
                break
            time.sleep(DELAY * 3)
            continue
        pts = re.findall(r'd\("(\d{4}-\d\d-\d\d)"\),([-\d.]+)\]', js)
        for d, v in pts:
            if (sym, d) not in existing:
                added += 1
            existing[(sym, d)] = float(v)
        print(f"[{i}/{len(todo)}] {sym}: {len(pts)} days", flush=True)
        time.sleep(DELAY)
    if added:
        rows = sorted((s, d, c) for (s, d), c in existing.items())
        history.SEED.parent.mkdir(exist_ok=True)
        history.SEED.write_bytes(history.to_csv_gz(rows))
        print(f"wrote {history.SEED}: {len(rows)} rows (+{added})", flush=True)
    else:
        print("no new rows", flush=True)
    return added


if __name__ == "__main__":
    main()
