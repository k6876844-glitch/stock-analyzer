# NSE Stock Analyzer

Fundamentals for companies on the Nairobi Securities Exchange (Kenya): valuation, growth,
profitability and balance-sheet health, scored against 12 rules of thumb.

- Prices: TradingView scanner, falling back to live.mystocks.co.ke, then afx.kwayisi.org
- Fundamentals: stockanalysis.com

Not investment advice.

## Run locally

    python3 server.py          # http://localhost:8000
    python3 stock.py SCOM      # terminal report

No packages to install; it uses only the Python standard library.

## Deploy (Render, free)

Push this folder to a GitHub repo, then in Render choose **New → Blueprint** and pick the
repo. `render.yaml` sets everything up. Free instances sleep after 15 minutes without
traffic, so the first visit after a pause takes about a minute.
