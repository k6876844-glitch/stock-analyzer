// Market views: dashboard, stock list, stock detail, compare, screener, news.
import { api, state, esc, isNum, NA, kes, kesFull, num, int, vol, pct, delta, deltaAbs, fmtDate, ago, dataPill, marketPill,
  $, $$, toast, errorBox, skeleton, every, sortableTable, params, compact } from "./core.js";
import { lineChart, stockChart, divergingBars, barChart, pts, palette } from "./charts.js";
import { renderAnalysis } from "./views-analyst.js";

const stockLink = s => `<a href="#/stock/${esc(s)}" class="sym">${esc(s)}</a>`;

// ---------- dashboard ----------
export async function dashboard(main) {
  main.innerHTML = `
    <div class="status-strip" id="strip"></div>
    <h1>Nairobi Securities Exchange today</h1>
    <p class="lede">Indices, market activity and the day's movers. Prices are never estimated: anything a source doesn't publish shows as n/a.</p>
    <div class="grid g-4" id="idx">${skeleton(4, 96)}</div>
    <div class="grid g-4 mt" id="stats"></div>
    <div class="grid g-21 mt">
      <div class="card"><h2><span class="dot"></span>Market performance<span class="right" id="mchartNote"></span></h2>
        <div class="row mb"><div class="seg" id="mRange">${["1Y", "3Y", "5Y"].map((r, i) => `<button aria-pressed="${i === 0}" data-y="${r[0]}">${r}</button>`).join("")}</div></div>
        <div class="chart-box mid" id="mchart"></div><p class="tiny muted" id="mchartFoot"></p></div>
      <div class="card"><h2><span class="dot"></span>Sector moves today</h2><div id="sectors"></div></div>
    </div>
    <div class="grid g-3 mt">
      <div class="card"><h2><span class="dot"></span>Top gainers</h2><div id="gainers" class="mini-list"></div></div>
      <div class="card"><h2><span class="dot"></span>Top losers</h2><div id="losers" class="mini-list"></div></div>
      <div class="card"><h2><span class="dot"></span>Most traded</h2><div id="traded" class="mini-list"></div></div>
    </div>
    <div class="grid g-2 mt">
      <div class="card"><h2><span class="dot"></span>Market news<span class="right"><a href="#/news">All news</a></span></h2><div id="dnews">${skeleton(1, 120)}</div></div>
      <div class="card"><h2><span class="dot"></span>Dividends &amp; corporate actions<span class="right"><a href="#/news?tab=calendar">Calendar</a></span></h2><div id="dcal">${skeleton(1, 120)}</div></div>
    </div>`;
  const draw = d => {
    state.market = d; state.stocks = d.stocks;
    $("#strip").innerHTML = `${marketPill(d.status)} ${dataPill(d.data_status, d.as_of)} <span class="tiny muted">Prices: ${esc(d.prices_source || "")}</span>
      <span class="tiny">Updated ${esc(fmtDate(d.fetched_at, true))} · ${esc(d.status.note)}${d.status.next_open ? " · next open " + esc(fmtDate(d.status.next_open, true)) : ""}</span>
      <span class="tiny muted">Bid/ask quotes aren't published by free sources.</span>`;
    const idx = d.indices;
    $("#idx").innerHTML = ["NASI", "N20I", "N25I", "NBSI"].filter(k => idx[k]).map(k => `
      <div class="stat"><div class="k">${esc(idx[k].name)}</div><div class="v num">${num(idx[k].value)}</div>
      <div class="d num">${deltaAbs(idx[k].change)} &nbsp;${delta(idx[k].change_pct)}</div>
      <div class="s">${esc(idx[k].source || "nse.co.ke")}</div></div>`).join("");
    $("#stats").innerHTML = `
      <div class="stat"><div class="k">Total market cap</div><div class="v num">${kes(d.total_market_cap)}</div><div class="s">${esc(d.market_cap_coverage)} (stockanalysis.com)</div></div>
      <div class="stat"><div class="k">Equity turnover</div><div class="v num">${kes(d.equity_turnover)}</div>
        <div class="d">${isNum(d.equity_turnover_prev) && d.equity_turnover_prev ? delta(d.equity_turnover / d.equity_turnover_prev - 1, 1) + ' <span class="tiny muted">vs previous session</span>' : ""}</div><div class="s">nse.co.ke</div></div>
      <div class="stat"><div class="k">Shares traded</div><div class="v num">${vol(d.shares_traded)}</div><div class="s">nse.co.ke</div></div>
      <div class="stat"><div class="k">Breadth</div><div class="v num"><span class="up">▲${d.advancers}</span> <span class="down">▼${d.decliners}</span></div>
        <div class="s">${d.unchanged} unchanged · ${d.not_traded} not traded</div></div>`;
    const list = (el, rows, f) => $(el).innerHTML = rows.length ? rows.map(s => `<div class="item" data-s="${s.symbol}">
        <span class="sym">${s.symbol}</span><span class="nm">${esc(s.name)}</span><span class="num" style="text-align:right">${f(s)}</span></div>`).join("")
      : '<div class="empty small">None today</div>';
    list("#gainers", d.gainers, s => `${num(s.price)}<div class="tiny">${delta(s.change_pct)}</div>`);
    list("#losers", d.losers, s => `${num(s.price)}<div class="tiny">${delta(s.change_pct)}</div>`);
    list("#traded", d.most_traded, s => `${vol(s.volume)}<div class="tiny">${delta(s.change_pct)}</div>`);
    $$(".mini-list .item", main).forEach(it => it.onclick = () => location.hash = "#/stock/" + it.dataset.s);
    const by = {};
    d.stocks.filter(s => s.volume).forEach(s => (by[s.sector || "Other"] ||= []).push(s.change_pct));
    divergingBars($("#sectors"), Object.entries(by).map(([k, v]) => ({ label: k, value: v.reduce((a, b) => a + b, 0) / v.length }))
      .sort((a, b) => b.value - a.value), v => (v >= 0 ? "+" : "") + (v * 100).toFixed(2) + "%");
  };
  draw(await api("/api/market"));
  const loadChart = async years => {
    const c = await api("/api/market/chart?years=" + years);
    const lines = [];
    if (c.composite.dates.length > 1) lines.push({ name: "Composite", data: pts(c.composite.dates, c.composite.value) });
    if (lines.length) {
      lineChart($("#mchart"), lines, { area: true });
      $("#mchartNote").textContent = "Computed composite, rebased to 100";
      $("#mchartFoot").textContent = c.composite_note + (c.indices.NASI.dates.length ? "" : " Official NASI values are recorded daily from now on.");
    } else if (c.indices.NASI.dates.length > 1) {
      lineChart($("#mchart"), [{ name: "NASI", data: pts(c.indices.NASI.dates, c.indices.NASI.value) }], { area: true });
      $("#mchartFoot").textContent = c.indices_note;
    } else {
      $("#mchart").innerHTML = `<div class="empty">Not enough price history stored yet to chart the market.<br><span class="tiny">The app records every closing price each trading day; this chart fills in as history builds up.</span></div>`;
    }
  };
  loadChart(1).catch(e => $("#mchart").innerHTML = errorBox(e));
  $$("#mRange button").forEach(b => b.onclick = () => {
    $$("#mRange button").forEach(x => x.setAttribute("aria-pressed", x === b));
    loadChart(+b.dataset.y);
  });
  api("/api/news?topic=market").then(d => $("#dnews").innerHTML = newsList(d.items.slice(0, 6))).catch(e => $("#dnews").innerHTML = errorBox(e));
  api("/api/calendar").then(d => $("#dcal").innerHTML = eventList(d.events.slice(0, 8))).catch(e => $("#dcal").innerHTML = errorBox(e));
  every(60000, async () => { if (state.market?.status?.state !== "closed") draw(await api("/api/market")); });
}

