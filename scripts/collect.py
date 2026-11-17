#!/usr/bin/env python3
"""Daily data collector, run by GitHub Actions after the market closes (see
.github/workflows/collect.yml) or by hand:

    python scripts/collect.py

  1. saves TradingView's technical + fundamental values for every NSE stock to
     data/tv_snapshot.json (the web host is rate-limited by TradingView);
  2. extends data/history.csv.gz with new daily closes from afx.kwayisi.org, if reachable.
The workflow commits whatever changed; the web app loads both files on startup.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tvdata  # noqa: E402


def main():
    ok = False
    try:
        data = tvdata.fetch_live()
        tvdata.write_snapshot_file(data)
        print(f"TradingView snapshot: {len(data)} stocks", flush=True)
        ok = True
    except Exception as e:
        print(f"TradingView snapshot failed: {e!r}", flush=True)
    try:
        import build_history
        build_history.main()
        ok = True
    except Exception as e:
        print(f"history update failed: {e!r}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
