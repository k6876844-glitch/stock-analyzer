// Charts on TradingView's open-source Lightweight Charts (loaded as a global from unpkg).
import { cssVar, onCleanup, isNum } from "./core.js";

const LWC = () => window.LightweightCharts;

function theme() {
  return {
    layout: { background: { type: "solid", color: "transparent" }, textColor: cssVar("--text-muted"),
              fontFamily: "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, sans-serif", fontSize: 11 },
    grid: { vertLines: { color: cssVar("--grid") }, horzLines: { color: cssVar("--grid") } },
    rightPriceScale: { borderColor: cssVar("--axis") },
    timeScale: { borderColor: cssVar("--axis"), timeVisible: false },
    crosshair: { mode: 1 },
  };
}

export const palette = () => [cssVar("--series-1"), cssVar("--series-2"), cssVar("--series-3"),
  cssVar("--series-4"), cssVar("--series-5"), cssVar("--series-6")];

function make(el, opts = {}) {
  if (!LWC()) { el.innerHTML = '<div class="empty small">Chart library failed to load. Check your connection.</div>'; return null; }
  el.innerHTML = "";
  const chart = LWC().createChart(el, { ...theme(), autoSize: true, handleScroll: true, handleScale: true, ...opts });
  const onTheme = () => chart.applyOptions(theme());
  window.addEventListener("themechange", onTheme);
  onCleanup(() => { window.removeEventListener("themechange", onTheme); chart.remove(); });
  return chart;
}

export const pts = (dates, values) => {
  const out = [];
  for (let i = 0; i < dates.length; i++) if (isNum(values[i])) out.push({ time: dates[i], value: values[i] });
  return out;
};

/** Simple multi-line chart. lines: [{name, data:[{time,value}], color?, width?, dashed?}] */
export function lineChart(el, lines, { percent = false, area = false, priceFormat } = {}) {
  const chart = make(el, percent ? { rightPriceScale: { mode: 0 } } : {});
  if (!chart) return null;
  const colors = palette();
  lines.forEach((l, i) => {
    const color = l.color || colors[i % colors.length];
    const s = area && i === 0
      ? chart.addAreaSeries({ lineColor: color, topColor: color + "44", bottomColor: color + "05", lineWidth: l.width || 2, priceLineVisible: false })
      : chart.addLineSeries({ color, lineWidth: l.width || 2, lineStyle: l.dashed ? 2 : 0, priceLineVisible: false,
          lastValueVisible: !l.dashed, ...(priceFormat ? { priceFormat } : {}) });
    s.setData(l.data);
  });
  chart.timeScale().fitContent();
  return chart;
}