export const newsList = items => items.length ? items.map(n => `<div class="news-item">
  <a href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a>
  <div class="meta">${esc(n.source || "")} · ${esc(fmtDate(n.published, true))}</div></div>`).join("") : '<div class="empty small">No recent articles.</div>';

export const eventList = ev => ev.length ? ev.map(e => `<div class="event">
  <span class="muted num">${esc(fmtDate(e.date))}</span>${stockLink(e.symbol)}<span>${esc(e.event)} <span class="tiny muted">${esc(e.company || "")}</span></span></div>`).join("")
  : '<div class="empty small">No events listed.</div>';

// ---------- all stocks ----------
export async function stocksPage(main) {
  const d = state.market || await api("/api/market");
  const sectors = [...new Set(d.stocks.map(s => s.sector).filter(Boolean))].sort();
  main.innerHTML = `<div class="status-strip">${marketPill(d.status)} ${dataPill(d.data_status, d.as_of)}</div>
    <h1>All NSE-listed securities</h1><p class="lede">${d.stocks.length} listings. Click a column to sort, a row to open the stock.</p>
    <div class="card"><div class="row mb"><input class="i" id="flt" placeholder="Filter by name or ticker" style="max-width:280px">
      <select class="i" id="sec" style="max-width:240px"><option value="">All sectors</option>${sectors.map(s => `<option>${esc(s)}</option>`).join("")}</select>
      <span class="spacer"></span><span class="tiny muted" id="cnt"></span></div><div id="tbl" class="scroll-y"></div></div>`;
  const cols = [
    { key: "symbol", label: "Ticker", fmt: r => `<span class="sym">${r.symbol}</span>` },
    { key: "name", label: "Company", cls: "l", fmt: r => `<span class="nm">${esc(r.name)}</span>` },
    { key: "sector", label: "Sector", cls: "l", fmt: r => `<span class="tiny muted">${esc(r.sector || "")}</span>` },
    { key: "price", label: "Price", fmt: r => num(r.price) },
    { key: "change_pct", label: "Change", fmt: r => r.volume ? delta(r.change_pct) : '<span class="tiny muted">no trades</span>' },
    { key: "volume", label: "Volume", fmt: r => vol(r.volume) },
    { key: "market_cap", label: "Market cap", fmt: r => isNum(r.market_cap) ? compact(r.market_cap) : NA },
  ];
  const t = sortableTable($("#tbl"), d.stocks, cols, { onRow: r => location.hash = "#/stock/" + r.symbol, initial: { key: "market_cap", dir: -1 } });
  const apply = () => {
    const q = $("#flt").value.trim().toLowerCase(), sec = $("#sec").value;
    const rows = d.stocks.filter(s => (!q || s.symbol.toLowerCase().includes(q) || s.name.toLowerCase().includes(q)) && (!sec || s.sector === sec));
    $("#cnt").textContent = rows.length + " shown";
    t.update(rows);
  };
  $("#flt").oninput = apply; $("#sec").onchange = apply; apply();
}

