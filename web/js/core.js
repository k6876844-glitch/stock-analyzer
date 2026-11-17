// Core: API client, formatting, router, shared state, header widgets.

export const state = { user: null, emailAvailable: false, stocks: [], market: null, routeCleanup: [] };

// ---------- API ----------
export async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {}, credentials: "same-origin" };
  if (init.method !== "GET") {
    init.headers["X-Requested-With"] = "ksa";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body || {});
  }
  let r;
  try { r = await fetch(path, init); }
  catch { throw new Error("Can't reach the server. Check your connection and try again."); }
  let data;
  try { data = await r.json(); }
  catch {
    throw new Error(r.status >= 500
      ? `Server error ${r.status}. The server may be waking up (free hosting sleeps when idle); try again in a minute.`
      : `Unexpected response (${r.status}).`);
  }
  if (!r.ok) {
    const e = new Error(data.error || r.statusText);
    e.status = r.status;
    if (r.status === 401 && state.user) { state.user = null; renderUser(); }
    throw e;
  }
  return data;
}

// ---------- formatting ----------
export const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
export const isNum = x => typeof x === "number" && Number.isFinite(x);
export const NA = '<span class="muted" title="Not published by the data sources">n/a</span>';

export function compact(x, d = 2) {
  if (!isNum(x)) return null;
  const a = Math.abs(x);
  for (const [u, v] of [["T", 1e12], ["B", 1e9], ["M", 1e6], ["K", 1e3]]) if (a >= v) return (x / v).toFixed(d) + u;
  return x.toFixed(d);
}
export const kes = (x, d = 2) => isNum(x) ? "KES " + (Math.abs(x) >= 1e4 ? compact(x, d) : x.toLocaleString("en-KE", { minimumFractionDigits: d, maximumFractionDigits: d })) : NA;
export const kesFull = (x, d = 2) => isNum(x) ? "KES " + x.toLocaleString("en-KE", { minimumFractionDigits: d, maximumFractionDigits: d }) : NA;
export const num = (x, d = 2) => isNum(x) ? x.toLocaleString("en-KE", { minimumFractionDigits: d, maximumFractionDigits: d }) : NA;
export const int = x => isNum(x) ? Math.round(x).toLocaleString("en-KE") : NA;
export const vol = x => isNum(x) ? (x >= 1e4 ? compact(x, 2) : Math.round(x).toLocaleString("en-KE")) : NA;
export const pct = (x, d = 1) => isNum(x) ? (x * 100).toFixed(d) + "%" : NA;
export function delta(x, d = 2) {
  if (!isNum(x)) return NA;
  if (x === 0) return `<span class="flat">0.00%</span>`;
  const up = x > 0;
  return `<span class="${up ? "up" : "down"}">${up ? "▲ +" : "▼ "}${(x * 100).toFixed(d)}%</span>`;
}
export function deltaAbs(x, d = 2) {
  if (!isNum(x)) return NA;
  if (x === 0) return `<span class="flat">0.00</span>`;
  return `<span class="${x > 0 ? "up" : "down"}">${x > 0 ? "+" : ""}${x.toFixed(d)}</span>`;
}
export function fmtDate(s, withTime = false) {
  if (!s) return "n/a";
  const d = new Date(s.length === 10 ? s + "T12:00:00" : s);
  if (isNaN(d)) return esc(s);
  return d.toLocaleDateString("en-KE", { day: "numeric", month: "short", year: "numeric" })
    + (withTime ? " " + d.toLocaleTimeString("en-KE", { hour: "2-digit", minute: "2-digit" }) : "");
}
export function ago(s) {
  if (!s) return "";
  const m = Math.round((Date.now() - new Date(s)) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return m + " min ago";
  if (m < 1440) return Math.round(m / 60) + " h ago";
  return Math.round(m / 1440) + " d ago";
}
export function dataPill(status, asOf) {
  const map = { "live": ["live", "Live"], "delayed": ["delayed", "Delayed"], "end-of-day": ["eod", "End of day"] };
  const [cls, label] = map[status] || ["na", "Unknown freshness"];
  const tip = { live: "Real-time prices", delayed: "Prices delayed during trading", eod: "Closing prices from the last session" }[cls] || "";
  return `<span class="pill ${cls}" title="${esc(tip)}">${label}${asOf ? " · " + esc(fmtDate(asOf)) : ""}</span>`;
}
export function marketPill(st) {
  if (!st) return "";
  const label = { open: "Market open", closed: "Market closed", "pre-open": "Pre-open" }[st.state] || st.state;
  return `<span class="pill ${esc(st.state)}" title="${esc(st.note)}"><span class="led"></span>${label}</span>`;
}

// ---------- UI helpers ----------
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export function toast(msg, ms = 2600) {
  const t = document.createElement("div");
  t.className = "toast"; t.textContent = msg; t.setAttribute("role", "status");
  document.body.appendChild(t);
  setTimeout(() => t.remove(), ms);
}
export const errorBox = e => `<div class="error">${esc(e.message || e)}</div>`;
export const skeleton = (n = 1, h = 140) => Array.from({ length: n }, () => `<div class="skeleton" style="height:${h}px"></div>`).join("");
export function onCleanup(fn) { state.routeCleanup.push(fn); }
export function every(ms, fn) { const id = setInterval(fn, ms); onCleanup(() => clearInterval(id)); }

// sortable tables: rows = array of objects, cols = [{key, label, fmt(row), cls, sort:false}]
export function sortableTable(el, rows, cols, { onRow, initial, empty = "Nothing to show." } = {}) {
  let sort = initial || null;
  const draw = () => {
    let r = rows.slice();
    if (sort) {
      const { key, dir } = sort;
      r.sort((a, b) => {
        const x = a[key], y = b[key];
        if (x == null && y == null) return 0;
        if (x == null) return 1;
        if (y == null) return -1;
        return (typeof x === "string" ? x.localeCompare(y) : x - y) * dir;
      });
    }
    el.innerHTML = `<div class="table-wrap"><table class="data"><thead><tr>${cols.map(c =>
      `<th class="${c.sort === false ? "" : "sortable"} ${c.cls || ""}" data-k="${c.key}" ${sort && sort.key === c.key ? `aria-sort="${sort.dir > 0 ? "ascending" : "descending"}"` : ""}>${c.label}</th>`).join("")}</tr></thead>
      <tbody>${r.length ? r.map((row, i) => `<tr class="${onRow ? "click" : ""}" data-i="${rows.indexOf(row)}">${cols.map(c => `<td class="${c.cls || ""}">${c.fmt ? c.fmt(row) : esc(row[c.key])}</td>`).join("")}</tr>`).join("")
        : `<tr><td colspan="${cols.length}" class="empty">${empty}</td></tr>`}</tbody></table></div>`;
    $$("th.sortable", el).forEach(th => th.onclick = () => {
      const k = th.dataset.k;
      sort = { key: k, dir: sort && sort.key === k ? -sort.dir : (typeof rows.find(x => x[k] != null)?.[k] === "string" ? 1 : -1) };
      draw();
    });
    if (onRow) $$("tbody tr.click", el).forEach(tr => tr.onclick = e => { if (!e.target.closest("button,a,input")) onRow(rows[+tr.dataset.i]); });
  };
  draw();
  return { update(newRows) { rows = newRows; draw(); } };
}

// ---------- theme ----------
export function initTheme() {
  try { const t = localStorage.getItem("theme"); if (t) document.documentElement.dataset.theme = t; } catch {}
  $("#themeBtn").onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch {}
    window.dispatchEvent(new Event("themechange"));
  };
}
export const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// ---------- session ----------
export async function loadUser() {
  try { const d = await api("/api/auth/me"); state.user = d.user; state.emailAvailable = d.email_available; }
  catch { state.user = null; }
  renderUser();
}
export function renderUser() {
  const box = $("#userBox");
  if (state.user) {
    box.innerHTML = `<button class="icon-btn" id="bellBtn" title="Notifications" aria-label="Notifications">
        <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/></svg>
        <span class="dot-count hide" id="bellCount"></span></button>
      <a class="user-btn" href="#/account" title="Account">${esc(state.user.name.split(" ")[0])}</a>`;
    $("#bellBtn").onclick = toggleDrawer;
    pollNotes();
  } else {
    box.innerHTML = `<a class="user-btn" href="#/login">Log in</a>`;
  }
}
let notesTimer = null;
async function pollNotes() {
  clearTimeout(notesTimer);
  if (!state.user) return;
  try {
    const d = await api("/api/notifications");
    const c = $("#bellCount");
    if (c) { c.textContent = d.unread; c.classList.toggle("hide", !d.unread); }
    state.notes = d;
  } catch {}
  notesTimer = setTimeout(pollNotes, 120000);
}
async function toggleDrawer() {
  const ex = $("#drawer");
  if (ex) { ex.remove(); return; }
  const el = document.createElement("div");
  el.id = "drawer"; el.className = "card drawer fade-in";
  el.innerHTML = `<h2><span class="dot"></span>Notifications<span class="right"><button class="btn small" id="readAll">Mark all read</button></span></h2><div id="noteList">${skeleton(1, 60)}</div>`;
  document.body.appendChild(el);
  const d = await api("/api/notifications").catch(e => ({ items: [], err: e }));
  $("#noteList").innerHTML = d.items.length ? d.items.map(n => `<div class="note ${n.read ? "" : "unread"}">
      <div class="nt">${n.link ? `<a href="${esc(n.link)}">${esc(n.title)}</a>` : esc(n.title)}</div>
      <div class="nb">${esc(n.body)}</div><div class="tiny muted">${esc(ago(n.created_at))}</div></div>`).join("")
    : `<div class="empty">No notifications yet. Set price alerts on the <a href="#/watchlist">Watchlist</a> page.</div>`;
  $("#readAll").onclick = async () => { await api("/api/notifications/read", { method: "POST" }); el.remove(); pollNotes(); };
  setTimeout(() => document.addEventListener("click", function close(e) {
    if (!el.contains(e.target) && !e.target.closest("#bellBtn")) { el.remove(); document.removeEventListener("click", close); }
  }), 0);
}
export const refreshNotes = pollNotes;

