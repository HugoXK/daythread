"""A polished loopback dashboard served by the API."""

from __future__ import annotations

import json

TEMPLATE = r"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daythread · 朝夕</title>
<style>
:root {
  --bg: #070A11;
  --bg-2: #0B101B;
  --surface: rgba(18, 24, 38, .82);
  --surface-2: rgba(24, 32, 50, .86);
  --line: rgba(255, 255, 255, .08);
  --line-strong: rgba(255, 255, 255, .15);
  --text: #F2F6FD;
  --muted: #8D99AE;
  --faint: #5C677B;
  --warm: #FFB86B;
  --warm-2: #FF8E72;
  --cool: #7AA2FF;
  --cool-2: #B28CFF;
  --success: #5BD6A2;
  --warn: #FFC46B;
  --danger: #FF7A90;
  --shadow: 0 24px 70px rgba(0, 0, 0, .38);
  --radius: 20px;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color-scheme: dark;
}
[data-theme="light"] {
  --bg: #F4F6FB;
  --bg-2: #EEF1F8;
  --surface: rgba(255, 255, 255, .86);
  --surface-2: rgba(255, 255, 255, .96);
  --line: rgba(16, 24, 40, .08);
  --line-strong: rgba(16, 24, 40, .16);
  --text: #121826;
  --muted: #62708A;
  --faint: #8A96AB;
  --warm: #E78A2E;
  --warm-2: #F0645A;
  --cool: #3B68F5;
  --cool-2: #7A54E8;
  --success: #159B6B;
  --warn: #B9770E;
  --danger: #D92D4B;
  --shadow: 0 24px 70px rgba(16, 24, 40, .10);
  color-scheme: light;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  font: 14px/1.55 -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Inter, sans-serif;
  color: var(--text);
  background:
    radial-gradient(900px 620px at 6% -12%, rgba(255, 184, 107, .14), transparent 60%),
    radial-gradient(900px 680px at 104% -6%, rgba(122, 162, 255, .16), transparent 58%),
    linear-gradient(180deg, var(--bg), var(--bg-2));
  background-attachment: fixed;
  -webkit-font-smoothing: antialiased;
}
button, input { font: inherit; }
button { cursor: pointer; }
a { color: var(--cool); }

.app { display: grid; grid-template-columns: 252px minmax(0, 1fr); min-height: 100vh; }

.sidebar {
  position: sticky; top: 0; height: 100vh;
  padding: 22px 16px;
  border-right: 1px solid var(--line);
  background: linear-gradient(180deg, rgba(255,255,255,.03), transparent 40%);
  display: flex; flex-direction: column; gap: 26px;
}
.brand { display: flex; align-items: center; gap: 12px; padding: 4px 8px; }
.brand .mark { width: 42px; height: 42px; filter: drop-shadow(0 8px 20px rgba(255, 184, 107, .18)); }
.brand .name { font-size: 17px; font-weight: 650; letter-spacing: -.02em; }
.brand .sub { font-size: 11px; color: var(--muted); letter-spacing: .16em; text-transform: uppercase; }
nav { display: flex; flex-direction: column; gap: 4px; }
.nav-item {
  display: flex; align-items: center; gap: 10px;
  width: 100%; padding: 10px 12px; border: 0; border-radius: 12px;
  background: transparent; color: var(--muted); text-align: left;
  transition: background .18s ease, color .18s ease, transform .18s ease;
}
.nav-item svg { width: 17px; height: 17px; opacity: .9; }
.nav-item:hover { background: rgba(255,255,255,.05); color: var(--text); }
.nav-item.active {
  color: var(--text);
  background: linear-gradient(90deg, rgba(255,184,107,.14), rgba(122,162,255,.08));
  box-shadow: inset 0 0 0 1px var(--line);
}
.sidebar-footer { margin-top: auto; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 0 6px; }
.status { display: flex; align-items: center; gap: 7px; font-size: 12px; color: var(--muted); }
.status .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--success); box-shadow: 0 0 0 4px rgba(91,214,162,.12); }
.icon-btn {
  width: 34px; height: 34px; display: grid; place-items: center;
  border: 1px solid var(--line); border-radius: 10px; background: transparent; color: var(--muted);
}
.icon-btn:hover { color: var(--text); border-color: var(--line-strong); }

