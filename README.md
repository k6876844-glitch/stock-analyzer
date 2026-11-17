# Kenya Stock Analyzer

A stock market analysis platform for the Nairobi Securities Exchange (NSE): live market
dashboard, technical and fundamental analysis, screener, comparison, portfolios, watchlists
with price alerts, news, and a rule-based research analyst.

Not investment advice. Prices are delayed or end-of-day and are never estimated: anything a
source doesn't publish is shown as unavailable.

## Features

| Area | What it does |
|---|---|
| Dashboard | NASI, NSE 20, NSE 25 (official, nse.co.ke), market cap, equity turnover, shares traded, breadth, top gainers/losers/most traded, sector moves, market status, news, dividend calendar. Refreshes every minute while the market is open. |
| Stock page | Open, previous close, high/low, volume, average volume, turnover, deals, market cap, 52-week range, profile; data freshness label (live / delayed / end-of-day). |
| Technicals | Interactive chart with SMA 20/50/200, EMA 12/26, Bollinger bands, RSI, MACD, volume; support/resistance, volatility, drawdown, trend, performance 1D to 5Y. |
| Fundamentals | EPS, P/E, P/B, dividend yield and history, revenue, net profit, total assets and liabilities, ROE, debt/equity, margins, growth; each figure shows its source. |
| Analyst | Rule-based research write-ups for a stock, comparisons, daily market report and indicator explanations. Each report gives its data date, assumptions, risks and an educational Buy/Hold/Sell signal with the scored factors behind it. |
| Compare | Up to 6 stocks: rebased price chart and side-by-side table. |
| Screener | Filter by price, market cap, dividend yield, P/E, EPS, P/B, ROE, debt/equity, RSI, returns, volume, sector and trend; presets; CSV export. |
| Portfolio | Accounts, multiple portfolios, purchase lots with fees, dividends received, P/L, returns, allocation, value-over-time chart. |
| Alerts | Watchlist; alerts on price above/below, daily % move, volume spike, dividend announcements; in-app notifications and email. |
| News | NSE, earnings, dividends, Central Bank of Kenya, economy (Google News, CBK feed) with dates and links; corporate-action calendar. |

## Data sources

| Source | Used for | Notes |
|---|---|---|
| live.mystocks.co.ke | prices, quotes, sectors, corporate actions | delayed during trading, end-of-day after |
| www.nse.co.ke | index values, turnover, shares traded | official |
| stockanalysis.com | fundamentals, dividends, market caps | |
| TradingView scanner | indicators and fundamentals fallback | rate-limits Render; cached, plus a daily snapshot file |
| afx.kwayisi.org | 10-year daily price history | blocks cloud hosts; fetched by `scripts/build_history.py` |
| Google News, CBK RSS | news | |

Bid/ask prices aren't published by any free source, so they show as unavailable.

## Run locally

    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
    ./serve                     # http://localhost:8000
    ./stock SCOM                # terminal report

Without `DATABASE_URL` the app stores accounts and history in `data/app.db` (SQLite).

## Deploy on Render

`render.yaml` defines the service. Set these environment variables in the Render dashboard:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection string from [Neon](https://neon.tech) (free). **Without it, accounts, portfolios and alerts are lost whenever Render restarts the app.** |
| `SMTP_USER`, `SMTP_PASSWORD` | Gmail address and an [app password](https://myaccount.google.com/apppasswords) for alert emails. |
| `CRON_SECRET` | Any random string. |
| `NSE_CLOSE` | Set to `16:00` when the NSE's extended trading hours start (announced for January 2027). |

Render's free plan sleeps after 15 minutes without traffic, which pauses alert checks. Create a
free job at [cron-job.org](https://cron-job.org) that calls
`https://<your-app>/api/cron?key=<CRON_SECRET>` every 10 minutes on weekdays.

`/api/health` shows the deployed commit, database status, and which data sources are reachable.

## Price history

Indicators need daily closing prices. The app records every stock's close each trading day.
To load about ten years of history, run this on a machine that can reach afx.kwayisi.org, then commit the file:

    python scripts/build_history.py
    git add data/history.csv.gz && git commit -m "Refresh price history" && git push

`python scripts/collect.py` also refreshes `data/tv_snapshot.json` (TradingView indicators). Until
there are 60 stored days for a stock, its indicators come from that snapshot.