// ---------- stock list for search ----------
export async function loadStocks() {
  try {
    const d = await api("/api/market");
    state.market = d;
    state.stocks = d.stocks;
  } catch {}
  return state.stocks;
}
export function initSearch() {
  const input = $("#topSearch"), box = $("#suggest");
  let active = 0, items = [];
  const close = () => { box.classList.add("hide"); };
  const go = s => { close(); input.value = ""; location.hash = "#/stock/" + s; };
  input.addEventListener("input", () => {
    const q = input.value.trim().toLowerCase();
    if (!q) return close();
    items = state.stocks.filter(s => s.symbol.toLowerCase().startsWith(q) || s.name.toLowerCase().includes(q)).slice(0, 10);
    active = 0;
    box.innerHTML = items.length ? items.map((s, i) => `<button type="button" class="${i === 0 ? "active" : ""}" data-s="${s.symbol}">
        <span class="sym">${s.symbol}</span><span class="nm">${esc(s.name)}</span><span class="num">${num(s.price)}</span></button>`).join("")
      : `<div class="empty small">No match</div>`;
    box.classList.remove("hide");
    $$("button", box).forEach(b => b.onclick = () => go(b.dataset.s));
  });
  input.addEventListener("keydown", e => {
    const btns = $$("button", box);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!btns.length) return;
      btns[active]?.classList.remove("active");
      active = (active + (e.key === "ArrowDown" ? 1 : -1) + btns.length) % btns.length;
      btns[active].classList.add("active");
    } else if (e.key === "Enter") {
      e.preventDefault();
      const b = btns[active];
      if (b) go(b.dataset.s);
      else if (input.value.trim()) go(input.value.trim().toUpperCase());
    } else if (e.key === "Escape") close();
  });
  document.addEventListener("click", e => { if (!e.target.closest(".top-search")) close(); });
}