main { min-width: 0; padding: 28px clamp(20px, 4vw, 48px) 48px; }
.topbar { display: flex; align-items: flex-start; gap: 16px; margin-bottom: 22px; }
.topbar h1 { margin: 0; font-size: clamp(24px, 3vw, 34px); line-height: 1.1; letter-spacing: -.03em; }
.topbar p { margin: 6px 0 0; color: var(--muted); }
.topbar .actions { margin-left: auto; display: flex; gap: 8px; align-items: center; }
.btn {
  border: 1px solid var(--line-strong); background: transparent; color: var(--text);
  padding: 8px 12px; border-radius: 10px; transition: border-color .18s, background .18s, transform .18s;
}
.btn:hover { border-color: var(--cool); background: rgba(122,162,255,.08); }
.btn:active { transform: translateY(1px); }
.btn.primary { border-color: transparent; background: linear-gradient(135deg, var(--warm), var(--warm-2)); color: #1A1206; font-weight: 650; }
.btn.cool { border-color: transparent; background: linear-gradient(135deg, var(--cool), var(--cool-2)); color: white; font-weight: 650; }
.btn.danger { border-color: rgba(255,122,144,.5); color: var(--danger); }
.btn.small { padding: 5px 9px; border-radius: 8px; font-size: 12px; }
.btn:disabled { opacity: .5; cursor: default; }

.banner {
  display: none; margin-bottom: 18px; padding: 12px 14px; border-radius: 12px;
  border: 1px solid rgba(255,122,144,.35); background: rgba(255,122,144,.08); color: var(--danger);
}
.banner.show { display: block; }

.view { animation: rise .28s ease both; }
.view[hidden] { display: none; }
@keyframes rise { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }

.hero {
  position: relative; overflow: hidden; border-radius: calc(var(--radius) + 4px);
  border: 1px solid var(--line); background:
    radial-gradient(700px 260px at 10% 0%, rgba(255,184,107,.14), transparent 60%),
    radial-gradient(620px 260px at 100% 0%, rgba(122,162,255,.14), transparent 60%),
    var(--surface);
  padding: 24px 26px; box-shadow: var(--shadow); margin-bottom: 20px;
}
.hero::after {
  content: ""; position: absolute; inset: auto -10% -70% 40%;
  height: 260px; background: radial-gradient(closest-side, rgba(255,184,107,.14), transparent);
  pointer-events: none;
}
.hero .eyebrow { font-size: 11px; letter-spacing: .18em; text-transform: uppercase; color: var(--warm); font-weight: 650; }
.hero h2 { margin: 8px 0 6px; font-size: clamp(22px, 3vw, 32px); letter-spacing: -.03em; }
.hero p { margin: 0; color: var(--muted); max-width: 720px; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }
.chip {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 6px 10px; border-radius: 999px; border: 1px solid var(--line);
  background: rgba(255,255,255,.03); color: var(--muted); font-size: 12px;
}
.chip b { color: var(--text); font-weight: 600; }
.chip .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--cool); }
.chip.warm .dot { background: var(--warm); }
.chip.warn .dot { background: var(--warn); }

.grid { display: grid; grid-template-columns: repeat(12, minmax(0, 1fr)); gap: 16px; }
.card {
  grid-column: span 4;
  border: 1px solid var(--line); border-radius: var(--radius);
  background: var(--surface); padding: 18px; min-height: 120px;
  box-shadow: 0 10px 30px rgba(0,0,0,.12);
  transition: border-color .18s ease, transform .18s ease;
}
.card:hover { border-color: var(--line-strong); }
.card.wide { grid-column: span 8; }
.card.full { grid-column: 1 / -1; }
.card h3 {
  margin: 0 0 12px; font-size: 12px; letter-spacing: .14em; text-transform: uppercase;
  color: var(--muted); font-weight: 650; display: flex; align-items: center; justify-content: space-between;
}
.card h3 span { font-variant-numeric: tabular-nums; color: var(--faint); letter-spacing: 0; }
.list { display: flex; flex-direction: column; }
.row {
  display: flex; gap: 10px; align-items: flex-start; justify-content: space-between;
  padding: 10px 0; border-top: 1px solid var(--line);
}
.row:first-child { border-top: 0; padding-top: 0; }
.row:last-child { padding-bottom: 0; }
.row .title { font-weight: 550; }
.row .meta { color: var(--muted); font-size: 12px; margin-top: 2px; }
.row .actions { display: flex; gap: 6px; flex-shrink: 0; }
.mono { font-family: var(--mono); font-size: 12px; }
.empty { color: var(--faint); font-size: 13px; padding: 8px 0; }
.empty::before { content: "—"; margin-right: 8px; color: var(--faint); }