/** Stock chart: price + overlays, then RSI, MACD and volume panes with synced time axes. */
export function stockChart(els, s, on) {
  const colors = palette();
  const main = make(els.main);
  if (!main) return;
  const charts = [main];
  const price = main.addAreaSeries({ lineColor: colors[0], topColor: colors[0] + "40", bottomColor: colors[0] + "05", lineWidth: 2 });
  price.setData(pts(s.dates, s.close));
  const add = (key, color, dashed = false, width = 1.5) => {
    if (!s[key]) return;
    const ser = main.addLineSeries({ color, lineWidth: width, lineStyle: dashed ? 2 : 0, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    ser.setData(pts(s.dates, s[key]));
  };
  if (on.sma) { add("sma20", colors[1]); add("sma50", colors[2]); add("sma200", colors[4], false, 2); }
  if (on.ema) { add("ema12", colors[3], true); add("ema26", colors[5], true); }
  if (on.bb) { add("bb_upper", colors[5], true, 1); add("bb_middle", colors[5], false, 1); add("bb_lower", colors[5], true, 1); }
  (on.levels || []).forEach(l => price.createPriceLine({ price: l.level, color: l.kind === "support" ? cssVar("--good") : cssVar("--critical"),
    lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: l.kind === "support" ? "S" : "R" }));

  if (els.rsi && s.rsi14) {
    const c = make(els.rsi);
    const r = c.addLineSeries({ color: colors[2], lineWidth: 1.5, priceLineVisible: false });
    r.setData(pts(s.dates, s.rsi14));
    [70, 30].forEach(v => r.createPriceLine({ price: v, color: cssVar("--axis"), lineWidth: 1, lineStyle: 2, axisLabelVisible: true }));
    charts.push(c);
  }
  if (els.macd && s.macd) {
    const c = make(els.macd);
    const h = c.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
    h.setData(s.dates.map((d, i) => isNum(s.macd_hist?.[i]) ? { time: d, value: s.macd_hist[i], color: s.macd_hist[i] >= 0 ? cssVar("--good") + "aa" : cssVar("--critical") + "aa" } : null).filter(Boolean));
    c.addLineSeries({ color: colors[0], lineWidth: 1.5, priceLineVisible: false }).setData(pts(s.dates, s.macd));
    c.addLineSeries({ color: colors[1], lineWidth: 1.5, priceLineVisible: false }).setData(pts(s.dates, s.macd_signal));
    charts.push(c);
  }
  if (els.volume) {
    const vols = pts(s.dates, s.volume || []);
    if (vols.length >= 2) {
      const c = make(els.volume);
      c.addHistogramSeries({ color: colors[5] + "99", priceFormat: { type: "volume" }, priceLineVisible: false }).setData(vols);
      charts.push(c);
    } else {
      els.volume.innerHTML = `<div class="empty small">Daily volume history starts building from the day this app began recording closes.</div>`;
    }
  }
  // keep panes scrolled/zoomed together
  let syncing = false;
  charts.forEach(c => c.timeScale().subscribeVisibleLogicalRangeChange(range => {
    if (syncing || !range) return;
    syncing = true;
    charts.forEach(o => o !== c && o.timeScale().setVisibleLogicalRange(range));
    syncing = false;
  }));
  main.timeScale().fitContent();
}

/** Horizontal bars centred on zero, e.g. sector % changes. rows: [{label, value}] */
export function divergingBars(el, rows, fmt) {
  const max = Math.max(...rows.map(r => Math.abs(r.value || 0)), 1e-9);
  el.innerHTML = `<div class="bars">${rows.map(r => {
    const w = Math.abs(r.value || 0) / max * 50;
    return `<div class="bar-row"><span class="nm" title="${r.label}">${r.label}</span>
      <div class="track" role="img" aria-label="${r.label}: ${fmt(r.value)}"><div class="fill ${r.value >= 0 ? "pos" : "neg"}" style="width:${w}%"></div></div>
      <span class="num" style="text-align:right">${fmt(r.value)}</span></div>`;
  }).join("")}</div>`;
}

/** Vertical bar chart (SVG) for annual figures. */
export function barChart(el, labels, values, fmt) {
  const W = 320, H = 150, pad = 22, n = values.length;
  const vals = values.map(v => (isNum(v) ? v : null));
  const max = Math.max(...vals.map(v => Math.abs(v || 0)), 1e-9);
  const hasNeg = vals.some(v => v < 0);
  const zero = hasNeg ? H / 2 : H - pad;
  const scale = (hasNeg ? H / 2 - 6 : H - pad - 8) / max;
  const bw = (W - 10) / n * 0.62, gap = (W - 10) / n;
  const c = cssVar("--series-1"), cn = cssVar("--critical");
  el.innerHTML = `<svg viewBox="0 0 ${W} ${H + 4}" role="img" aria-label="bar chart" style="width:100%;height:auto;max-height:180px">
    <line x1="0" x2="${W}" y1="${zero}" y2="${zero}" stroke="${cssVar("--axis")}"/>
    ${vals.map((v, i) => {
      const x = 5 + i * gap + (gap - bw) / 2;
      if (v == null) return `<text x="${x + bw / 2}" y="${zero - 4}" font-size="10" text-anchor="middle" fill="${cssVar("--text-muted")}">n/a</text>`;
      const h = Math.max(1, Math.abs(v) * scale);
      const y = v >= 0 ? zero - h : zero;
      return `<rect x="${x}" y="${y}" width="${bw}" height="${h}" rx="3" fill="${v >= 0 ? c : cn}"><title>${labels[i]}: ${fmt(v)}</title></rect>
        <text x="${x + bw / 2}" y="${H + 2}" font-size="10" text-anchor="middle" fill="${cssVar("--text-muted")}">${labels[i]}</text>`;
    }).join("")}</svg>`;
}