// ---------- stock detail ----------
export async function stockPage(main, sym) {
  sym = sym.toUpperCase();
  const d = await api("/api/stock/" + encodeURIComponent(sym));
  const q = d.quote, t = d.technicals, f = d.fundamentals;
  const lo = q.week52_low, hi = q.week52_high;
  const pos = isNum(lo) && isNum(hi) && hi > lo && isNum(q.price) ? Math.min(1, Math.max(0, (q.price - lo) / (hi - lo))) : null;
  main.innerHTML = `
    <div class="status-strip">${dataPill(q.data_status, q.as_of)} <span class="tiny">Prices: ${esc(q.source)}${q.extras_source ? " · 52-week range &amp; averages: " + esc(q.extras_source) : ""}</span></div>
    <div class="card hero fade-in">
      <div><span class="ticker-badge">NSE: ${esc(sym)}</span>
        <h1>${esc(q.name)}</h1><div class="muted small">${esc([q.sector, q.segment].filter(Boolean).join(" · "))}</div>
        <div class="price num">${kesFull(q.price)}<span class="chg">${deltaAbs(q.change)} (${delta(q.change_pct)})</span></div>
        <div class="row mt">
          <button class="btn" id="watchBtn">${d.watching ? "★ Watching" : "☆ Watch"}</button>
          <a class="btn" href="#/watchlist?alert=${esc(sym)}">🔔 Set alert</a>
          <a class="btn" href="#/portfolio?add=${esc(sym)}">＋ Add to portfolio</a>
          <a class="btn" href="#/compare?s=${esc(sym)}">Compare</a>
        </div></div>
      <div>${pos != null ? `<div class="range-label"><span>52-week range</span><span>${Math.round(pos * 100)}% of range</span></div>
        <div class="range-track"><div class="range-fill" style="width:${pos * 100}%"></div><div class="range-pin" style="left:${pos * 100}%"></div></div>
        <div class="range-ends num"><span>${kesFull(lo)}</span><span>${kesFull(hi)}</span></div>` : ""}
        <div class="metrics mt">
          ${[["Open", num(q.open)], ["Previous close", num(q.prev_close)], ["Day high", num(q.high)], ["Day low", num(q.low)],
             ["Volume", vol(q.volume)], ["Avg volume", vol(q.avg_volume)], ["Turnover", kes(q.turnover)], ["Deals", int(q.deals)],
             ["Market cap", kes(q.market_cap)], ["Bid / Ask", '<span class="tiny muted" title="myStocks shows the order book only to members; no free source publishes it">not published</span>']]
            .map(([k, v]) => `<div class="metric"><div class="k">${k}</div><div class="v num" style="font-size:15px">${v}</div></div>`).join("")}
        </div></div>
    </div>
    <div class="tabs" role="tablist">${["Overview", "Chart", "Technicals", "Fundamentals", "News", "AI analysis"].map((x, i) =>
      `<button role="tab" aria-selected="${i === 0}" data-t="${i}">${x}</button>`).join("")}</div>
    <div id="tab"></div>`;
  $("#watchBtn").onclick = async () => {
    if (!state.user) return location.hash = "#/login?next=" + encodeURIComponent(location.hash);
    if (d.watching) await api("/api/watchlist/" + sym, { method: "DELETE" }); else await api("/api/watchlist", { method: "POST", body: { symbol: sym } });
    d.watching = !d.watching; $("#watchBtn").textContent = d.watching ? "★ Watching" : "☆ Watch";
    toast(d.watching ? sym + " added to your watchlist" : sym + " removed from your watchlist");
  };
  const tabs = [() => overviewTab(q, t, f), () => chartTab(sym, t), () => techTab(t), () => fundTab(f), () => newsTab(sym), () => aiTab(sym)];
  const show = i => {
    $$(".tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.t == i));
    const el = $("#tab"); el.innerHTML = "";
    Promise.resolve(tabs[i](el)).then(h => { if (typeof h === "string") el.innerHTML = h; }).catch(e => el.innerHTML = errorBox(e));
  };
  $$(".tabs button").forEach(b => b.onclick = () => show(+b.dataset.t));
  show(+(params().get("tab") || 0));
}

const PERF = ["1D", "1W", "1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y"];

function overviewTab(q, t, f) {
  const p = t.performance || {};
  const m = f.metrics;
  return `<div class="grid g-21">
    <div class="card"><h2><span class="dot"></span>Company profile</h2>
      <p class="small" style="white-space:pre-line">${esc(q.profile || "No profile published.")}</p>
      <div class="metrics">${[["Shares issued", vol(q.shares_issued)], ["Year end", esc(q.year_end || "n/a")], ["Beta", num(q.beta)],
        ["Segment", esc(q.segment || "n/a")]].map(([k, v]) => `<div class="metric"><div class="k">${k}</div><div class="v small">${v}</div></div>`).join("")}</div></div>
    <div class="card"><h2><span class="dot"></span>Key figures</h2>
      <div class="metrics">${[["P/E", num(m.pe, 1)], ["EPS", num(m.eps)], ["Dividend yield", pct(m.dividend_yield)], ["P/B", num(m.pb)],
        ["ROE", pct(m.roe)], ["Trend", esc(t.trend?.label || "n/a")]].map(([k, v]) => `<div class="metric"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("")}</div></div>
  </div>
  <div class="card mt"><h2><span class="dot"></span>Performance<span class="right">${esc(t.source || "")}</span></h2>
    <div class="table-wrap"><table class="data"><thead><tr>${PERF.map(k => `<th>${k}</th>`).join("")}</tr></thead>
    <tbody><tr>${PERF.map(k => `<td class="num">${delta(p[k], 1)}</td>`).join("")}</tr></tbody></table></div></div>`;
}

async function chartTab(sym, t) {
  const el = $("#tab");
  el.innerHTML = `<div class="card"><div class="row mb">
      <div class="seg" id="rng">${["1M", "3M", "6M", "YTD", "1Y", "3Y", "5Y", "MAX"].map(r => `<button data-r="${r}" aria-pressed="${r === "1Y"}">${r}</button>`).join("")}</div>
      <span class="spacer"></span>
      <label class="check-label"><input type="checkbox" id="oSma" checked> SMA 20/50/200</label>
      <label class="check-label"><input type="checkbox" id="oEma"> EMA 12/26</label>
      <label class="check-label"><input type="checkbox" id="oBb"> Bollinger</label>
      <label class="check-label"><input type="checkbox" id="oSr" checked> Support/resistance</label></div>
    <div class="chart-legend" id="legend"></div>
    <div class="chart-box" id="cMain"></div>
    <div class="pane-label">RSI (14)</div><div class="chart-box small" id="cRsi"></div>
    <div class="pane-label">MACD (12, 26, 9)</div><div class="chart-box small" id="cMacd"></div>
    <div class="pane-label">Volume</div><div class="chart-box small" id="cVol"></div>
    <p class="tiny muted" id="cNote"></p></div>`;
  let rng = "1Y";
  const draw = async () => {
    const c = await api(`/api/stock/${sym}/chart?range=${rng}`);
    const s = c.series;
    if (!s.dates || s.dates.length < 2) {
      $("#cMain").innerHTML = `<div class="empty">No price history stored for ${esc(sym)} yet.<br><span class="tiny">${esc(c.reason || "")}. The app records each day's close; the chart fills in over time.</span></div>`;
      ["#cRsi", "#cMacd", "#cVol"].forEach(x => $(x).innerHTML = "");
      return;
    }
    const col = palette();
    const on = { sma: $("#oSma").checked, ema: $("#oEma").checked, bb: $("#oBb").checked,
      levels: $("#oSr").checked ? [...(t.support || []).map(l => ({ ...l, kind: "support" })), ...(t.resistance || []).map(l => ({ ...l, kind: "resistance" }))] : [] };
    $("#legend").innerHTML = [`<span style="--c:${col[0]}">Close</span>`,
      on.sma && `<span style="--c:${col[1]}">SMA 20</span><span style="--c:${col[2]}">SMA 50</span><span style="--c:${col[4]}">SMA 200</span>`,
      on.ema && `<span style="--c:${col[3]}">EMA 12</span><span style="--c:${col[5]}">EMA 26</span>`,
      on.bb && `<span style="--c:${col[5]}">Bollinger 20/2</span>`].filter(Boolean).join("");
    stockChart({ main: $("#cMain"), rsi: $("#cRsi"), macd: $("#cMacd"), volume: $("#cVol") }, s, on);
    $("#cNote").textContent = `${s.dates.length} trading days shown (of ${c.points_total} stored). Indicators: ${c.source || "n/a"}. Scroll to zoom, drag to pan.`;
  };
  $$("#rng button").forEach(b => b.onclick = () => { rng = b.dataset.r; $$("#rng button").forEach(x => x.setAttribute("aria-pressed", x === b)); draw(); });
  ["#oSma", "#oEma", "#oBb", "#oSr"].forEach(id => $(id).onchange = draw);
  await draw();
}

