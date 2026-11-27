"""A tiny loopback dashboard served by the API."""

from __future__ import annotations

import json


def html(token: str) -> str:
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Daythread</title>
<style>
:root {{ color-scheme: light dark; --bg:#0f1115; --panel:#171a21; --text:#e6e8ee; --muted:#9aa3b2; --accent:#7aa2ff; --danger:#ff7a90; }}
@media (prefers-color-scheme: light) {{ :root {{ --bg:#f6f7fb; --panel:#fff; --text:#1b1f2a; --muted:#5c667a; --accent:#335dff; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:var(--bg); color:var(--text); }}
header {{ padding:18px 24px; border-bottom:1px solid #2a2f3a; display:flex; gap:16px; align-items:center; }}
h1 {{ font-size:18px; margin:0; }}
button {{ background:var(--panel); color:var(--text); border:1px solid #343b49; border-radius:8px; padding:6px 10px; cursor:pointer; }}
button.primary {{ border-color:var(--accent); color:var(--accent); }}
button.danger {{ border-color:var(--danger); color:var(--danger); }}
main {{ display:grid; grid-template-columns: repeat(auto-fit,minmax(320px,1fr)); gap:16px; padding:20px 24px; }}
section {{ background:var(--panel); border:1px solid #262c37; border-radius:12px; padding:14px 16px; }}
h2 {{ font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:0 0 10px; }}
pre {{ white-space:pre-wrap; margin:0; font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace; }}
.item {{ border-top:1px solid #262c37; padding:8px 0; display:flex; gap:8px; justify-content:space-between; align-items:center; }}
.item:first-of-type {{ border-top:0; }}
.muted {{ color:var(--muted); }}
input {{ width:100%; padding:8px 10px; border-radius:8px; border:1px solid #343b49; background:transparent; color:var(--text); }}
.row {{ display:flex; gap:8px; margin-bottom:8px; }}
a {{ color:var(--accent); }}
</style>
</head>
<body>
<header>
  <h1>Daythread · 朝夕</h1>
  <span id="status" class="muted">loading…</span>
  <span style="flex:1"></span>
  <button onclick="load()">Refresh</button>
</header>
<main>
  <section><h2>Today</h2><pre id="today">—</pre></section>
  <section><h2>Approvals</h2><div id="approvals" class="muted">none</div></section>
  <section><h2>Runs</h2><div id="runs" class="muted">none</div></section>
  <section>
    <h2>Memory</h2>
    <div class="row"><input id="q" placeholder="search facts…" onkeydown="if(event.key==='Enter')search()"><button onclick="search()">Search</button></div>
    <div id="facts" class="muted">—</div>
  </section>
</main>
<script>
const TOKEN = {token};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
async function api(path, options={{}}) {{
  const headers = Object.assign({{"Authorization": "Bearer " + TOKEN}}, options.headers || {{}});
  const response = await fetch(path, Object.assign({{}}, options, {{headers}}));
  if (!response.ok) throw new Error(path + " → " + response.status);
  return response.json();
}}
function item(text, actions) {{
  return `<div class="item"><span>${{esc(text)}}</span><span>${{actions}}</span></div>`;
}}
async function load() {{
  try {{
    const [today, runs] = await Promise.all([api('/v1/today'), api('/v1/runs?limit=10')]);
    document.getElementById('today').textContent = JSON.stringify(today, null, 2);
    const approvals = (today.pending_approvals || []);
    document.getElementById('approvals').innerHTML = approvals.length
      ? approvals.map(r => item(r.tool + ' · ' + r.id.slice(0,8), `<button class="primary" onclick="act('${{r.id}}','approve')">Approve</button> <button class="danger" onclick="act('${{r.id}}','reject')">Reject</button>`)).join('')
      : '<span class="muted">Nothing waiting.</span>';
    document.getElementById('runs').innerHTML = runs.length
      ? runs.map(r => item(r.state.padEnd(16) + ' ' + r.tool, (r.state==='completed' ? `<button onclick="act('${{r.id}}','undo')">Undo</button>` : ''))).join('')
      : '<span class="muted">No runs.</span>';
    document.getElementById('status').textContent = 'online';
  }} catch (error) {{
    document.getElementById('status').textContent = error.message;
  }}
}}
async function act(id, action) {{
  await api('/v1/runs/' + id + '/' + action, {{method: 'POST'}});
  await load();
}}
async function search() {{
  const q = document.getElementById('q').value.trim();
  if (!q) return;
  const facts = await api('/v1/facts?q=' + encodeURIComponent(q));
  document.getElementById('facts').innerHTML = facts.length
    ? facts.map(f => item(f.subject_id + ' · ' + f.predicate + ' · ' + JSON.stringify(f.object), '')).join('')
    : '<span class="muted">No matches.</span>';
}}
load();
setInterval(load, 30000);
</script>
</body>
</html>
""".format(token=json.dumps(token))
