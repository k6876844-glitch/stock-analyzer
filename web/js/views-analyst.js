// Rule-based analyst: report renderer + analyst page.
import { api, esc, fmtDate, $, $$, errorBox, skeleton, num, isNum, kes, delta } from "./core.js";
import { divergingBars } from "./charts.js";

const factorHtml = f => `<div class="factor"><span class="sc ${f.score > 0 ? "p" : f.score < 0 ? "n" : "z"}">${f.score > 0 ? "+" : ""}${f.score}</span>
  <div><div class="t">${esc(f.name)}<small>${esc(f.value)}</small></div><div class="x">${esc(f.explanation)}</div></div></div>`;

function meta(a) {
  const dd = a.data_date || {};
  const dates = Object.entries(dd).filter(([, v]) => v).map(([k, v]) => `<li><b>${esc(k.replace(/_/g, " "))}:</b> ${esc(String(v).length === 10 || String(v).includes("T") ? fmtDate(v, String(v).includes("T")) : v)}</li>`).join("");
  return `<div class="grid g-3 mt">
    <div class="card"><h2><span class="dot"></span>Data date</h2><ul class="tight">${dates || "<li>n/a</li>"}</ul></div>
    <div class="card"><h2><span class="dot"></span>Assumptions</h2><ul class="tight">${(a.assumptions || []).map(x => `<li>${esc(x)}</li>`).join("") || "<li>None</li>"}</ul></div>
    <div class="card"><h2><span class="dot"></span>Risk warnings</h2><ul class="tight">${(a.risks || []).map(x => `<li>${esc(x)}</li>`).join("") || "<li>See disclaimer</li>"}</ul></div>
  </div>
  <div class="callout warn mt"><b>Not investment advice.</b> ${esc(a.disclaimer)}</div>`;
}

export function renderAnalysis(el, a) {
  if (a.type === "stock") {
    el.innerHTML = `<div class="card fade-in">
      <div class="row"><span class="signal ${esc(a.signal)}">${esc(a.signal)}</span>
        <div><div style="font-weight:700">${esc(a.name)} (${esc(a.symbol)})</div>
        <div class="small muted">Educational research signal · score ${a.score > 0 ? "+" : ""}${a.score} · confidence ${esc(a.confidence)}</div></div>
        <span class="spacer"></span><a class="btn small" href="#/stock/${esc(a.symbol)}">Open stock page</a></div>
      <div class="mt">${a.summary.map(p => `<p>${esc(p)}</p>`).join("")}</div>
      <p class="small"><b>${esc(a.reasoning)}</b></p></div>
      <div class="grid g-2 mt">
        <div class="card"><h2><span class="dot"></span>Technical factors</h2>${a.technical_factors.map(factorHtml).join("") || '<div class="empty small">No technical data</div>'}
          ${(a.support || []).length || (a.resistance || []).length ? `<p class="small mt">Nearest support ${a.support?.[0] ? num(a.support[0].level) : "n/a"} · nearest resistance ${a.resistance?.[0] ? num(a.resistance[0].level) : "n/a"}</p>` : ""}</div>
        <div class="card"><h2><span class="dot"></span>Fundamental factors</h2>${a.fundamental_factors.map(factorHtml).join("") || '<div class="empty small">No fundamental data</div>'}</div>
      </div>${meta(a)}`;
  } else if (a.type === "compare") {
    el.innerHTML = `<div class="card fade-in"><h2><span class="dot"></span>Comparison: ${a.symbols.map(esc).join(" vs ")}</h2>
      <ul class="tight">${a.summary.map(x => `<li>${esc(x)}</li>`).join("")}</ul>
      <div class="table-wrap mt"><table class="data"><thead><tr><th></th>${a.symbols.map(s => `<th>${esc(s)}</th>`).join("")}</tr></thead><tbody>
      ${a.table.map(r => `<tr><td>${esc(r.label)}</td>${a.symbols.map(s => {
        const v = r.values[s]; const t = r.format === "kes" ? kes(v) : r.format === "pct" ? (isNum(v) ? (v * 100).toFixed(1) + "%" : "n/a") : num(v);
        return `<td class="num ${r.best === s ? "best" : ""}">${t}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>
      <p class="small mt"><a href="#/compare?s=${a.symbols.join(",")}">Open the comparison page with charts →</a></p></div>${meta(a)}`;
  } else if (a.type === "market") {
    el.innerHTML = `<div class="card fade-in"><h2><span class="dot"></span>${esc(a.headline)}</h2>${a.summary.map(p => `<p>${esc(p)}</p>`).join("")}
      <p class="small muted">${esc(a.explanation)}</p></div>
      <div class="grid g-2 mt"><div class="card"><h2><span class="dot"></span>Sector performance</h2><div id="repSectors"></div></div>
        <div class="card"><h2><span class="dot"></span>Headlines that may be related</h2>${(a.headlines || []).map(n => `<div class="news-item"><a href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a><div class="meta">${esc(n.source || "")} · ${esc(fmtDate(n.published, true))}</div></div>`).join("") || '<div class="empty small">No headlines</div>'}
        ${(a.cbk || []).length ? `<h3 class="mt">Central Bank of Kenya</h3>${a.cbk.map(n => `<div class="news-item"><a href="${esc(n.url)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a><div class="meta">${esc(fmtDate(n.published))}</div></div>`).join("")}` : ""}</div></div>${meta(a)}`;
    divergingBars($("#repSectors", el), a.sectors.map(s => ({ label: `${s.sector} (${s.stocks})`, value: s.avg_change })), v => (v >= 0 ? "+" : "") + (v * 100).toFixed(2) + "%");
  } else if (a.type === "explain") {
    el.innerHTML = `<div class="card fade-in"><h2><span class="dot"></span>${esc(a.headline)}</h2>${a.summary.map(p => `<p>${esc(p)}</p>`).join("")}
      <p class="tiny muted">${esc(a.disclaimer)}</p></div>`;
  }
}

export async function analystPage(main) {
  main.innerHTML = `<h1>AI stock analyst</h1>
    <p class="lede">Rule-based research assistant: it reads the app's live data and explains what the numbers say, step by step. Every report shows its data date, assumptions and risks. Educational only, never a guarantee.</p>
    <div class="card"><div class="ask-box"><input class="i" id="askIn" placeholder="Ask: Analyze SCOM · Compare KCB and Equity · Market report · What is MACD?">
      <button class="btn primary" id="askGo">Ask</button></div>
      <div class="row mt-s">${["Market report", "Analyze SCOM", "Compare KCB, EQTY and COOP", "Analyze EABL", "What is RSI?", "Explain Bollinger bands", "What is P/E?"]
        .map(q => `<button class="btn small" data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div></div>
    <div id="askOut" class="mt"></div>`;
  const ask = async q => {
    $("#askIn").value = q;
    $("#askOut").innerHTML = skeleton(2);
    try {
      const a = /^market report$/i.test(q.trim()) ? await api("/api/analyst/market") : await api("/api/analyst/ask", { method: "POST", body: { question: q } });
      renderAnalysis($("#askOut"), a);
    } catch (e) { $("#askOut").innerHTML = errorBox(e); }
  };
  $("#askGo").onclick = () => ask($("#askIn").value);
  $("#askIn").onkeydown = e => { if (e.key === "Enter") ask($("#askIn").value); };
  $$("[data-q]", main).forEach(b => b.onclick = () => ask(b.dataset.q));
  ask("Market report");
}