function techTab(t) {
  if (!t.available) return `<div class="card"><div class="empty">${esc(t.reason || "Technical indicators unavailable.")}</div></div>`;
  const L = t.latest, px = L.close;
  const rel = (v, label) => isNum(v) && isNum(px) ? `<span class="${px >= v ? "up" : "down"} tiny">${px >= v ? "price above" : "price below"} ${label}</span>` : "";
  const rsiNote = !isNum(L.rsi14) ? "" : L.rsi14 >= 70 ? "Overbought (≥70)" : L.rsi14 <= 30 ? "Oversold (≤30)" : L.rsi14 >= 55 ? "Bullish momentum" : L.rsi14 <= 45 ? "Weak momentum" : "Neutral";
  const levels = (arr, cls) => arr && arr.length ? arr.map(l => `<span class="badge ${cls}">${num(l.level)}${l.touches ? ` · ${l.touches}×` : ""}</span>`).join(" ") : NA;
  return `<div class="callout mb">Indicators as of ${esc(fmtDate(t.as_of))} · ${esc(t.source)}</div>
  <div class="grid g-2">
    <div class="card"><h2><span class="dot"></span>Trend</h2><div class="price" style="font-size:24px">${esc(t.trend.label)}</div>
      <p class="small muted">${esc(t.trend.detail)}</p>
      <h3 class="mt">Moving averages</h3>
      <div class="metrics">${[["SMA 20", L.sma20], ["SMA 50", L.sma50], ["SMA 200", L.sma200], ["EMA 12", L.ema12 ?? L.ema20], ["EMA 26", L.ema26 ?? L.ema50]]
        .map(([k, v], i) => `<div class="metric"><div class="k">${i === 3 && L.ema12 == null ? "EMA 20" : i === 4 && L.ema26 == null ? "EMA 50" : k}</div><div class="v num">${num(v)}</div>${rel(v, k)}</div>`).join("")}</div>
      ${(t.crosses_50_200 || []).length ? `<p class="small mt">Recent 50/200-day crosses: ${t.crosses_50_200.map(c => `${c.type === "bullish" ? "golden" : "death"} cross on ${esc(fmtDate(c.date))}`).join("; ")}</p>` : ""}
    </div>
    <div class="card"><h2><span class="dot"></span>Momentum</h2>
      <div class="metrics">
        <div class="metric"><div class="k">RSI (14)</div><div class="v num">${num(L.rsi14, 1)}</div><div class="src">${rsiNote}</div></div>
        <div class="metric"><div class="k">MACD</div><div class="v num">${num(L.macd, 3)}</div><div class="src">signal ${num(L.macd_signal, 3)}</div></div>
        <div class="metric"><div class="k">MACD histogram</div><div class="v num">${num(L.macd_hist, 3)}</div><div class="src">${isNum(L.macd_hist) ? (L.macd_hist >= 0 ? "MACD above signal" : "MACD below signal") : ""}</div></div>
        <div class="metric"><div class="k">Bollinger position</div><div class="v num">${isNum(L.bb_position) ? Math.round(L.bb_position * 100) + "%" : NA}</div><div class="src">${num(L.bb_lower)} – ${num(L.bb_upper)}</div></div>
      </div>
      ${(t.macd_crosses || []).length ? `<p class="small mt">MACD crossed its signal: ${t.macd_crosses.map(c => `${c.type} on ${esc(fmtDate(c.date))}`).join("; ")}</p>` : ""}
    </div>
    <div class="card"><h2><span class="dot"></span>Support &amp; resistance</h2>
      <p class="small">Resistance: ${levels(t.resistance, "bad")}</p><p class="small">Support: ${levels(t.support, "good")}</p>
      <p class="tiny muted">${esc(t.support_method || "Clusters of swing highs and lows over the last 120 sessions (× = times touched).")}</p></div>
    <div class="card"><h2><span class="dot"></span>Volatility &amp; volume</h2>
      <div class="metrics">
        <div class="metric"><div class="k">Volatility (30d, annualised)</div><div class="v num">${pct(L.volatility_30d)}</div></div>
        <div class="metric"><div class="k">Volatility (1y, annualised)</div><div class="v num">${pct(L.volatility_1y)}</div></div>
        ${L.avg_daily_range_1m != null ? `<div class="metric"><div class="k">Avg daily range (1M)</div><div class="v num">${pct(L.avg_daily_range_1m, 2)}</div></div>` : ""}
        <div class="metric"><div class="k">Max drawdown (1y)</div><div class="v num">${pct(L.max_drawdown_1y)}</div></div>
        <div class="metric"><div class="k">Volume vs average</div><div class="v num">${isNum(L.volume_vs_avg) ? L.volume_vs_avg.toFixed(2) + "×" : NA}</div>
          <div class="src">${isNum(L.volume_vs_avg) ? (L.volume_vs_avg >= 2 ? "unusually heavy" : L.volume_vs_avg < 0.5 ? "light trading" : "normal") : ""}</div></div>
      </div></div>
  </div>`;
}