// ---------- router ----------
const routes = [];
export function addRoute(pattern, view, { auth = false } = {}) { routes.push({ rx: new RegExp("^" + pattern + "$"), view, auth }); }
export function params() { return new URLSearchParams(location.hash.split("?")[1] || ""); }
export function requireLogin(next) {
  return `<div class="card auth-card"><h2><span class="dot"></span>Log in required</h2>
    <p>You need an account to use this page. It's free.</p>
    <div class="row"><a class="btn primary" href="#/login?next=${encodeURIComponent(next)}">Log in</a>
    <a class="btn" href="#/register?next=${encodeURIComponent(next)}">Create account</a></div></div>`;
}
export async function router() {
  state.routeCleanup.forEach(f => { try { f(); } catch {} });
  state.routeCleanup = [];
  const path = (location.hash.slice(1) || "/").split("?")[0];
  const main = $("#main");
  $$("nav.main a").forEach(a => a.setAttribute("aria-current", path === a.getAttribute("href").slice(1) || (a.dataset.prefix && path.startsWith(a.dataset.prefix)) ? "page" : "false"));
  for (const r of routes) {
    const m = path.match(r.rx);
    if (!m) continue;
    window.scrollTo(0, 0);
    if (r.auth && !state.user) { main.innerHTML = requireLogin(location.hash); return; }
    main.innerHTML = skeleton(2);
    try { await r.view(main, ...m.slice(1).map(decodeURIComponent)); }
    catch (e) { console.error(e); main.innerHTML = errorBox(e); }
    return;
  }
  main.innerHTML = `<div class="empty"><h1>Page not found</h1><a href="#/">Back to the dashboard</a></div>`;
}
