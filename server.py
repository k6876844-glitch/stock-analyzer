#!/usr/bin/env python3
"""Local web server for the Nairobi Securities Exchange stock analyzer.

    ./serve            # then open http://localhost:8000
    ./serve 9000       # custom port

GET /api/analyze?symbols=SCOM,EQTY  ->  {"results": [...], "errors": {...}}
GET /api/nse/list                   ->  every NSE listing with today's price
GET /api/health                     ->  deployed commit + connectivity to the data sources
"""
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import nse
from stock import CHECKS, score

STATIC = Path(__file__).parent / "web"


def analyze(symbol):
    m = nse.fetch(symbol)  # nse.py caches page fetches
    m["scorecard"] = [
        {"category": cat, "label": label, "key": chk[2], "passed": r}
        for (cat, label, r), chk in zip(score(m), CHECKS)
    ]
    return m


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(STATIC), **kw)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s\n" % (fmt % args))

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == "/api/health":
            return self._json(200, {
                "commit": os.environ.get("RENDER_GIT_COMMIT", "local"),
                "network": nse.diagnose(),
            })
        if url.path == "/api/nse/list":
            try:
                return self._json(200, {"stocks": nse.list_stocks()})
            except Exception as e:
                return self._json(502, {"error": str(e)})
        if url.path != "/api/analyze":
            return super().do_GET()
        raw = parse_qs(url.query).get("symbols", [""])[0]
        symbols = list(dict.fromkeys(s.strip().upper() for s in raw.split(",") if s.strip()))[:6]
        if not symbols:
            return self._json(400, {"error": "pass ?symbols=SCOM,EQTY"})

        results, errors = {}, {}

        def run(s):
            try:
                results[s] = analyze(s)
            except Exception as e:
                errors[s] = str(e)

        # go easy on the source sites: two tickers at a time
        with ThreadPoolExecutor(max_workers=2) as ex:
            list(ex.map(run, symbols))
        self._json(200, {"results": [results[s] for s in symbols if s in results], "errors": errors})

    def _json(self, code, obj):
        body = json.dumps(obj, default=str, allow_nan=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    # Hosts like Render set PORT and need the server reachable on all interfaces.
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8000))
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"NSE stock analyzer running at http://localhost:{port}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