.badge {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 600;
  border: 1px solid var(--line); color: var(--muted); background: rgba(255,255,255,.03);
}
.badge .dot { width: 5px; height: 5px; border-radius: 50%; background: currentColor; }
.badge.pending { color: var(--warn); border-color: rgba(255,196,107,.35); background: rgba(255,196,107,.08); }
.badge.completed { color: var(--success); border-color: rgba(91,214,162,.32); background: rgba(91,214,162,.08); }
.badge.failed, .badge.denied, .badge.rejected { color: var(--danger); border-color: rgba(255,122,144,.32); background: rgba(255,122,144,.08); }
.badge.running { color: var(--cool); border-color: rgba(122,162,255,.32); background: rgba(122,162,255,.08); }

.toolbar { display: flex; gap: 10px; align-items: center; margin-bottom: 16px; }
.search {
  flex: 1; display: flex; align-items: center; gap: 10px;
  border: 1px solid var(--line); border-radius: 12px; background: var(--surface); padding: 10px 14px;
}
.search input { flex: 1; border: 0; outline: 0; background: transparent; color: var(--text); }
.search input::placeholder { color: var(--faint); }

.table { width: 100%; border-collapse: collapse; }
.table th {
  text-align: left; font-size: 11px; letter-spacing: .12em; text-transform: uppercase;
  color: var(--muted); font-weight: 650; padding: 10px 12px; border-bottom: 1px solid var(--line);
}
.table td { padding: 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
.table tr:last-child td { border-bottom: 0; }
.table tr:hover td { background: rgba(255,255,255,.02); }

.skeleton { position: relative; overflow: hidden; border-radius: 8px; background: rgba(255,255,255,.06); min-height: 14px; }
.skeleton::after {
  content: ""; position: absolute; inset: 0; transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, rgba(255,255,255,.10), transparent);
  animation: shimmer 1.4s infinite;
}
@keyframes shimmer { to { transform: translateX(100%); } }