const SRC = s => s ? `<div class="src">${esc(s)}</div>` : `<div class="src">not published</div>`;

function fundTab(f) {
  const m = f.metrics, s = f.sources;
  const tile = (k, label, fmt) => `<div class="metric"><div class="k">${label}</div><div class="v num ${m[k] == null ? "na" : ""}">${m[k] == null ? "unavailable" : fmt(m[k])}</div>${SRC(s[k])}</div>`;
  const h = f.history, bs = f.balance_history, dv = f.dividends;
  const sc = f.scorecard || [];
  const passed = sc.filter(c => c.passed === true).length, total = sc.filter(c => c.passed !== null).length;
  const el = $("#tab");
  el.innerHTML = `${f.error ? `<div class="callout warn mb">Some fundamentals couldn't be loaded: ${esc(f.error)}</div>` : ""}
  <div class="grid g-2">
    <div class="card"><h2><span class="dot"></span>Valuation</h2><div class="metrics">
      ${tile("pe", "P/E (trailing)", v => num(v, 1))}${tile("pe_forward", "P/E (forward)", v => num(v, 1))}${tile("eps", "EPS (KES)", v => num(v))}
      ${tile("pb", "Price / book", v => num(v))}${tile("ps", "Price / sales", v => num(v))}${tile("peg", "PEG", v => num(v))}${tile("ev_ebitda", "EV / EBITDA", v => num(v))}${tile("fcf_yield", "FCF yield", v => pct(v))}</div></div>
    <div class="card"><h2><span class="dot"></span>Profitability &amp; growth</h2><div class="metrics">
      ${tile("revenue", "Revenue", v => kes(v))}${tile("net_income", "Net profit", v => kes(v))}${tile("roe", "Return on equity", v => pct(v))}${tile("roa", "Return on assets", v => pct(v))}
      ${tile("net_margin", "Net margin", v => pct(v))}${tile("operating_margin", "Operating margin", v => pct(v))}${tile("revenue_growth", "Revenue growth", v => pct(v))}${tile("earnings_growth", "EPS growth", v => pct(v))}</div></div>
    <div class="card"><h2><span class="dot"></span>Balance sheet</h2><div class="metrics">
      ${tile("total_assets", "Total assets", v => kes(v))}${tile("total_liabilities", "Total liabilities", v => kes(v))}${tile("equity", "Shareholders' equity", v => kes(v))}
      ${tile("debt_to_equity", "Debt / equity", v => num(v))}${tile("current_ratio", "Current ratio", v => num(v))}${tile("free_cash_flow", "Free cash flow", v => kes(v))}</div></div>
    <div class="card"><h2><span class="dot"></span>Dividends</h2><div class="metrics">
      ${tile("dividend_yield", "Dividend yield", v => pct(v))}${tile("annual_dividend", "Annual dividend (KES)", v => num(v))}${tile("payout_ratio", "Payout ratio", v => pct(v))}
      <div class="metric"><div class="k">Frequency</div><div class="v small">${esc(dv.summary.frequency || "n/a")}</div></div></div>
      <div id="divChart" class="mt"></div></div>
  </div>
  <div class="grid g-2 mt">
    <div class="card"><h2><span class="dot"></span>Annual results<span class="right">KES, fiscal years</span></h2>
      <div class="grid g-2"><div><div class="pane-label">Revenue</div><div id="fRev"></div></div><div><div class="pane-label">Net profit</div><div id="fNi"></div></div>
      <div><div class="pane-label">EPS</div><div id="fEps"></div></div><div><div class="pane-label">Total assets</div><div id="fTa"></div></div></div></div>
    <div class="card"><h2><span class="dot"></span>Dividend history</h2><div class="scroll-y" style="max-height:330px">
      ${dv.history.length ? `<table class="data"><thead><tr><th>Ex-date</th><th>Amount</th><th>Record date</th><th>Pay date</th></tr></thead><tbody>
      ${dv.history.map(x => `<tr><td>${esc(fmtDate(x.ex_date))}</td><td class="num">KES ${num(x.amount, 3)}</td><td>${esc(fmtDate(x.record_date))}</td><td>${esc(fmtDate(x.pay_date))}</td></tr>`).join("")}</tbody></table>`
      : '<div class="empty small">No dividend history published.</div>'}</div></div>
  </div>
  ${sc.length ? `<div class="card mt"><h2><span class="dot"></span>Rule-of-thumb checks<span class="right">${passed}/${total} passed</span></h2><div class="checks">
    ${sc.map(c => `<div class="check ${c.passed === true ? "pass" : c.passed === false ? "fail" : "na"}"><span class="ico">${c.passed === true ? "✓" : c.passed === false ? "✗" : "–"}</span>
      <span class="tiny muted" style="min-width:86px">${esc(c.category)}</span>${esc(c.label)}</div>`).join("")}</div></div>` : ""}
  ${f.analysts?.target ? `<div class="card mt"><h2><span class="dot"></span>Analyst consensus</h2><p>Mean target ${kesFull(f.analysts.target)} · ${esc(f.analysts.rating || "")} from ${f.analysts.count || "?"} analysts (stockanalysis.com)</p></div>` : ""}`;
  const yrs = (h.years || []).slice().reverse();
  const rev = v => (v || []).slice().reverse();
  if (yrs.length) {
    barChart($("#fRev"), yrs, rev(h.revenue), v => kes(v));
    barChart($("#fNi"), yrs, rev(h.net_income), v => kes(v));
    barChart($("#fEps"), yrs, rev(h.eps), v => num(v));
  } else ["#fRev", "#fNi", "#fEps"].forEach(x => $(x).innerHTML = '<div class="empty small">Not published</div>');
  if ((bs.years || []).length) barChart($("#fTa"), bs.years.slice().reverse(), (bs.total_assets || []).slice().reverse(), v => kes(v));
  else $("#fTa").innerHTML = '<div class="empty small">Not published</div>';
  if (dv.by_year?.length) barChart($("#divChart"), dv.by_year.map(x => String(x.year)), dv.by_year.map(x => x.total), v => "KES " + num(v, 2));
}

async function newsTab(sym) {
  const d = await api(`/api/stock/${sym}/news`);
  return `<div class="grid g-21"><div class="card"><h2><span class="dot"></span>News</h2>${newsList(d.news)}</div>
    <div class="card"><h2><span class="dot"></span>Corporate actions</h2>${eventList(d.corporate_actions.map(e => ({ ...e, symbol: sym })))}</div></div>`;
}

async function aiTab(sym) {
  const el = $("#tab");
  el.innerHTML = skeleton(2);
  renderAnalysis(el, await api("/api/analyst/stock/" + sym));
}

// ---------- compare ----------
export async function comparePage(main) {
  const init = (params().get("s") || "").split(",").filter(Boolean);
  main.innerHTML = `<h1>Compare companies</h1><p class="lede">Pick 2–6 stocks to compare prices, returns, valuation, dividends and technicals.</p>
    <div class="card"><div class="row"><input class="i" id="cmpIn" placeholder="Tickers, e.g. KCB, EQTY, COOP" style="max-width:420px;text-transform:uppercase" value="${esc(init.join(", "))}">
      <div class="seg" id="cRng">${["6M", "1Y", "3Y", "5Y"].map(r => `<button data-r="${r}" aria-pressed="${r === "1Y"}">${r}</button>`).join("")}</div>
      <button class="btn primary" id="cmpGo">Compare</button></div>
      <div class="row mt-s">${[["Banks", "KCB,EQTY,COOP,ABSA,NCBA,SCBK"], ["Insurers", "BRIT,JUB,CIC,KNRE,LBTY"], ["Telco vs banks", "SCOM,KCB,EQTY"], ["Energy", "KEGN,KPLC,TOTL,KPC"]]
        .map(([l, s]) => `<button class="btn small" data-s="${s}">${l}</button>`).join("")}</div></div>
    <div id="cmpOut" class="mt"></div>`;
  let rng = "1Y";
  const run = async () => {
    const syms = $("#cmpIn").value.toUpperCase().split(/[\s,]+/).filter(Boolean);
    if (syms.length < 2) { $("#cmpOut").innerHTML = '<div class="empty">Enter at least two tickers.</div>'; return; }
    history.replaceState(null, "", "#/compare?s=" + syms.join(","));
    $("#cmpOut").innerHTML = skeleton(2);
    try {
      const d = await api(`/api/compare?symbols=${syms.join(",")}&range=${rng}`);
      const fmt = (f, v) => f === "kes" ? kes(v) : f === "pct" ? pct(v) : num(v);
      $("#cmpOut").innerHTML = `${d.missing.length ? `<div class="callout warn mb">Not found: ${esc(d.missing.join(", "))}</div>` : ""}
        <div class="card"><h2><span class="dot"></span>Price performance<span class="right">${esc(d.chart_note)}</span></h2>
          <div class="chart-legend">${d.symbols.map((s, i) => `<span style="--c:${palette()[i]}">${s}</span>`).join("")}</div>
          <div class="chart-box" id="cmpChart"></div></div>
        <div class="card mt"><h2><span class="dot"></span>Side by side<span class="right">★ = best in row</span></h2><div class="table-wrap"><table class="data">
          <thead><tr><th></th>${d.symbols.map(s => `<th>${stockLink(s)}</th>`).join("")}</tr></thead><tbody>
          ${d.table.map(r => `<tr><td>${r.label}</td>${d.symbols.map(s => `<td class="num ${r.best === s ? "best" : ""}">${fmt(r.format, r.values[s])}</td>`).join("")}</tr>`).join("")}
          <tr><td><b>Research signal</b></td>${d.symbols.map(s => `<td><span class="badge ${({ Buy: "good", Sell: "bad" })[d.analyses[s].signal] || "neutral"}">${esc(d.analyses[s].signal)} ${isNum(d.analyses[s].score) ? (d.analyses[s].score > 0 ? "+" : "") + d.analyses[s].score : ""}</span></td>`).join("")}</tr>
          </tbody></table></div></div>
        <div class="card mt"><h2><span class="dot"></span>Analyst summary</h2><ul class="tight">${d.summary.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
          <p class="tiny muted mt">Data: prices ${esc(fmtDate(d.data_date.prices))}, indicators ${esc(fmtDate(d.data_date.indicators))}. ${esc(d.disclaimer)}</p></div>`;
      const lines = d.symbols.filter(s => d.chart[s]).map(s => ({ name: s, data: pts(d.chart[s].dates, d.chart[s].value) }));
      if (lines.some(l => l.data.length > 1)) lineChart($("#cmpChart"), lines);
      else $("#cmpChart").innerHTML = '<div class="empty">Not enough stored price history yet to chart these stocks.</div>';
    } catch (e) { $("#cmpOut").innerHTML = errorBox(e); }
  };
  $("#cmpGo").onclick = run;
  $("#cmpIn").onkeydown = e => { if (e.key === "Enter") run(); };
  $$("#cRng button").forEach(b => b.onclick = () => { rng = b.dataset.r; $$("#cRng button").forEach(x => x.setAttribute("aria-pressed", x === b)); run(); });
  $$("[data-s]", main).forEach(b => { if (b.classList.contains("btn")) b.onclick = () => { $("#cmpIn").value = b.dataset.s.replace(/,/g, ", "); run(); }; });
  if (init.length >= 2) run();
}

// ---------- screener ----------
const FILTERS = [
  ["price", "Share price (KES)", "range"], ["market_cap", "Market cap (KES bn)", "range", 1e9],
  ["dividend_yield", "Dividend yield (%)", "range", 0.01], ["pe", "P/E", "range"],
  ["eps", "EPS (KES)", "range"], ["pb", "P/B", "range"], ["roe", "ROE (%)", "range", 0.01],
  ["debt_to_equity", "Debt/equity", "range"], ["rsi14", "RSI (14)", "range"],
  ["perf_1y", "1-year return (%)", "range", 0.01], ["change_pct", "Today's change (%)", "range", 0.01],
  ["volume", "Volume today", "range"],
];
const PRESETS = {
  "High dividend": { dividend_yield: [6, null] },
  "Value": { pe: [null, 8], pb: [null, 1.5] },
  "Quality": { roe: [15, null], debt_to_equity: [null, 1] },
  "Momentum": { perf_1y: [20, null], above_sma200: true },
  "Oversold": { rsi14: [null, 35] },
  "Large caps": { market_cap: [50, null] },
};
export async function screenerPage(main) {
  main.innerHTML = `<h1>Stock screener</h1><p class="lede">Filter NSE stocks on price, size, valuation, dividends, profitability and technicals. Stocks missing a value are excluded only when you filter on that field.</p>
    <div class="card"><div class="row mb"><span class="small muted">Presets:</span>${Object.keys(PRESETS).map(p => `<button class="btn small" data-p="${p}">${p}</button>`).join("")}
      <button class="btn small" id="reset">Reset</button></div>
      <div class="form-grid" id="flts">${FILTERS.map(([k, label]) => `<label class="f">${label}<span class="row" style="flex-wrap:nowrap;gap:4px">
        <input class="i" type="number" step="any" placeholder="min" data-k="${k}" data-b="0"><input class="i" type="number" step="any" placeholder="max" data-k="${k}" data-b="1"></span></label>`).join("")}
        <label class="f">Sector<select class="i" id="fSec"><option value="">Any</option></select></label>
        <label class="f">Trend<select class="i" id="fTrend"><option value="">Any</option><option value="up">Above 200-day average</option><option value="down">Below 200-day average</option></select></label>
      </div>
      <div class="row mt"><span class="small" id="scrCount"></span><span class="spacer"></span><button class="btn small" id="csv">Download CSV</button></div></div>
    <div class="card mt"><div id="scrTbl"></div><p class="tiny muted mt" id="scrNote"></p></div>`;
  const d = await api("/api/screener");
  $("#fSec").innerHTML += [...new Set(d.rows.map(r => r.sector).filter(Boolean))].sort().map(s => `<option>${esc(s)}</option>`).join("");
  $("#scrNote").textContent = `Prices: ${d.data_status} data for ${fmtDate(d.prices_as_of)}. Valuation and technical fields: ${d.indicators_source || "unavailable"}${d.indicators_as_of ? ", " + fmtDate(d.indicators_as_of, true) : ""}.`;
  const cols = [
    { key: "symbol", label: "Ticker", fmt: r => `<span class="sym">${r.symbol}</span>` },
    { key: "name", label: "Company", cls: "l", fmt: r => `<span class="nm">${esc(r.name)}</span>` },
    { key: "price", label: "Price", fmt: r => num(r.price) },
    { key: "change_pct", label: "Today", fmt: r => delta(r.change_pct) },
    { key: "market_cap", label: "Mkt cap", fmt: r => isNum(r.market_cap) ? compact(r.market_cap) : NA },
    { key: "pe", label: "P/E", fmt: r => num(r.pe, 1) },
    { key: "pb", label: "P/B", fmt: r => num(r.pb) },
    { key: "dividend_yield", label: "Div yield", fmt: r => pct(r.dividend_yield) },
    { key: "roe", label: "ROE", fmt: r => pct(r.roe) },
    { key: "rsi14", label: "RSI", fmt: r => num(r.rsi14, 0) },
    { key: "perf_1y", label: "1Y", fmt: r => delta(r.perf_1y, 0) },
    { key: "volume", label: "Volume", fmt: r => vol(r.volume) },
  ];
  const tbl = sortableTable($("#scrTbl"), d.rows, cols, { onRow: r => location.hash = "#/stock/" + r.symbol, initial: { key: "market_cap", dir: -1 }, empty: "No stocks match these filters." });
  let current = d.rows;
  const apply = () => {
    const f = {};
    $$("#flts input").forEach(i => { if (i.value !== "") (f[i.dataset.k] ||= [null, null])[+i.dataset.b] = +i.value; });
    const sec = $("#fSec").value, trend = $("#fTrend").value;
    current = d.rows.filter(r => {
      for (const [k, , , mult = 1] of FILTERS) {
        if (!f[k]) continue;
        const v = r[k];
        if (v == null) return false;
        const [lo, hi] = f[k];
        if (lo != null && v < lo * mult) return false;
        if (hi != null && v > hi * mult) return false;
      }
      if (sec && r.sector !== sec) return false;
      if (trend === "up" && r.above_sma200 !== true) return false;
      if (trend === "down" && r.above_sma200 !== false) return false;
      return true;
    });
    $("#scrCount").textContent = `${current.length} of ${d.rows.length} stocks match`;
    tbl.update(current);
  };
  $$("#flts input, #flts select").forEach(i => i.oninput = apply);
  $$("[data-p]", main).forEach(b => b.onclick = () => {
    $$("#flts input").forEach(i => i.value = ""); $("#fTrend").value = ""; $("#fSec").value = "";
    for (const [k, v] of Object.entries(PRESETS[b.dataset.p])) {
      if (k === "above_sma200") { $("#fTrend").value = v ? "up" : "down"; continue; }
      v.forEach((x, i) => { if (x != null) $(`#flts input[data-k="${k}"][data-b="${i}"]`).value = x; });
    }
    apply();
  });
  $("#reset").onclick = () => { $$("#flts input").forEach(i => i.value = ""); $("#fSec").value = ""; $("#fTrend").value = ""; apply(); };
  $("#csv").onclick = () => {
    const keys = ["symbol", "name", "sector", "price", "change_pct", "market_cap", "pe", "eps", "pb", "dividend_yield", "roe", "debt_to_equity", "rsi14", "perf_1y", "volume"];
    const csv = [keys.join(","), ...current.map(r => keys.map(k => r[k] == null ? "" : typeof r[k] === "string" ? `"${r[k].replace(/"/g, '""')}"` : r[k]).join(","))].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `nse-screener-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
  };
  apply();
}

// ---------- news ----------
export async function newsPage(main) {
  const tabs = [["market", "NSE & markets"], ["earnings", "Earnings"], ["dividends", "Dividends"], ["cbk", "Central Bank of Kenya"], ["economy", "Economy & inflation"], ["calendar", "Corporate calendar"]];
  const start = params().get("tab") || "market";
  main.innerHTML = `<h1>News &amp; market events</h1><p class="lede">Headlines link to the original publishers. Dividend dates come from the NSE corporate calendar (myStocks) or, when that's unreachable, from stockanalysis.com.</p>
    <div class="tabs">${tabs.map(([k, l]) => `<button data-k="${k}" aria-selected="${k === start}">${l}</button>`).join("")}</div><div id="newsOut"></div>`;
  const show = async k => {
    $$(".tabs button").forEach(b => b.setAttribute("aria-selected", b.dataset.k === k));
    $("#newsOut").innerHTML = skeleton(2, 100);
    try {
      if (k === "calendar") {
        const d = await api("/api/calendar");
        const today = new Date().toISOString().slice(0, 10);
        const up = d.events.filter(e => e.date >= today).reverse(), past = d.events.filter(e => e.date < today);
        $("#newsOut").innerHTML = `<p class="tiny muted">Source: ${esc(d.source || "n/a")}</p><div class="grid g-2"><div class="card"><h2><span class="dot"></span>Upcoming</h2>${eventList(up)}</div>
          <div class="card"><h2><span class="dot"></span>Recent</h2><div class="scroll-y">${eventList(past)}</div></div></div>`;
      } else {
        const d = await api("/api/news?topic=" + k);
        $("#newsOut").innerHTML = `<div class="card">${newsList(d.items)}</div>`;
      }
    } catch (e) { $("#newsOut").innerHTML = errorBox(e); }
  };
  $$(".tabs button").forEach(b => b.onclick = () => show(b.dataset.k));
  show(start);
}
