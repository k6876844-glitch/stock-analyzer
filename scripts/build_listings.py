#!/usr/bin/env python3
"""Build data/listings.json: name and sector for every NSE security.

The exchange's ticker API gives prices but not names or sectors, so the app reads them
from this file. Sources: live.mystocks.co.ke price list (names + sectors), then
stockanalysis.com for any names it lacks. Run when listings change, then commit:

    python scripts/build_listings.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import market  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "listings.json"
# securities the NSE feed carries that myStocks doesn't list
MANUAL = {"FAHR": {"name": "ILAM Fahari I-REIT", "sector": "Real Estate Investment Trusts"}}


def main():
    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    listings = dict(old)
    try:
        for s in market.price_list_mystocks()["stocks"]:
            listings[s["symbol"]] = {"name": s["name"], "sector": s["sector"]}
    except Exception as e:
        print(f"myStocks unavailable ({e!r}); keeping existing names/sectors", flush=True)
    try:
        for sym, name in market.sa_names().items():
            listings.setdefault(sym, {"name": name, "sector": None})
    except Exception as e:
        print(f"stockanalysis.com names unavailable: {e!r}", flush=True)
    for sym, v in MANUAL.items():
        listings.setdefault(sym, v)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(dict(sorted(listings.items())), indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {OUT}: {len(listings)} securities", flush=True)


if __name__ == "__main__":
    main()