@media (max-width: 1040px) {
  .app { grid-template-columns: 76px minmax(0, 1fr); }
  .brand .name, .brand .sub, .nav-item span, .sidebar-footer .status { display: none; }
  .sidebar { align-items: center; padding: 20px 10px; }
  .nav-item { justify-content: center; }
  .card { grid-column: span 6; }
  .card.wide { grid-column: span 12; }
}
@media (max-width: 720px) {
  .app { grid-template-columns: 1fr; }
  .sidebar { position: static; height: auto; flex-direction: row; align-items: center; overflow-x: auto; }
  .sidebar-footer { margin: 0; }
  nav { flex-direction: row; }
  main { padding: 20px 16px 40px; }
  .card, .card.wide { grid-column: 1 / -1; }
  .topbar { flex-wrap: wrap; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <svg class="mark" viewBox="0 0 64 64" aria-hidden="true">
        <defs>
          <linearGradient id="mg" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stop-color="#0A1020"/><stop offset=".6" stop-color="#141D31"/><stop offset="1" stop-color="#1C2A48"/>
          </linearGradient>
          <linearGradient id="mt" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stop-color="#FFB86B"/><stop offset=".5" stop-color="#FFD7A1"/><stop offset="1" stop-color="#7AA2FF"/>
          </linearGradient>
          <radialGradient id="ms" cx="50%" cy="45%" r="55%">
            <stop offset="0" stop-color="#FFF2D6"/><stop offset=".6" stop-color="#FFD7A1"/><stop offset="1" stop-color="#FFB86B"/>
          </radialGradient>
        </defs>
        <rect width="64" height="64" rx="15" fill="url(#mg)"/>
        <path d="M13 42H51" stroke="#fff" stroke-opacity=".14" stroke-width="1.2" stroke-linecap="round"/>
        <path d="M15 42C20 27 26 21 32 21C38 21 44 27 49 42" fill="none" stroke="url(#mt)" stroke-width="3.2" stroke-linecap="round"/>
        <circle cx="32" cy="25" r="4.2" fill="url(#ms)"/>
        <circle cx="15" cy="42" r="1.5" fill="#FFB86B"/><circle cx="49" cy="42" r="1.5" fill="#7AA2FF"/>
      </svg>
      <div><div class="name">Daythread</div><div class="sub">朝夕</div></div>
    </div>
    <nav id="nav">
      <button class="nav-item active" data-view="today">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19"/></svg>
        <span>Today</span>
      </button>
      <button class="nav-item" data-view="runs">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M4 6h16M4 12h16M4 18h10"/></svg>
        <span>Runs</span>
      </button>
      <button class="nav-item" data-view="memory">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>
        <span>Memory</span>
      </button>
      <button class="nav-item" data-view="connectors">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M8 12h8M12 8v8"/><rect x="3" y="6" width="6" height="12" rx="2"/><rect x="15" y="6" width="6" height="12" rx="2"/></svg>
        <span>Connectors</span>
      </button>
    </nav>
    <div class="sidebar-footer">
      <div class="status"><span class="dot"></span><span id="status">connecting</span></div>
      <button class="icon-btn" id="theme" title="Toggle theme">◐</button>
    </div>
  </aside>

  <main>
    <header class="topbar">
      <div>
        <h1 id="title">Today</h1>
        <p id="subtitle">A thread from dawn to dusk.</p>
      </div>
      <div class="actions">
        <button class="btn" id="refresh">Refresh</button>
      </div>
    </header>
    <div class="banner" id="banner"></div>

    <section class="view" id="view-today">
      <div class="hero">
        <div class="eyebrow" id="eyebrow">Morning brief</div>
        <h2 id="greeting">Good morning.</h2>
        <p id="herosub">Here is what changed, what matters, and what needs you.</p>
        <div class="chips" id="chips"></div>
      </div>
      <div class="grid">
        <article class="card"><h3>Calendar <span id="count-calendar"></span></h3><div class="list" id="calendar"></div></article>
        <article class="card"><h3>Tasks <span id="count-tasks"></span></h3><div class="list" id="tasks"></div></article>
        <article class="card"><h3>Commitments <span id="count-commitments"></span></h3><div class="list" id="commitments"></div></article>
        <article class="card wide"><h3>Needs attention <span id="count-candidates"></span></h3><div class="list" id="candidates"></div></article>
        <article class="card"><h3>Approvals <span id="count-approvals"></span></h3><div class="list" id="approvals"></div></article>
        <article class="card"><h3>Messages <span id="count-messages"></span></h3><div class="list" id="messages"></div></article>
        <article class="card wide"><h3>Connectors <span id="count-connectors"></span></h3><div class="list" id="connectors-mini"></div></article>
      </div>
    </section>

    <section class="view" id="view-runs" hidden>
      <div class="card full">
        <h3>Recent runs <span id="count-runs"></span></h3>
        <table class="table">
          <thead><tr><th>State</th><th>Tool</th><th>Intent</th><th>Risk</th><th></th></tr></thead>
          <tbody id="runs-body"></tbody>
        </table>
      </div>
    </section>

    <section class="view" id="view-memory" hidden>
      <div class="toolbar">
        <div class="search">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></svg>
          <input id="q" placeholder="Search people, projects, preferences…" autocomplete="off">
        </div>
        <button class="btn cool" id="search-btn">Search</button>
      </div>
      <div class="card full"><h3>Facts <span id="count-facts"></span></h3><div class="list" id="facts"></div></div>
    </section>

    <section class="view" id="view-connectors" hidden>
      <div class="card full"><h3>Connectors <span id="count-connectors-page"></span></h3><div class="list" id="connectors"></div></div>
    </section>
  </main>
</div>
<script>
const TOKEN = __TOKEN__;
const $ = (id) => document.getElementById(id);
const state = { today: null, runs: [], connectors: [], facts: [], view: 'today' };

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function fmtTime(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(undefined, {hour: '2-digit', minute: '2-digit'}).format(date);
}
function fmtDate(value) {
  const date = value ? new Date(value) : new Date();
  return new Intl.DateTimeFormat(undefined, {weekday: 'long', month: 'long', day: 'numeric'}).format(date);
}
function greeting() {
  const hour = new Date().getHours();
  if (hour < 5) return 'Still up?';
  if (hour < 12) return 'Good morning.';
  if (hour < 18) return 'Good afternoon.';
  return 'Good evening.';
}
function showError(message) {
  const banner = $('banner');
  banner.textContent = message;
  banner.classList.toggle('show', Boolean(message));
}
async function api(path, options = {}) {
  const headers = Object.assign({Authorization: 'Bearer ' + TOKEN}, options.headers || {});
  const response = await fetch(path, Object.assign({}, options, {headers}));
  if (!response.ok) throw new Error(path + ' → ' + response.status);
  return response.json();
}
function badge(stateName) {
  const cls = stateName === 'pending_approval' ? 'pending' : stateName;
  return `<span class="badge ${esc(cls)}"><span class="dot"></span>${esc(stateName.replace('_', ' '))}</span>`;
}
function empty(text) { return `<div class="empty">${esc(text)}</div>`; }
function row(title, meta, actions = '', mono = '') {
  return `<div class="row"><div><div class="title ${mono}">${title}</div>${meta ? `<div class="meta">${meta}</div>` : ''}</div>${actions ? `<div class="actions">${actions}</div>` : ''}</div>`;
}
function renderToday(today) {
  const events = today.calendar || [];
  const tasks = today.tasks || [];
  const commitments = today.commitments || [];
  const messages = today.messages || [];
  const candidates = today.candidates || [];
  const approvals = today.pending_approvals || [];
  $('eyebrow').textContent = today.kind === 'evening' ? 'Evening review' : 'Morning brief';
  $('greeting').textContent = greeting();
  $('herosub').textContent = fmtDate(today.date) + ' · what changed, what matters, and what needs you.';
  $('chips').innerHTML = [
    `<span class="chip warm"><span class="dot"></span><b>${events.length}</b> events</span>`,
    `<span class="chip"><span class="dot"></span><b>${tasks.length}</b> tasks</span>`,
    `<span class="chip warn"><span class="dot"></span><b>${approvals.length}</b> approvals</span>`,
    `<span class="chip"><span class="dot"></span><b>${candidates.length}</b> signals</span>`,
  ].join('');
  $('count-calendar').textContent = events.length || '';
  $('calendar').innerHTML = events.length ? events.map((event) => row(
    esc(event.title), `${fmtTime(event.start)}${event.end ? ' – ' + fmtTime(event.end) : ''}`
  )).join('') : empty('No events today.');
  $('count-tasks').textContent = tasks.length || '';
  $('tasks').innerHTML = tasks.length ? tasks.map((task) => row(
    esc(task.title), task.due ? `Due ${esc(task.due)}` : 'No due date',
    `<button class="btn small" onclick="taskDone('${esc(task.id)}')">Done</button>`
  )).join('') : empty('Nothing due.');
  $('count-commitments').textContent = commitments.length || '';
  $('commitments').innerHTML = commitments.length ? commitments.map((fact) => row(
    esc(fact.subject_id) + ' · ' + esc(fact.predicate), esc(typeof fact.object === 'string' ? fact.object : JSON.stringify(fact.object))
  )).join('') : empty('No open commitments.');
  $('count-messages').textContent = messages.length || '';
  $('messages').innerHTML = messages.length ? messages.map((message) => row(
    esc(message.subject || '(no subject)'), esc(message.sender || '')
  )).join('') : empty('Inbox is quiet.');
  $('count-candidates').textContent = candidates.length || '';
  $('candidates').innerHTML = candidates.length ? candidates.map((candidate) => row(
    esc(candidate.title), `${esc(candidate.kind)} · score ${Number(candidate.score).toFixed(2)}`,
    `<button class="btn small" onclick="feedback('${esc(candidate.id)}','accept')">Accept</button>
     <button class="btn small" onclick="feedback('${esc(candidate.id)}','ignore')">Ignore</button>`
  )).join('') : empty('Nothing needs attention.');
  $('count-approvals').textContent = approvals.length || '';
  $('approvals').innerHTML = approvals.length ? approvals.map((run) => row(
    esc(run.tool), esc(run.intent || ''),
    `<button class="btn small primary" onclick="act('${esc(run.id)}','approve')">Approve</button>
     <button class="btn small danger" onclick="act('${esc(run.id)}','reject')">Reject</button>`
  )).join('') : empty('Nothing waiting.');
  $('count-connectors').textContent = state.connectors.length || '';
  $('connectors-mini').innerHTML = state.connectors.length ? state.connectors.slice(0, 4).map((connector) => row(
    esc(connector.name), esc(connector.id) + ' · ' + esc(connector.status)
  )).join('') : empty('No connectors.');
}
function renderRuns(runs) {
  $('count-runs').textContent = runs.length || '';
  $('runs-body').innerHTML = runs.length ? runs.map((run) => {
    const actions = run.state === 'pending_approval'
      ? `<button class="btn small primary" onclick="act('${esc(run.id)}','approve')">Approve</button>
         <button class="btn small danger" onclick="act('${esc(run.id)}','reject')">Reject</button>`
      : (run.state === 'completed' ? `<button class="btn small" onclick="act('${esc(run.id)}','undo')">Undo</button>` : '');
    return `<tr><td>${badge(run.state)}</td><td class="mono">${esc(run.tool)}</td><td>${esc(run.intent)}</td><td>${esc(run.risk)}</td><td class="actions">${actions}</td></tr>`;
  }).join('') : `<tr><td colspan="5">${empty('No runs yet.')}</td></tr>`;
}
function renderConnectors(connectors) {
  $('count-connectors-page').textContent = connectors.length || '';
  $('connectors').innerHTML = connectors.length ? connectors.map((connector) => row(
    esc(connector.name), `${esc(connector.id)} · ${esc(connector.auth)} · ${esc(connector.status)}`,
    connector.status === 'connected' ? `<button class="btn small" onclick="syncConnector('${esc(connector.id)}')">Sync</button>` : ''
  )).join('') : empty('No connectors.');
}
function renderFacts(facts) {
  $('count-facts').textContent = facts.length || '';
  $('facts').innerHTML = facts.length ? facts.map((fact) => row(
    esc(fact.subject_id) + ' · ' + esc(fact.predicate),
    esc(typeof fact.object === 'string' ? fact.object : JSON.stringify(fact.object)) +
      (fact.tags && fact.tags.length ? ' · ' + esc(fact.tags.join(', ')) : ''),
    '', 'mono'
  )).join('') : empty('No matching facts.');
}
async function load() {
  $('status').textContent = 'syncing';
  showError('');
  try {
    const [today, runs, candidates, connectors] = await Promise.all([
      api('/v1/today'),
      api('/v1/runs?limit=30'),
      api('/v1/candidates?status=new'),
      api('/v1/connectors'),
    ]);
    today.candidates = candidates;
    state.today = today;
    state.runs = runs;
    state.connectors = connectors;
    renderToday(today);
    renderRuns(runs);
    renderConnectors(connectors);
    $('status').textContent = 'online';
  } catch (error) {
    showError(String(error));
    $('status').textContent = 'offline';
  }
}
async function act(id, action) {
  try { await api(`/v1/runs/${id}/${action}`, {method: 'POST'}); await load(); }
  catch (error) { showError(String(error)); }
}
async function taskDone(id) {
  try { await api('/v1/runs', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({steps: [{id: 's1', tool: 'task.done', args: {id}}]})}); await load(); }
  catch (error) { showError(String(error)); }
}
async function feedback(id, action) {
  try { await api('/v1/feedback', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({target: id, action})}); await load(); }
  catch (error) { showError(String(error)); }
}
async function syncConnector(id) {
  try { await api(`/v1/connectors/${id}/sync`, {method: 'POST'}); await load(); }
  catch (error) { showError(String(error)); }
}
async function search() {
  const query = $('q').value.trim();
  if (!query) return;
  try { state.facts = await api('/v1/facts?q=' + encodeURIComponent(query)); renderFacts(state.facts); }
  catch (error) { showError(String(error)); }
}
function setView(view) {
  state.view = view;
  for (const button of document.querySelectorAll('.nav-item')) button.classList.toggle('active', button.dataset.view === view);
  for (const section of ['today', 'runs', 'memory', 'connectors']) {
    $('view-' + section).hidden = section !== view;
  }
  const titles = {
    today: ['Today', 'A thread from dawn to dusk.'],
    runs: ['Runs', 'Every action, approval, and undo.'],
    memory: ['Memory', 'Facts you can read, edit, and forget.'],
    connectors: ['Connectors', 'The apps your day already runs on.'],
  };
  $('title').textContent = titles[view][0];
  $('subtitle').textContent = titles[view][1];
}
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem('daythread-theme', theme);
  $('theme').textContent = theme === 'dark' ? '◐' : '◑';
}
for (const button of document.querySelectorAll('.nav-item')) {
  button.addEventListener('click', () => setView(button.dataset.view));
}
$('refresh').addEventListener('click', load);
$('search-btn').addEventListener('click', search);
$('q').addEventListener('keydown', (event) => { if (event.key === 'Enter') search(); });
$('theme').addEventListener('click', () => {
  applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
});
applyTheme(localStorage.getItem('daythread-theme') || (window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'));
setView('today');
load();
setInterval(load, 30000);
</script>
</body>
</html>
"""


def html(token: str) -> str:
    return TEMPLATE.replace("__TOKEN__", json.dumps(token))
