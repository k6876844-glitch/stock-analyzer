// Account views: login, register, account, portfolio, watchlist & alerts.
import { api, state, esc, isNum, NA, kes, kesFull, num, vol, pct, delta, deltaAbs, fmtDate, ago, $, $$, toast,
  errorBox, skeleton, params, loadUser, sortableTable, refreshNotes } from "./core.js";
import { lineChart, pts, divergingBars } from "./charts.js";

const today = () => new Date().toISOString().slice(0, 10);
const symOptions = () => state.stocks.map(s => `<option value="${s.symbol}">${esc(s.name)}</option>`).join("");

// ---------- auth ----------
export function loginPage(main) { authForm(main, "login"); }
export function registerPage(main) { authForm(main, "register"); }

function authForm(main, mode) {
  const next = params().get("next") || "#/portfolio";
  const reg = mode === "register";
  main.innerHTML = `<div class="card auth-card fade-in"><h2><span class="dot"></span>${reg ? "Create your free account" : "Log in"}</h2>
    <form id="af">${reg ? `<label class="f">Name<input class="i" name="name" autocomplete="name" required maxlength="80"></label>` : ""}
      <label class="f">Email<input class="i" name="email" type="email" autocomplete="email" required></label>
      <label class="f">Password${reg ? " (8+ characters)" : ""}<input class="i" name="password" type="password" autocomplete="${reg ? "new-password" : "current-password"}" required minlength="${reg ? 8 : 1}"></label>
      <div id="aerr"></div>
      <button class="btn primary" type="submit">${reg ? "Create account" : "Log in"}</button>
      <p class="small muted">${reg ? `Already have an account? <a href="#/login?next=${encodeURIComponent(next)}">Log in</a>` : `New here? <a href="#/register?next=${encodeURIComponent(next)}">Create an account</a>`}</p>
    </form>
    <p class="tiny muted">An account lets you keep portfolios, a watchlist and price alerts. Your password is stored only as a salted hash.</p></div>`;
  $("#af").onsubmit = async e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    const btn = $("#af button"); btn.disabled = true;
    try {
      await api("/api/auth/" + mode, { method: "POST", body: fd });
      await loadUser();
      toast(reg ? "Welcome, " + state.user.name.split(" ")[0] + "!" : "Logged in");
      location.hash = next;
    } catch (err) { $("#aerr").innerHTML = errorBox(err); btn.disabled = false; }
  };
}

export async function accountPage(main) {
  const u = state.user;
  main.innerHTML = `<h1>Account</h1><div class="grid g-2">
    <div class="card"><h2><span class="dot"></span>Profile</h2><p><b>${esc(u.name)}</b><br><span class="muted">${esc(u.email)}</span></p>
      <label class="check-label"><input type="checkbox" id="emailAl" ${u.email_alerts ? "checked" : ""}> Email me when an alert triggers</label>
      <p class="tiny muted">${state.emailAvailable ? "Email delivery is configured." : "Email delivery isn't configured on this server yet, so alerts arrive in-app only."}</p>
      <div class="row mt"><button class="btn danger" id="logout">Log out</button></div></div>
    <div class="card"><h2><span class="dot"></span>Change password</h2><form id="pw" class="grid" style="gap:10px">
      <label class="f">Current password<input class="i" type="password" name="old" autocomplete="current-password" required></label>
      <label class="f">New password (8+ characters)<input class="i" type="password" name="new" autocomplete="new-password" minlength="8" required></label>
      <div id="pwMsg"></div><button class="btn primary" type="submit">Update password</button></form></div></div>`;
  $("#emailAl").onchange = async e => { await api("/api/auth/settings", { method: "PATCH", body: { email_alerts: e.target.checked } }); state.user.email_alerts = e.target.checked; toast("Saved"); };
  $("#logout").onclick = async () => { await api("/api/auth/logout", { method: "POST" }); await loadUser(); location.hash = "#/"; };
  $("#pw").onsubmit = async e => {
    e.preventDefault();
    try { await api("/api/auth/password", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); $("#pwMsg").innerHTML = '<div class="okmsg">Password updated.</div>'; e.target.reset(); }
    catch (err) { $("#pwMsg").innerHTML = errorBox(err); }
  };
}

// ---------- portfolio ----------
export async function portfolioPage(main) {
  const { portfolios } = await api("/api/portfolios");
  let pid = +(params().get("p") || 0) || portfolios[0]?.id;
  const addSym = params().get("add");
  main.innerHTML = `<div class="row"><h1>Portfolio</h1><span class="spacer"></span>
      <select class="i" id="pSel" style="max-width:220px">${portfolios.map(p => `<option value="${p.id}" ${p.id === pid ? "selected" : ""}>${esc(p.name)}</option>`).join("")}</select>
      <button class="btn small" id="pNew">New</button><button class="btn small" id="pRen">Rename</button><button class="btn small danger" id="pDel">Delete</button></div>
    <p class="lede">Values use the latest prices (delayed or end-of-day). Profit and loss include purchase fees; total return adds dividends you record.</p>
    <div id="pBody">${skeleton(2)}</div>
    <datalist id="symList">${symOptions()}</datalist>`;
  $("#pSel").onchange = e => { location.hash = "#/portfolio?p=" + e.target.value; };
  $("#pNew").onclick = async () => { const n = prompt("Name for the new portfolio:", "Portfolio " + (portfolios.length + 1)); if (!n) return; const r = await api("/api/portfolios", { method: "POST", body: { name: n } }); location.hash = "#/portfolio?p=" + r.id; };
  $("#pRen").onclick = async () => { const n = prompt("New name:", portfolios.find(p => p.id === pid)?.name); if (!n) return; await api("/api/portfolios/" + pid, { method: "PATCH", body: { name: n } }); portfolioPage(main); };
  $("#pDel").onclick = async () => {
    if (portfolios.length <= 1) return toast("Keep at least one portfolio.");
    if (!confirm("Delete this portfolio and all its holdings? This can't be undone.")) return;
    await api("/api/portfolios/" + pid, { method: "DELETE" }); location.hash = "#/portfolio";
  };
  if (!pid) { $("#pBody").innerHTML = '<div class="empty">No portfolio yet.</div>'; return; }
  const load = async () => {
    const v = await api("/api/portfolios/" + pid);
    const S = v.summary;
    $("#pBody").innerHTML = `
      <div class="grid g-4">
        <div class="stat"><div class="k">Market value</div><div class="v num">${kes(S.value)}</div><div class="d">${deltaAbs(S.day_change)} today (${delta(S.day_change_pct)})</div></div>
        <div class="stat"><div class="k">Amount invested</div><div class="v num">${kes(S.cost)}</div><div class="s">${S.positions} position${S.positions === 1 ? "" : "s"}</div></div>
        <div class="stat"><div class="k">Profit / loss</div><div class="v num ${S.gain >= 0 ? "up" : "down"}">${kes(S.gain)}</div><div class="d">${delta(S.gain_pct)}</div></div>
        <div class="stat"><div class="k">Total return incl. dividends</div><div class="v num ${S.total_return >= 0 ? "up" : "down"}">${kes(S.total_return)}</div><div class="d">${delta(S.total_return_pct)} · dividends ${kes(S.dividends)}</div></div>
      </div>
      ${S.unpriced.length ? `<div class="callout warn mt">No current price for ${esc(S.unpriced.join(", "))}; they're left out of the totals.</div>` : ""}
      <div class="card mt"><h2><span class="dot"></span>Add a purchase</h2>
        <form id="hForm" class="form-grid">
          <label class="f">Stock<input class="i" name="symbol" list="symList" required placeholder="e.g. SCOM" style="text-transform:uppercase" value="${esc(addSym || "")}"></label>
          <label class="f">Quantity<input class="i" name="quantity" type="number" min="1" step="any" required></label>
          <label class="f">Price paid (KES)<input class="i" name="price" type="number" min="0.01" step="any" required></label>
          <label class="f">Fees (KES)<input class="i" name="fees" type="number" min="0" step="any" value="0"></label>
          <label class="f">Date<input class="i" name="bought_on" type="date" value="${today()}" max="${today()}" required></label>
          <button class="btn primary" type="submit">Add</button></form><div id="hErr"></div>
        <p class="tiny muted">Tip: NSE trades carry roughly 2.1% in brokerage, levies and CDSC fees; include them in Fees for accurate P/L.</p></div>
      <div class="card mt"><h2><span class="dot"></span>Positions</h2><div id="posTbl"></div></div>
      <div class="grid g-21 mt">
        <div class="card"><h2><span class="dot"></span>Portfolio value vs. amount invested</h2><div class="chart-box mid" id="pChart"></div><p class="tiny muted" id="pChartNote"></p></div>
        <div class="card"><h2><span class="dot"></span>Allocation</h2><div id="alloc"></div></div>
      </div>
      <div class="grid g-2 mt">
        <div class="card"><h2><span class="dot"></span>Record a dividend received</h2>
          <form id="dForm" class="form-grid"><label class="f">Stock<input class="i" name="symbol" list="symList" required style="text-transform:uppercase"></label>
          <label class="f">Amount (KES, after tax)<input class="i" name="amount" type="number" min="0.01" step="any" required></label>
          <label class="f">Paid on<input class="i" name="paid_on" type="date" value="${today()}" max="${today()}" required></label>
          <button class="btn primary" type="submit">Record</button></form><div id="dErr"></div></div>
        <div class="card"><h2><span class="dot"></span>Dividends received</h2><div id="divTbl"></div></div>
      </div>
      <div class="card mt"><h2><span class="dot"></span>Purchase lots</h2><div id="lotTbl"></div></div>`;
    sortableTable($("#posTbl"), v.positions, [
      { key: "symbol", label: "Stock", fmt: r => `<a class="sym" href="#/stock/${r.symbol}">${r.symbol}</a> <span class="nm">${esc(r.name)}</span>`, cls: "l" },
      { key: "quantity", label: "Shares", fmt: r => num(r.quantity, 0) },
      { key: "avg_cost", label: "Avg cost", fmt: r => num(r.avg_cost) },
      { key: "price", label: "Price", fmt: r => num(r.price) },
      { key: "value", label: "Value", fmt: r => kes(r.value) },
      { key: "day_change", label: "Today", fmt: r => deltaAbs(r.day_change) },
      { key: "gain", label: "P/L", fmt: r => `<span class="${r.gain >= 0 ? "up" : "down"}">${kes(r.gain)}</span>` },
      { key: "gain_pct", label: "P/L %", fmt: r => delta(r.gain_pct) },
      { key: "dividends", label: "Dividends", fmt: r => kes(r.dividends) },
      { key: "total_return_pct", label: "Total return", fmt: r => delta(r.total_return_pct) },
      { key: "weight", label: "Weight", fmt: r => pct(r.weight) },
    ], { empty: "No holdings yet. Add your first purchase above.", initial: { key: "value", dir: -1 } });
    const lots = v.positions.flatMap(p => p.lots.map(l => ({ ...l })));
    sortableTable($("#lotTbl"), lots, [
      { key: "bought_on", label: "Date", fmt: r => esc(fmtDate(r.bought_on)), cls: "l" },
      { key: "symbol", label: "Stock", fmt: r => `<span class="sym">${r.symbol}</span>` },
      { key: "quantity", label: "Shares", fmt: r => num(r.quantity, 0) },
      { key: "price", label: "Price", fmt: r => num(r.price) },
      { key: "fees", label: "Fees", fmt: r => num(r.fees) },
      { key: "id", label: "", sort: false, fmt: r => `<button class="btn small danger" data-del-lot="${r.id}">Remove</button>` },
    ], { empty: "No purchases recorded." });
    sortableTable($("#divTbl"), v.dividends, [
      { key: "paid_on", label: "Paid", fmt: r => esc(fmtDate(r.paid_on)), cls: "l" },
      { key: "symbol", label: "Stock", fmt: r => `<span class="sym">${r.symbol}</span>` },
      { key: "amount", label: "Amount", fmt: r => kesFull(r.amount) },
      { key: "id", label: "", sort: false, fmt: r => `<button class="btn small danger" data-del-div="${r.id}">Remove</button>` },
    ], { empty: "No dividends recorded." });
    $$("[data-del-lot]").forEach(b => b.onclick = async () => { if (!confirm("Remove this purchase?")) return; await api(`/api/portfolios/${pid}/holdings/${b.dataset.delLot}`, { method: "DELETE" }); load(); });
    $$("[data-del-div]").forEach(b => b.onclick = async () => { await api(`/api/portfolios/${pid}/dividends/${b.dataset.delDiv}`, { method: "DELETE" }); load(); });
    const totalW = v.positions.filter(p => p.weight);
    if (totalW.length) divergingBars($("#alloc"), totalW.map(p => ({ label: p.symbol, value: p.weight })), x => (x * 100).toFixed(1) + "%");
    else $("#alloc").innerHTML = '<div class="empty small">Add holdings to see allocation.</div>';
    $("#hForm").onsubmit = async e => {
      e.preventDefault();
      const fd = Object.fromEntries(new FormData(e.target)); fd.symbol = fd.symbol.toUpperCase().trim();
      try { await api(`/api/portfolios/${pid}/holdings`, { method: "POST", body: fd }); toast(`Added ${fd.quantity} ${fd.symbol}`); history.replaceState(null, "", "#/portfolio?p=" + pid); load(); }
      catch (err) { $("#hErr").innerHTML = errorBox(err); }
    };
    $("#dForm").onsubmit = async e => {
      e.preventDefault();
      const fd = Object.fromEntries(new FormData(e.target)); fd.symbol = fd.symbol.toUpperCase().trim();
      try { await api(`/api/portfolios/${pid}/dividends`, { method: "POST", body: fd }); toast("Dividend recorded"); load(); }
      catch (err) { $("#dErr").innerHTML = errorBox(err); }
    };
    const perf = await api(`/api/portfolios/${pid}/performance?days=730`);
    if (perf.dates.length > 1) {
      lineChart($("#pChart"), [{ name: "Value", data: pts(perf.dates, perf.value) }, { name: "Invested", data: pts(perf.dates, perf.invested), dashed: true }], { area: true });
      $("#pChartNote").textContent = "Solid: market value from stored closing prices. Dashed: cumulative amount invested.";
    } else {
      $("#pChart").innerHTML = '<div class="empty">The chart appears once there are at least two days of prices since your first purchase.</div>';
    }
  };
  await load();
}

// ---------- watchlist & alerts ----------
export async function watchlistPage(main) {
  const pre = params().get("alert");
  main.innerHTML = `<h1>Watchlist &amp; alerts</h1>
    <p class="lede">Alerts are checked every few minutes while the market is open. Each alert fires once, then switches off until you re-arm it.</p>
    <div class="card"><h2><span class="dot"></span>Watchlist</h2>
      <form id="wForm" class="row mb"><input class="i" name="symbol" list="symList" placeholder="Add a stock, e.g. KCB" style="max-width:260px;text-transform:uppercase" required>
      <button class="btn primary" type="submit">Add</button></form><div id="wTbl">${skeleton(1, 80)}</div></div>
    <div class="card mt"><h2><span class="dot"></span>New alert</h2>
      <form id="aForm" class="form-grid">
        <label class="f">Stock<input class="i" name="symbol" list="symList" required style="text-transform:uppercase" value="${esc(pre || "")}"></label>
        <label class="f">When<select class="i" name="kind" id="aKind"></select></label>
        <label class="f" id="aValL">Value<input class="i" name="value" type="number" step="any" min="0" id="aVal"></label>
        <label class="check-label"><input type="checkbox" name="email" checked> Email me too</label>
        <button class="btn primary" type="submit">Create alert</button></form>
      <div id="aErr"></div><p class="tiny muted" id="aNote"></p></div>
    <div class="card mt"><h2><span class="dot"></span>Your alerts</h2><div id="aTbl"></div></div>
    <datalist id="symList">${symOptions()}</datalist>`;
  const loadW = async () => {
    const w = await api("/api/watchlist");
    sortableTable($("#wTbl"), w.stocks, [
      { key: "symbol", label: "Stock", cls: "l", fmt: r => `<span class="sym">${r.symbol}</span> <span class="nm">${esc(r.name)}</span>` },
      { key: "price", label: "Price", fmt: r => num(r.price) },
      { key: "change_pct", label: "Today", fmt: r => r.volume ? delta(r.change_pct) : '<span class="tiny muted">no trades</span>' },
      { key: "volume", label: "Volume", fmt: r => vol(r.volume) },
      { key: "symbol", label: "", sort: false, fmt: r => `<button class="btn small" data-alert="${r.symbol}">Alert</button> <button class="btn small danger" data-unwatch="${r.symbol}">Remove</button>` },
    ], { onRow: r => location.hash = "#/stock/" + r.symbol, empty: "Your watchlist is empty. Add stocks above or with ☆ Watch on any stock page." });
    $$("[data-unwatch]").forEach(b => b.onclick = async () => { await api("/api/watchlist/" + b.dataset.unwatch, { method: "DELETE" }); loadW(); });
    $$("[data-alert]").forEach(b => b.onclick = () => { $("#aForm [name=symbol]").value = b.dataset.alert; $("#aForm").scrollIntoView({ behavior: "smooth" }); $("#aVal").focus(); });
  };
  const loadA = async () => {
    const a = await api("/api/alerts");
    if (!$("#aKind").options.length) {
      $("#aKind").innerHTML = Object.entries(a.kinds).map(([k, l]) => `<option value="${k}">${esc(l)}</option>`).join("");
      const hint = () => {
        const k = $("#aKind").value;
        $("#aValL").classList.toggle("hide", k === "dividend");
        $("#aVal").placeholder = { price_above: "KES", price_below: "KES", pct_up: "% e.g. 5", pct_down: "% e.g. 5", volume_spike: "× e.g. 3" }[k] || "";
        $("#aNote").textContent = { volume_spike: "Compares today's volume with the stock's average daily volume.", dividend: "Fires when a new dividend declaration, book closure or payment is listed for the stock." }[k] || "";
      };
      $("#aKind").onchange = hint; hint();
    }
    if (!a.email_available) $("#aNote").textContent += " Email delivery isn't configured on this server yet, so alerts arrive in-app (bell icon).";
    sortableTable($("#aTbl"), a.alerts, [
      { key: "description", label: "Alert", cls: "l", fmt: r => esc(r.description) },
      { key: "active", label: "Status", fmt: r => r.active ? '<span class="badge info">armed</span>' : `<span class="badge good">fired ${esc(ago(r.triggered_at))}</span>` },
      { key: "email", label: "Email", fmt: r => r.email ? "yes" : "no" },
      { key: "created_at", label: "Created", fmt: r => esc(fmtDate(r.created_at)) },
      { key: "id", label: "", sort: false, fmt: r => `${r.active ? "" : `<button class="btn small" data-rearm="${r.id}">Re-arm</button> `}<button class="btn small danger" data-del="${r.id}">Delete</button>` },
    ], { empty: "No alerts yet." });
    $$("[data-del]").forEach(b => b.onclick = async () => { await api("/api/alerts/" + b.dataset.del, { method: "DELETE" }); loadA(); });
    $$("[data-rearm]").forEach(b => b.onclick = async () => { await api(`/api/alerts/${b.dataset.rearm}/rearm`, { method: "POST" }); toast("Alert re-armed"); loadA(); });
  };
  $("#wForm").onsubmit = async e => {
    e.preventDefault();
    const s = new FormData(e.target).get("symbol").toUpperCase().trim();
    try { await api("/api/watchlist", { method: "POST", body: { symbol: s } }); e.target.reset(); loadW(); }
    catch (err) { toast(err.message); }
  };
  $("#aForm").onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const body = { symbol: fd.get("symbol").toUpperCase().trim(), kind: fd.get("kind"), value: fd.get("value") || null, email: fd.get("email") === "on" };
    try { await api("/api/alerts", { method: "POST", body }); $("#aErr").innerHTML = ""; toast("Alert created"); $("#aVal").value = ""; loadA(); refreshNotes(); }
    catch (err) { $("#aErr").innerHTML = errorBox(err); }
  };
  await Promise.all([loadW(), loadA()]);
}
