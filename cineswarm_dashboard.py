"""Self-contained CineSwarm dashboard document.

The control plane imports this module and assigns ``DASHBOARD_HTML`` at runtime so the
UI can evolve independently of HTTP and orchestration code.
"""

DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>CineSwarm Control Dashboard</title>
<style>
:root{color-scheme:dark;--bg:#090c10;--surface:#11161c;--surface-2:#171e26;--line:#293440;--text:#e8edf2;--muted:#98a6b5;--accent:#55d6a8;--accent-2:#72b7e8;--warn:#e0b45c;--danger:#e66b72;--ok:#63c98d;--radius:8px;--sidebar:230px;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-size:14px;line-height:1.45}button,input,select,textarea{font:inherit}button{border:1px solid transparent;border-radius:6px;background:var(--accent);color:#07110e;padding:.55rem .8rem;font-weight:700;cursor:pointer}button:hover{filter:brightness(1.08)}button:focus-visible,a:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible{outline:2px solid var(--accent-2);outline-offset:2px}.secondary{background:var(--surface-2);border-color:var(--line);color:var(--text)}.danger{background:transparent;border-color:var(--danger);color:#ff9ca2}.app{min-height:100vh;display:grid;grid-template-columns:var(--sidebar) 1fr}.sidebar{position:fixed;inset:0 auto 0 0;width:var(--sidebar);background:#0c1116;border-right:1px solid var(--line);padding:20px 14px;z-index:20;display:flex;flex-direction:column}.brand{padding:4px 10px 20px;border-bottom:1px solid var(--line)}.brand strong{display:block;letter-spacing:.16em;font-size:1.05rem}.brand span{color:var(--muted);font-size:.76rem}.nav{display:grid;gap:4px;margin-top:18px}.nav button{width:100%;text-align:left;background:transparent;color:var(--muted);font-weight:600;padding:.7rem .75rem}.nav button:hover,.nav button[aria-current="page"]{background:var(--surface-2);color:var(--text);filter:none}.nav button[aria-current="page"]{border-left:3px solid var(--accent)}.sidebar-foot{margin-top:auto;color:var(--muted);font-size:.75rem;padding:10px}.topbar{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:62px;padding:10px 24px;border-bottom:1px solid var(--line);background:rgba(9,12,16,.94);position:sticky;top:0;z-index:10}.topbar h1{font-size:1.05rem;margin:0}.top-actions{display:flex;align-items:center;gap:8px}.menu{display:none}.main{grid-column:2;min-width:0}.content{padding:24px;max-width:1600px;margin:auto}.view[hidden]{display:none}.view-head{display:flex;justify-content:space-between;align-items:start;gap:16px;margin-bottom:18px}.view-head h2{margin:0 0 4px;font-size:1.45rem}.view-head p{margin:0;color:var(--muted)}.health-banner{border:1px solid var(--line);border-left:4px solid var(--accent-2);background:var(--surface);padding:15px 18px;border-radius:var(--radius);display:flex;justify-content:space-between;gap:16px;margin-bottom:16px}.health-banner.danger-state{border-left-color:var(--danger)}.health-banner.warn-state{border-left-color:var(--warn)}.health-title{font-weight:800}.health-detail{color:var(--muted);margin-top:2px}.grid{display:grid;gap:14px}.metrics{grid-template-columns:repeat(6,minmax(130px,1fr));margin-bottom:16px}.two{grid-template-columns:repeat(2,minmax(0,1fr))}.three{grid-template-columns:repeat(3,minmax(0,1fr))}.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:16px;min-width:0}.card h3{font-size:.95rem;margin:0 0 12px}.metric-label,.eyebrow{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.08em}.metric-value{font-variant-numeric:tabular-nums;font-size:1.6rem;font-weight:750;margin-top:5px}.muted{color:var(--muted)}.status{display:inline-flex;align-items:center;gap:6px;border-radius:999px;border:1px solid var(--line);padding:.2rem .52rem;font-size:.72rem;text-transform:capitalize}.status.ok{color:var(--ok);border-color:#315e47}.status.warn{color:var(--warn);border-color:#66532d}.status.bad{color:var(--danger);border-color:#68373b}.service-list,.stack{display:grid;gap:9px}.service-row,.issue,.decision,.queue-row,.edition{background:var(--surface-2);border:1px solid var(--line);border-radius:6px;padding:11px}.service-row{display:flex;align-items:center;justify-content:space-between;gap:10px}.split{display:flex;align-items:center;justify-content:space-between;gap:12px}.actions,.form-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.form-row{margin-bottom:12px}.form-row input,.form-row select{min-width:150px;flex:1}.field{display:grid;gap:5px;color:var(--muted)}input,select,textarea{border:1px solid var(--line);border-radius:6px;background:#0b1015;color:var(--text);padding:.58rem .65rem}textarea{width:100%;min-height:110px;resize:vertical}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:6px}table{border-collapse:collapse;width:100%;font-size:.82rem}th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{position:sticky;top:0;background:#161d24;color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.05em}tr:last-child td{border-bottom:0}.state{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.76rem}.score-grid{display:grid;grid-template-columns:repeat(6,minmax(80px,1fr));gap:7px;margin:11px 0}.score{background:#0b1015;border:1px solid var(--line);padding:8px;border-radius:5px}.score b{display:block;font-size:1.05rem}.score span{display:block;color:var(--muted);font-size:.68rem}.reason-list{margin:8px 0;padding-left:18px;color:var(--muted);font-size:.8rem}.discovery-item{border:1px solid var(--line);background:var(--surface-2);border-radius:7px;padding:14px}.discovery-item h4{margin:0;font-size:1rem}.result{margin-top:10px;white-space:pre-wrap;overflow-wrap:anywhere;background:#090d11;border:1px solid var(--line);border-radius:6px;padding:11px;color:#cbd5df;max-height:320px;overflow:auto}.terminal{background:#070a0d;border:1px solid #2e3b47;border-radius:7px;min-height:300px;max-height:56vh;overflow:auto;padding:14px;font:13px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace}.terminal-entry{margin-bottom:12px;white-space:pre-wrap}.terminal-entry .prompt{color:var(--accent)}.empty,.loading,.error{padding:18px;text-align:center;border:1px dashed var(--line);border-radius:6px;color:var(--muted)}.error{color:var(--danger);border-color:#663238}.loading::after{content:"";display:inline-block;width:11px;height:11px;margin-left:8px;border:2px solid var(--line);border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite}.notice{min-height:22px;color:var(--muted);margin-top:8px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}@keyframes spin{to{transform:rotate(360deg)}}
@media(max-width:1150px){.metrics{grid-template-columns:repeat(3,1fr)}.three{grid-template-columns:1fr 1fr}.score-grid{grid-template-columns:repeat(3,1fr)}}
@media(max-width:760px){.app{display:block}.sidebar{transform:translateX(-100%);transition:transform .18s ease;box-shadow:10px 0 30px #000}.sidebar.open{transform:translateX(0)}.main{grid-column:auto}.menu{display:inline-flex}.content{padding:16px}.topbar{padding:10px 16px}.metrics,.two,.three{grid-template-columns:1fr 1fr}.view-head{display:block}.view-head .actions{margin-top:10px}.score-grid{grid-template-columns:repeat(2,1fr)}}
@media(max-width:480px){.metrics,.two,.three{grid-template-columns:1fr}.top-actions .secondary{display:none}.health-banner{display:block}.health-banner .status{margin-top:8px}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}}
</style>
</head>
<body>
<div class="app">
<aside class="sidebar" id="sidebar" aria-label="Primary navigation">
  <div class="brand"><strong>CINESWARM</strong><span>Media operations control plane</span></div>
  <nav class="nav">
    <button type="button" data-view="overview" aria-current="page">Overview</button>
    <button type="button" data-view="catalog">Catalog</button>
    <button type="button" data-view="operations">Operations</button>
    <button type="button" data-view="discovery">Discovery</button>
    <button type="button" data-view="curation">Curation</button>
    <button type="button" data-view="preservation">Preservation</button>
    <button type="button" data-view="analytics">Analytics</button>
    <button type="button" data-view="terminal">Terminal</button>
  </nav>

  <div class="sidebar-foot">Policy-controlled automation<br><span id="nav-freshness">Awaiting status</span></div>
</aside>
<main class="main">
<header class="topbar"><div class="top-actions"><button class="menu secondary" id="menu-toggle" type="button" aria-label="Open navigation" aria-expanded="false">Menu</button><h1 id="page-title">Overview</h1></div><div class="top-actions"><span class="status" id="top-health">Loading</span><button class="secondary" id="refresh-current" type="button">Refresh view</button></div></header>
<div class="content">
<section class="view" id="view-overview" data-title="Overview">
 <div class="view-head"><div><h2>System overview</h2><p>Health, capacity, queue pressure, and issues requiring operator attention.</p></div></div>
 <div id="health-banner" class="health-banner"><div><div class="health-title">Loading system state</div><div class="health-detail">Collecting control-plane telemetry.</div></div><span class="status">Loading</span></div>
 <div class="grid metrics" aria-label="Key system metrics">
  <article class="card"><div class="metric-label">Movies</div><div class="metric-value" id="metric-movies">--</div></article>
  <article class="card"><div class="metric-label">Series</div><div class="metric-value" id="metric-series">--</div></article>
  <article class="card"><div class="metric-label">Transcode Shield</div><div class="metric-value" id="metric-shield">--</div><div class="muted" id="metric-shield-sub">iGPU Protection</div></article>
  <article class="card"><div class="metric-label">Queue backlog</div><div class="metric-value" id="metric-queue">--</div></article>
  <article class="card"><div class="metric-label">Actionable issues</div><div class="metric-value" id="metric-issues">--</div></article>
  <article class="card"><div class="metric-label">Free storage</div><div class="metric-value" id="metric-storage">--</div></article>
  <article class="card"><div class="metric-label">Worker freshness</div><div class="metric-value" id="metric-worker">--</div></article>
 </div>
 <div class="grid two"><article class="card"><h3>Services</h3><div id="overview-services" class="service-list loading">Loading services</div></article><article class="card"><h3>Top actionable issues</h3><div id="overview-issues" class="stack loading">Loading issues</div></article></div>
 <article class="card" style="margin-top:14px"><div class="split"><h3>Recent decisions</h3><button class="secondary" type="button" data-view-jump="operations">Open operations</button></div><div id="overview-decisions" class="stack loading">Loading decisions</div></article>
</section>
<section class="view" id="view-catalog" data-title="Catalog" hidden>
 <div class="view-head"><div><h2>Vault catalog browser</h2><p>Search, filter, and browse all 10,700+ movies and series in your local vault.</p></div></div>
 <article class="card">
  <form id="catalog-form" class="form-row">
   <label class="field">Search query<input name="q" placeholder="Title or plot overview..."></label>
   <label class="field">Genre
    <select name="genre">
     <option value="">All Genres</option>
     <option value="Action">Action</option><option value="Adventure">Adventure</option><option value="Animation">Animation</option><option value="Comedy">Comedy</option><option value="Crime">Crime</option><option value="Drama">Drama</option><option value="Fantasy">Fantasy</option><option value="Horror">Horror</option><option value="Mystery">Mystery</option><option value="Romance">Romance</option><option value="Science Fiction">Sci-Fi</option><option value="Thriller">Thriller</option>
    </select>
   </label>
   <label class="field">Media type
    <select name="media_type"><option value="">All Media</option><option value="movie">Movies</option><option value="series">TV Series</option></select>
   </label>
   <button type="submit">Search catalog</button>
  </form>
  <div class="split" style="margin-bottom:12px">
   <div id="catalog-count" class="muted">Loading catalog...</div>
   <div class="actions">
    <button class="secondary" id="cat-prev" type="button">Prev</button>
    <span id="cat-page-label" class="muted" style="align-self:center">Page 1</span>
    <button class="secondary" id="cat-next" type="button">Next</button>
   </div>
  </div>
  <div id="catalog-table" class="loading">Loading catalog items</div>
 </article>
</section>
<section class="view" id="view-operations" data-title="Operations" hidden>
 <div class="view-head"><div><h2>Operations</h2><p>Downloads, pending control tasks, retries, blocks, and autonomy controls.</p></div><div class="actions"><button type="button" id="op-refresh-services">Refresh services</button><button class="secondary" type="button" id="op-reconcile">Reconcile</button><button class="secondary" type="button" id="op-plex-refresh">Request Plex refresh</button></div></div>
 <div class="grid three"><article class="card"><h3>Autonomy state</h3><div id="autonomy-state" class="loading">Loading autonomy</div></article><article class="card"><h3>Emergency stop</h3><p class="muted">Immediately blocks automatic and approved write actions.</p><button class="danger" id="emergency-stop" type="button" aria-pressed="false">Loading state</button><div id="emergency-notice" class="notice" role="status"></div></article><article class="card"><h3>Queue pressure</h3><div id="operations-pressure" class="loading">Loading queue totals</div></article></div>
 <article class="card" style="margin-top:14px"><h3>Active queue and downloads</h3><div id="downloads-table" class="loading">Loading downloads</div></article>
 <article class="card"><h3>Pending and recent tasks</h3><p class="muted">Approval requirements, blocked states, retries, and failures remain visible.</p><div id="tasks-table" class="loading">Loading tasks</div></article>
 <article class="card"><h3>Acquisition observations and worker retries</h3><div id="retry-table" class="loading">Loading retry states</div></article>
 <div class="grid two"><article class="card"><h3>Targeted acquisition planner</h3><form id="acquisition-form"><div class="form-row"><label class="field">Media type<select name="media_type"><option value="movie">Movie</option><option value="series">Series</option></select></label><label class="field">Title or search term<input name="term" required placeholder="Exact title or query"></label><button type="submit">Build plan</button></div></form><div id="acquisition-result" class="notice" role="status"></div></article><article class="card"><h3>Analysis and repair actions</h3><p class="muted">These operations return explicit results; repair tasks remain policy-controlled.</p><div class="actions"><button class="secondary" id="quality-movies" type="button">Analyze movie quality</button><button class="secondary" id="quality-series" type="button">Analyze series quality</button><button class="secondary" id="health-scan" type="button">Scan media health</button></div><div id="analysis-result" class="notice" role="status"></div></article></div>
 <div id="operations-notice" class="notice" role="status"></div>
</section>
<section class="view" id="view-discovery" data-title="Discovery" hidden>
 <div class="view-head"><div><h2>Discovery</h2><p>Transparent component scoring for candidates awaiting review.</p></div><div class="actions"><button id="discovery-generate" type="button">Generate candidates</button><button id="discovery-franchise" class="secondary" type="button">Find franchise gaps</button></div></div>
 <div id="discovery-notice" class="notice" role="status"></div><div id="discovery-list" class="stack loading">Loading candidates</div>
</section>
<section class="view" id="view-curation" data-title="Curation" hidden>
 <div class="view-head"><div><h2>Curation & Intelligent Neural Features</h2><p>Semantic vibe queries, dense vector search, double-feature cinema nights, and playlist management.</p></div></div>
 
 <div class="grid two">
  <article class="card">
   <h3>🧠 Gemini Neural Vibe Search</h3>
   <p class="muted">Search 10,700+ titles by atmosphere, mood, visual aesthetic, or plot tropes.</p>
   <form id="vibe-search-form">
    <div class="form-row" style="align-items:flex-end">
     <label class="field" style="flex:1">Prompt or Aesthetic<input name="prompt" required placeholder="e.g. Gritty 70s neo-noir set in rainy NYC..."></label>
     <button type="submit">🔮 Vibe Search</button>
    </div>
   </form>
   <div id="vibe-search-result" class="notice" role="status"></div>
  </article>

  <article class="card">
   <h3>🧬 SOTA 100% Local Dense Vector Search</h3>
   <p class="muted">384-dimensional dense semantic feature search ($0 cost, 100% local).</p>
   <form id="intel-vector-search-form">
    <div class="form-row" style="align-items:flex-end">
     <label class="field" style="flex:1">Concept / Plot / Aesthetic<input name="query" required placeholder="e.g. 90s claustrophobic submarine thriller..."></label>
     <button type="submit">⚡ Vector Search</button>
    </div>
   </form>
   <div id="vector-search-result" class="notice" role="status"></div>
  </article>
 </div>

 <div class="grid two" style="margin-top:14px">
  <article class="card">
   <h3>🍿 Cinema Night & Double Feature Generator</h3>
   <p class="muted">Generate thematic double features with custom intermission trivia & fun facts.</p>
   <form id="cinema-night-form">
    <div class="form-row" style="align-items:flex-end">
     <label class="field" style="flex:1">Theme or Franchise<input name="theme" required placeholder="e.g. Cyberpunk Noir, Mind-Bending Sci-Fi..."></label>
     <button type="submit">🎬 Generate Cinema Night</button>
    </div>
   </form>
   <div id="cinema-night-result" class="notice" role="status"></div>
  </article>

  <article class="card">
   <h3>🎯 Watch Tonight Concierge</h3>
   <p class="muted">Get instant curated recommendations matched to your available time & genre interest.</p>
   <form id="watch-form">
    <div class="form-row" style="align-items:flex-end">
     <label class="field" style="width:100px">Max Time<input name="max_minutes" type="number" min="10" value="120" placeholder="Mins"></label>
     <label class="field" style="flex:1">Genre<input name="genre" placeholder="Optional genre"></label>
     <button type="submit">Recommend Titles</button>
    </div>
   </form>
   <div id="watch-result" class="notice" role="status"></div>
  </article>
 </div>

 <div class="grid two" style="margin-top:14px">
  <article class="card">
   <h3>🎨 Collection & Playlist Curator</h3>
   <p class="muted">Automatically group vault items into a Plex Collection or Playlist.</p>
   <form id="curation-form">
    <div class="form-row" style="align-items:flex-end">
     <label class="field" style="flex:2">Theme / Mood<input name="theme" required placeholder="e.g. Restored 80s Cyberpunk"></label>
     <label class="field" style="flex:1"><select name="mode"><option value="collection">Collection</option><option value="playlist">Playlist</option></select></label>
     <label class="field" style="width:70px"><input name="limit" type="number" min="1" max="50" value="20"></label>
     <button type="submit">Create Curation</button>
    </div>
   </form>
   <div id="curation-result" class="notice" role="status"></div>
  </article>

  <article class="card">
   <h3>🐝 Multi-Agent Consensus Deliberation</h3>
   <p class="muted">Test candidate releases against Sentinel, Marshal, Projectionist & Librarian nodes.</p>
   <form id="intel-consensus-form">
    <div class="form-row" style="align-items:flex-end">
     <label class="field" style="flex:2">Candidate Title<input name="title" value="Sample Release (2025)" placeholder="Movie Title"></label>
     <label class="field" style="width:90px">Size (GB)<input name="size_gb" type="number" step="0.1" value="12.5"></label>
     <button type="submit">Run Consensus Audit</button>
    </div>
   </form>
   <div id="consensus-result" class="notice" role="status"></div>
  </article>
 </div>

 <article class="card" style="margin-top:14px">
  <div class="split">
   <h3>🧠 Swarm Intelligence Center & Vault Health</h3>
   <button class="secondary" id="intel-status-refresh" type="button">Refresh Status</button>
  </div>
  <div class="grid two" style="margin-top:12px">
   <div>
    <h4>Quality-Guard & Reinforcement Multipliers</h4>
    <div id="intel-status-display" class="loading">Loading Swarm Intelligence status</div>
   </div>
   <div>
    <h4>Vault Storage Audit & Tools</h4>
    <div class="stack">
     <button type="button" id="intel-run-storage-audit">Run Vault Storage Optimization Audit</button>
     <form id="intel-double-feature-form">
      <div class="form-row" style="align-items:flex-end">
       <label class="field" style="flex:1">Double Feature Theme<input name="theme" value="mind-bending twists" placeholder="Theme or mood"></label>
       <button type="submit">Quick Double Feature</button>
      </div>
     </form>
    </div>
   </div>
  </div>
  <div id="intel-result" class="notice" style="margin-top:10px" role="status"></div>
 </article>

 <article class="card" style="margin-top:14px">
  <div class="split">
   <h3>🎬 Active Plex Playlists</h3>
   <button class="secondary" id="playlists-refresh" type="button">Refresh Playlists</button>
  </div>
  <div id="playlists-list" class="loading">Loading playlists</div>
 </article>
</section>


<section class="view" id="view-preservation" data-title="Preservation" hidden>
 <div class="view-head"><div><h2>Preservation</h2><p>Bounded evidence from storage scans, checksums, mounts, maintenance, and provenance.</p></div></div>
 <div class="grid metrics"><article class="card"><div class="metric-label">Latest scan</div><div class="metric-value" id="pres-scan">--</div></article><article class="card"><div class="metric-label">Mounts available</div><div class="metric-value" id="pres-mounts">--</div></article><article class="card"><div class="metric-label">Checksum mismatch</div><div class="metric-value" id="pres-checksums">--</div></article><article class="card"><div class="metric-label">Kernel events</div><div class="metric-value" id="pres-events">--</div></article><article class="card"><div class="metric-label">DB integrity</div><div class="metric-value" id="pres-integrity">--</div></article><article class="card"><div class="metric-label">Backup status</div><div class="metric-value" id="pres-backup">--</div></article></div>
 <div class="grid two"><article class="card"><h3>Mount availability</h3><div id="pres-mount-table" class="loading">Loading mounts</div></article><article class="card"><h3>Latest scan and database maintenance</h3><div id="pres-maintenance" class="loading">Loading maintenance</div></article></div>
 <article class="card"><h3>Changed, missing, or checksum-affected files</h3><div id="pres-files" class="loading">Loading file states</div></article>
 <article class="card"><h3>Editions and provenance</h3><div id="pres-editions" class="loading">Loading editions</div></article>
</section>
<section class="view" id="view-analytics" data-title="Analytics" hidden>
 <div class="view-head"><div><h2>Analytics</h2><p>Collection composition, storage footprint, and filmography gap analysis.</p></div></div>
 <div class="grid three"><article class="card"><h3>Storage footprint</h3><div class="metric-value" id="analytics-size">--</div></article><article class="card"><h3>Video codecs</h3><div id="analytics-codecs" class="loading">Loading codecs</div></article><article class="card"><h3>Top genres</h3><div id="analytics-genres" class="loading">Loading genres</div></article></div>
 <article class="card" style="margin-top:14px"><h3>Filmography gap scan</h3><form id="filmography-form" class="form-row"><label class="field">Person<input name="person" required placeholder="Director or performer"></label><label class="field">Role<select name="role"><option value="director">Director</option><option value="actor">Actor</option></select></label><button type="submit">Scan filmography</button></form><div id="filmography-result" class="notice" role="status"></div></article>
</section>
<section class="view" id="view-terminal" data-title="Terminal" hidden>
 <div class="view-head"><div><h2>Terminal</h2><p>Natural-language CineSwarm agent workflow with explicit server responses.</p></div></div>
 <div id="terminal-output" class="terminal" role="log" aria-live="polite"><div class="terminal-entry muted">Control terminal ready. Write actions remain policy-controlled.</div></div>
 <form id="terminal-form" style="margin-top:10px"><label class="sr-only" for="terminal-input">Command</label><div class="form-row"><input id="terminal-input" name="prompt" autocomplete="off" required placeholder="Ask CineSwarm about the collection or propose an action"><button type="submit">Send</button></div></form>
</section>
</div></main></div>
<script>
'use strict';
const state={status:null,summary:null,operations:null,autonomy:null,preservation:null,acquisitionPlan:null};
function escapeHtml(value){return String(value??'').replace(/[&<>'"]/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));}
function fmtNumber(value){const number=Number(value);return Number.isFinite(number)?new Intl.NumberFormat().format(number):'--';}
function fmtBytes(value){let number=Number(value);if(!Number.isFinite(number)||number<0)return '--';const units=['B','KB','MB','GB','TB','PB'];let index=0;while(number>=1024&&index<units.length-1){number/=1024;index++;}return `${number>=10||index===0?number.toFixed(0):number.toFixed(1)} ${units[index]}`;}
function relativeTime(value){if(value===null||value===undefined||value==='')return 'Unknown';let stamp=typeof value==='number'?value*1000:Date.parse(value);if(!Number.isFinite(stamp))return 'Unknown';let seconds=Math.round((stamp-Date.now())/1000);const units=[['year',31536000],['month',2592000],['day',86400],['hour',3600],['minute',60],['second',1]];for(const [name,size] of units){if(Math.abs(seconds)>=size||size===1)return new Intl.RelativeTimeFormat(undefined,{numeric:'auto'}).format(Math.round(seconds/size),name);}return 'just now';}
function statusClass(value){const text=String(value||'unknown').toLowerCase();if(['healthy','ok','completed','complete','success','running','imported','available','unchanged'].includes(text))return'ok';if(text.includes('fail')||text.includes('error')||text.includes('missing')||text.includes('mismatch')||text==='unhealthy'||text==='stopped')return'bad';return'warn';}
function badge(value){return `<span class="status ${statusClass(value)}">${escapeHtml(value||'unknown')}</span>`;}
function empty(text){return `<div class="empty">${escapeHtml(text)}</div>`;}
function errorState(error){return `<div class="error" role="alert">${escapeHtml(error.message||error)}</div>`;}
async function apiFetch(url,options={}){const config={...options,headers:{Accept:'application/json',...(options.body?{'Content-Type':'application/json'}:{}),...(options.headers||{})}};let response;try{response=await fetch(url,config);}catch(error){throw new Error(`Network error while requesting ${url}: ${error.message}`);}const type=response.headers.get('content-type')||'';let payload=null;try{payload=type.includes('json')?await response.json():await response.text();}catch(error){throw new Error(`Invalid response from ${url}`);}if(!response.ok){const detail=payload&&typeof payload==='object'?(payload.error||payload.message):payload;throw new Error(detail||`Request failed with status ${response.status}`);}return payload;}
function table(headers,rows){if(!rows.length)return empty('No records available.');return `<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th scope="col">${escapeHtml(h)}</th>`).join('')}</tr></thead><tbody>${rows.join('')}</tbody></table></div>`;}
function setView(name){document.querySelectorAll('.view').forEach(view=>view.hidden=view.id!==`view-${name}`);document.querySelectorAll('[data-view]').forEach(button=>button.setAttribute('aria-current',button.dataset.view===name?'page':'false'));const view=document.getElementById(`view-${name}`);document.getElementById('page-title').textContent=view?.dataset.title||'CineSwarm';location.hash=name==='overview'?'':name;closeMenu();loadView(name);}
function closeMenu(){const side=document.getElementById('sidebar'),toggle=document.getElementById('menu-toggle');side.classList.remove('open');toggle.setAttribute('aria-expanded','false');}
function renderOverview(){const status=state.status||{},summary=state.summary||{},catalog=status.catalog||{},worker=status.worker||{};const services=status.services||[],issues=summary.actionable_issues||[];const unhealthy=services.filter(s=>s.status!=='healthy');const healthy=services.length===3&&!unhealthy.length&&worker.healthy;const banner=document.getElementById('health-banner');banner.className=`health-banner ${healthy?'':unhealthy.length?'danger-state':'warn-state'}`;banner.innerHTML=`<div><div class="health-title">${healthy?'Systems operational':'Operator attention recommended'}</div><div class="health-detail">${escapeHtml(unhealthy.length?`${unhealthy.length} service(s) unhealthy`:worker.healthy?'All monitored services and worker are current':'Worker heartbeat is unavailable or stale')}</div></div>${badge(healthy?'healthy':'attention')}`;document.getElementById('top-health').outerHTML=`<span class="status ${healthy?'ok':'warn'}" id="top-health">${healthy?'Healthy':'Attention'}</span>`;document.getElementById('metric-movies').textContent=fmtNumber(catalog.movie||0);document.getElementById('metric-series').textContent=fmtNumber(catalog.series||0);const shield=state.shield||{};document.getElementById('metric-shield').textContent=shield.compliance_percentage!==undefined?`${shield.compliance_percentage}%`:'--';document.getElementById('metric-shield-sub').textContent=shield.non_compliant_count?`${shield.non_compliant_count} AV1 remaining`:'100% iGPU DirectPlay';document.getElementById('metric-queue').textContent=fmtNumber(Object.values((state.operations||{}).downloads||{}).reduce((total,queue)=>total+Number(queue.total_records||0),0));
document.getElementById('metric-issues').textContent=fmtNumber(issues.length);document.getElementById('metric-storage').textContent=fmtBytes((summary.storage||{}).free_bytes);document.getElementById('metric-worker').textContent=worker.updated_at?relativeTime(worker.updated_at):worker.age_seconds!==undefined?relativeTime(Date.now()/1000-worker.age_seconds):'Unknown';document.getElementById('nav-freshness').textContent=`Worker: ${worker.healthy?'current':'stale or unknown'}`;document.getElementById('overview-services').className='service-list';document.getElementById('overview-services').innerHTML=services.length?services.map(s=>`<div class="service-row"><div><strong>${escapeHtml(s.service)}</strong><div class="muted">Checked ${escapeHtml(relativeTime(s.fetched_at))}</div></div>${badge(s.status)}</div>`).join(''):empty('No service snapshots yet.');document.getElementById('overview-issues').className='stack';document.getElementById('overview-issues').innerHTML=issues.length?issues.slice(0,8).map(issue=>`<div class="issue"><div class="split"><strong>${escapeHtml(String(issue.type||'issue').replaceAll('_',' '))}</strong>${badge(issue.status||issue.state||'attention')}</div><div class="muted">${escapeHtml(issue.action||issue.task_type||issue.path||issue.error||'Review operational details.')}</div></div>`).join(''):empty('No actionable issues in this window.');}
async function loadOverview(force=false){const targets=['overview-services','overview-issues','overview-decisions'];try{if(force||!state.status)[state.status,state.summary,state.operations,state.shield]=await Promise.all([apiFetch('/api/status'),apiFetch('/api/operational-summary?hours=24'),apiFetch('/api/operations/queue?limit=20'),apiFetch('/api/transcode-shield')]);renderOverview();const result=await apiFetch('/api/decisions');const decisions=result.decisions||[];const el=document.getElementById('overview-decisions');el.className='stack';el.innerHTML=decisions.length?decisions.slice(0,8).map(d=>`<div class="decision"><div class="split"><strong>${escapeHtml(d.subject||d.category||'Decision')}</strong>${badge(d.decision)}</div><div class="muted">${escapeHtml(d.category||'action')} · ${escapeHtml(relativeTime(d.created_at))}</div><div>${escapeHtml(typeof d.reasons==='string'?d.reasons:JSON.stringify(d.reasons||{}))}</div><div class="actions"><button class="secondary" type="button" data-decision-feedback="good" data-decision-id="${escapeHtml(d.decision_id)}">Mark useful</button><button class="secondary" type="button" data-decision-feedback="bad" data-decision-id="${escapeHtml(d.decision_id)}">Mark unhelpful</button></div></div>`).join(''):empty('No decisions have been recorded.');}catch(error){targets.forEach(id=>{document.getElementById(id).className='';document.getElementById(id).innerHTML=errorState(error);});}}
function queueRecords(data){const records=[];for(const [kind,queue] of Object.entries((data||{}).downloads||{})){for(const item of queue.records||[])records.push({...item,_kind:kind});}return records;}
function renderOperations(){const data=state.operations||{},records=queueRecords(data),tasks=data.tasks||[],observations=data.observations||[],jobs=data.worker_jobs||[];document.getElementById('operations-pressure').className='';document.getElementById('operations-pressure').innerHTML=`<div class="metric-value">${fmtNumber(records.length+jobs.filter(j=>['queued','running','retry'].includes(j.status)).length)}</div><div class="muted">${fmtNumber(records.length)} service downloads · ${fmtNumber(jobs.length)} worker jobs</div>${Object.keys(data.errors||{}).length?`<div class="error">${escapeHtml(Object.entries(data.errors).map(([k,v])=>`${k}: ${v}`).join('; '))}</div>`:''}`;document.getElementById('downloads-table').className='';document.getElementById('downloads-table').innerHTML=table(['Type','Title','Status','Progress','Remaining'],records.map(r=>{const size=Number(r.size||0),left=Number(r.sizeleft!==undefined?r.sizeleft:r.sizeLeft!==undefined?r.sizeLeft:0),st=String(r.status||r.trackedDownloadState||'').toLowerCase();const isCompleted=st==='completed'||left===0;const progress=isCompleted?100:(size>0?Math.max(0,Math.min(100,(size-left)/size*100)):Number(r.progress||0));return `<tr><td>${escapeHtml(r._kind)}</td><td>${escapeHtml(r.title||r.seriesTitle||'Untitled')}</td><td>${badge(r.status||r.trackedDownloadState)}</td><td>${escapeHtml(Number.isFinite(progress)?`${progress.toFixed(0)}%`:'--')}</td><td>${escapeHtml(isCompleted?'Import pending':(r.timeleft||r.estimatedCompletionTime||'--'))}</td></tr>`;}));document.getElementById('tasks-table').className='';document.getElementById('tasks-table').innerHTML=table(['Task','State','Approval','Requested','Updated','Action'],tasks.map(t=>`<tr><td><strong>${escapeHtml(t.task_type)}</strong><div class="state">${escapeHtml(t.task_id)}</div></td><td>${badge(t.status)}</td><td>${escapeHtml(t.requires_approval?'Required':'No')}</td><td>${escapeHtml(t.requested_by)}</td><td>${escapeHtml(relativeTime(t.updated_at))}</td><td>${t.status==='pending_approval'?`<button type="button" data-approve-task="${escapeHtml(t.task_id)}">Approve</button>`:'--'}</td></tr>`));document.getElementById('retry-table').className='';const rows=[...observations.map(o=>`<tr><td>Acquisition</td><td class="state">${escapeHtml(o.task_id)}</td><td>${badge(o.state)}</td><td>${escapeHtml(relativeTime(o.checked_at))}</td><td>${escapeHtml((o.details||{}).error||'--')}</td></tr>`),...jobs.map(j=>`<tr><td>${escapeHtml(j.job_type||'Worker job')}</td><td class="state">${escapeHtml(j.id||'--')}</td><td>${badge(j.status)}</td><td>${escapeHtml(relativeTime(j.updated_at))}</td><td>${escapeHtml(j.last_error||`Attempt ${j.attempts||0}`)}</td></tr>`)];document.getElementById('retry-table').innerHTML=table(['Source','Identifier','State','Updated','Detail'],rows);}

function renderAutonomy(){const auto=state.autonomy||{},policies=auto.policies||{},stop=Boolean(auto.emergency_stop),el=document.getElementById('autonomy-state');el.className='';el.innerHTML=`${badge(stop?'stopped':'enabled')}<div class="muted" style="margin-top:8px">Full autopilot: ${escapeHtml(policies.CINESWARM_FULL_AUTOPILOT||'false')}<br>Budget mode: ${escapeHtml(policies.CINESWARM_AUTO_BUDGET_MODE||'default')}<br>Movies this week: ${fmtNumber((auto.budget||{}).movies_added||0)}<br>Series this week: ${fmtNumber((auto.budget||{}).series_added||0)}</div>`;const button=document.getElementById('emergency-stop');button.dataset.enabled=String(stop);button.setAttribute('aria-pressed',String(stop));button.textContent=stop?'Resume automatic actions':'Activate emergency stop';}
async function loadOperations(force=false){try{if(force||!state.operations)[state.operations,state.autonomy,state.summary]=await Promise.all([apiFetch('/api/operations/queue?limit=200'),apiFetch('/api/autonomous/status'),apiFetch('/api/operational-summary?hours=24')]);renderOperations();renderAutonomy();}catch(error){['operations-pressure','downloads-table','tasks-table','retry-table','autonomy-state'].forEach(id=>{document.getElementById(id).className='';document.getElementById(id).innerHTML=errorState(error);});}}
function score(candidate,key){const value=Number(candidate[key]);return Number.isFinite(value)?value.toFixed(0):'--';}
async function loadDiscovery(){const el=document.getElementById('discovery-list');el.className='stack loading';el.textContent='Loading candidates';try{const data=await apiFetch('/api/discovery'),items=data.candidates||[];el.className='stack';el.innerHTML=items.length?items.map(c=>{const reasons=c.score_reasons||[];return `<article class="discovery-item"><div class="split"><div><h4>${escapeHtml(c.title||'Untitled')} ${c.year?`<span class="muted">(${escapeHtml(c.year)})</span>`:''}</h4><div class="muted">${escapeHtml(c.media_type||'media')} · ${escapeHtml(c.status||'new')}</div></div>${badge(`overall ${score(c,'overall_score')}`)}</div><div class="score-grid"><div class="score"><b>${score(c,'watch_affinity_score')}</b><span>Watch affinity</span></div><div class="score"><b>${score(c,'collection_significance_score')}</b><span>Significance</span></div><div class="score"><b>${score(c,'rarity_preservation_score')}</b><span>Rarity</span></div><div class="score"><b>${score(c,'storage_cost_score')}</b><span>Storage efficiency</span></div><div class="score"><b>${score(c,'acquisition_confidence_score')}</b><span>Confidence</span></div><div class="score"><b>${score(c,'overall_score')}</b><span>Overall</span></div></div><p>${escapeHtml(c.rationale||c.reason||'No rationale supplied.')}</p>${reasons.length?`<ul class="reason-list">${reasons.map(r=>`<li><strong>${escapeHtml(String(r.component||'score').replaceAll('_',' '))}:</strong> ${escapeHtml(r.detail||'No explanation')}</li>`).join('')}</ul>`:''}<div class="actions"><button type="button" data-discovery-action="approve" data-candidate-id="${escapeHtml(c.id)}">Approve</button><button class="danger" type="button" data-discovery-action="reject" data-candidate-id="${escapeHtml(c.id)}">Reject</button></div></article>`;}).join(''):empty('No discovery candidates are awaiting review.');}catch(error){el.className='stack';el.innerHTML=errorState(error);}}
async function loadPlaylists(){const el=document.getElementById('playlists-list');el.className='loading';el.textContent='Loading playlists';try{const data=await apiFetch('/api/playlists'),items=data.playlists||[];el.className='';el.innerHTML=table(['Playlist','Items','Action'],items.map(p=>`<tr><td>${escapeHtml(p.title||p.name||'Untitled')}</td><td>${fmtNumber(p.leafCount||p.item_count||0)}</td><td>${p.ratingKey?`<button class="danger" type="button" data-delete-playlist="${escapeHtml(p.ratingKey)}">Delete</button>`:'--'}</td></tr>`));}catch(error){el.className='';el.innerHTML=errorState(error);}}
function renderPreservation(){const data=state.preservation||{},scan=data.latest_scan||{},summary=scan.summary||{},mounts=data.mounts||[],files=data.files||[],events=data.storage_events||[],integrity=data.database_integrity||{},maintenance=data.maintenance||{};document.getElementById('pres-scan').textContent=scan.finished_at?relativeTime(scan.finished_at):scan.status||'None';document.getElementById('pres-mounts').textContent=`${mounts.filter(m=>m.mounted).length}/${mounts.length}`;document.getElementById('pres-checksums').textContent=fmtNumber((summary.statuses||{}).checksum_mismatch||files.filter(f=>f.status==='checksum_mismatch').length);document.getElementById('pres-events').textContent=fmtNumber(summary.storage_events??events.length);const dbOk=['control','catalog'].every(k=>(integrity[k]||{}).integrity==='ok');document.getElementById('pres-integrity').textContent=dbOk?'OK':'Attention';document.getElementById('pres-backup').textContent=maintenance.status||'Unknown';document.getElementById('pres-mount-table').className='';document.getElementById('pres-mount-table').innerHTML=table(['Mount','State','Filesystem','Free','Error'],mounts.map(m=>`<tr><td>${escapeHtml(m.mount_path)}</td><td>${badge(m.mounted?'available':'missing')}</td><td>${escapeHtml(m.filesystem||'--')}</td><td>${fmtBytes(m.free_bytes)}</td><td>${escapeHtml(m.error||'--')}</td></tr>`));document.getElementById('pres-maintenance').className='';document.getElementById('pres-maintenance').innerHTML=`<div class="stack"><div class="service-row"><span>Preservation scan</span>${badge(scan.status||'unavailable')}</div><div class="service-row"><span>Database maintenance</span>${badge(maintenance.status||'unavailable')}</div><div class="service-row"><span>Control database</span>${badge((integrity.control||{}).integrity)}</div><div class="service-row"><span>Catalog database</span>${badge((integrity.catalog||{}).integrity)}</div><div class="muted">Scan finished ${escapeHtml(relativeTime(scan.finished_at))}. Maintenance finished ${escapeHtml(relativeTime(maintenance.finished_at))}.</div></div>`;document.getElementById('pres-files').className='';document.getElementById('pres-files').innerHTML=table(['Path','State','Size','Checksum','Error'],files.map(f=>`<tr><td>${escapeHtml(f.logical_path)}</td><td>${badge(f.status)}</td><td>${fmtBytes(f.size)}</td><td class="state">${escapeHtml(f.checksum_sha256?`${f.checksum_sha256.slice(0,14)}...`:'Not sampled')}</td><td>${escapeHtml(f.error||'--')}</td></tr>`));const editions=data.editions||[];document.getElementById('pres-editions').className='';document.getElementById('pres-editions').innerHTML=table(['Edition','Source provenance','Authenticity','Rarity','Backup','Updated'],editions.map(e=>`<tr><td>${escapeHtml(e.edition_label)}</td><td>${escapeHtml(e.source_release_name||'Not recorded')}</td><td>${escapeHtml(`${Number(e.authenticity_confidence||0).toFixed(0)}%`)}</td><td>${escapeHtml(e.rarity_flag?'Rare':'Standard')}</td><td>${badge(e.backup_status)}</td><td>${escapeHtml(relativeTime(e.updated_at))}</td></tr>`));}
async function loadPreservation(force=false){try{if(force||!state.preservation)state.preservation=await apiFetch('/api/preservation?limit=100');renderPreservation();}catch(error){['pres-mount-table','pres-maintenance','pres-files','pres-editions'].forEach(id=>{document.getElementById(id).className='';document.getElementById(id).innerHTML=errorState(error);});}}
async function loadAnalytics(){for(const id of ['analytics-codecs','analytics-genres']){document.getElementById(id).className='loading';}try{const data=await apiFetch('/api/analytics/summary');document.getElementById('analytics-size').textContent=`${fmtNumber(data.total_size_gb||0)} GB`;for(const [id,values] of [['analytics-codecs',data.codecs||{}],['analytics-genres',data.top_genres||{}]]){const el=document.getElementById(id);el.className='stack';const entries=Object.entries(values);el.innerHTML=entries.length?entries.map(([name,count])=>`<div class="service-row"><span>${escapeHtml(name)}</span><strong>${fmtNumber(count)}</strong></div>`).join(''):empty('No analytics data available.');}}catch(error){['analytics-codecs','analytics-genres'].forEach(id=>{document.getElementById(id).className='';document.getElementById(id).innerHTML=errorState(error);});}}
let currentCatPage = 1, totalCatPages = 1;
async function loadCatalog(page = 1) {
  currentCatPage = page;
  const form = document.getElementById('catalog-form');
  const formData = new FormData(form);
  const q = encodeURIComponent(String(formData.get('q') || '').trim());
  const genre = encodeURIComponent(String(formData.get('genre') || ''));
  const mediaType = encodeURIComponent(String(formData.get('media_type') || ''));
  const el = document.getElementById('catalog-table');
  el.className = 'loading'; el.textContent = 'Loading catalog items';
  try {
    const data = await apiFetch(`/api/catalog?q=${q}&genre=${genre}&media_type=${mediaType}&page=${page}&limit=48`);
    totalCatPages = data.pages || 1;
    document.getElementById('catalog-count').textContent = `Total items: ${fmtNumber(data.total)} (${data.items.length} shown)`;
    document.getElementById('cat-page-label').textContent = `Page ${data.page} of ${totalCatPages}`;
    el.className = '';
    const items = data.items || [];
    el.innerHTML = table(['Type', 'Title', 'Year', 'Genres', 'Overview'], items.map(item => `<tr><td>${badge(item.media_type === 'movie' ? 'movie' : 'series')}</td><td><strong>${escapeHtml(item.title)}</strong></td><td>${escapeHtml(item.year || '--')}</td><td>${(item.genres || []).slice(0, 3).map(g => `<span class="status ok" style="font-size:0.7rem">${escapeHtml(g)}</span>`).join(' ')}</td><td class="muted">${escapeHtml((item.overview || '').slice(0, 120))}${item.overview && item.overview.length > 120 ? '...' : ''}</td></tr>`));
  } catch (error) {
    el.className = ''; el.innerHTML = errorState(error);
  }
}
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('catalog-form')?.addEventListener('submit', (e) => { e.preventDefault(); loadCatalog(1); });
  document.getElementById('cat-prev')?.addEventListener('click', () => { if (currentCatPage > 1) loadCatalog(currentCatPage - 1); });
  document.getElementById('cat-next')?.addEventListener('click', () => { if (currentCatPage < totalCatPages) loadCatalog(currentCatPage + 1); });
  document.getElementById('vibe-search-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const resultEl = document.getElementById('vibe-search-result');
    resultEl.className = 'notice loading'; resultEl.textContent = 'Analyzing neural library vibe...';
    try {
      const prompt = new FormData(e.target).get('prompt');
      const res = await apiFetch('/api/vibe-search', { method: 'POST', body: JSON.stringify({ prompt }) });
      resultEl.className = 'notice';
      if (res.matches && res.matches.length) {
        resultEl.innerHTML = `<div class="stack"><strong>🔮 Top Vibe Matches for "${escapeHtml(prompt)}":</strong>` +
          res.matches.map(m => `<div class="decision"><div class="split"><strong>${escapeHtml(m.title)} (${escapeHtml(m.year)})</strong>${badge('Vibe match')}</div><div class="muted">${escapeHtml(m.genres || '')} · ${escapeHtml(m.media_type)}</div><div>${escapeHtml(m.reasoning || '')}</div></div>`).join('') + `</div>`;
      } else { resultEl.innerHTML = empty('No neural vibe matches found.'); }
    } catch(err) { resultEl.className = 'notice'; resultEl.innerHTML = errorState(err); }
  });
  document.getElementById('cinema-night-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const resultEl = document.getElementById('cinema-night-result');
    resultEl.className = 'notice loading'; resultEl.textContent = 'Generating Cinema Night experience...';
    try {
      const theme = new FormData(e.target).get('theme');
      const res = await apiFetch('/api/cinema-night', { method: 'POST', body: JSON.stringify({ theme }) });
      resultEl.className = 'notice';
      if (res.double_feature) {
        const df = res.double_feature;
        resultEl.innerHTML = `<div class="stack"><strong>🎬 ${escapeHtml(df.event_title || 'CineSwarm Cinema Night')}</strong>` +
          `<div class="issue"><strong>Feature 1: ${escapeHtml(df.feature_1.title)} (${escapeHtml(df.feature_1.year)})</strong><div class="muted">${escapeHtml(df.feature_1.pitch)}</div></div>` +
          `<div class="issue" style="border-left-color:var(--accent)"><strong>🍿 Intermission Trivia:</strong><ul style="margin:4px 0 0 16px;padding:0">${(df.intermission_trivia || []).map(t => `<li>${escapeHtml(t)}</li>`).join('')}</ul></div>` +
          `<div class="issue"><strong>Feature 2: ${escapeHtml(df.feature_2.title)} (${escapeHtml(df.feature_2.year)})</strong><div class="muted">${escapeHtml(df.feature_2.pitch)}</div></div></div>`;
      } else { resultEl.innerHTML = empty('Could not generate Cinema Night.'); }
    } catch(err) { resultEl.className = 'notice'; resultEl.innerHTML = errorState(err); }
  });
  document.getElementById('intel-status-refresh')?.addEventListener('click', loadIntelligence);
  document.getElementById('intel-run-storage-audit')?.addEventListener('click', async (e) => {
    action(e.currentTarget, '/api/intelligence/storage-audit', { method: 'POST' }, 'intel-result', res => {
      document.getElementById('intel-result').innerHTML = `<div class="stack" style="margin-top:10px">` +
        `<div class="split"><strong>Vault Storage Optimization Audit Complete</strong>${badge('healthy')}</div>` +
        `<div class="muted">Audited ${fmtNumber(res.audited_items||0)} catalog files across local storage.</div>` +
        `<div class="service-row"><span>Identified >20GB Files</span><strong>${fmtNumber(res.over_20gb_count||0)}</strong></div>` +
        `<div class="service-row"><span>Identified Unaccelerated AV1 Files</span><strong>${fmtNumber(res.unaccelerated_av1_count||0)}</strong></div>` +
        `</div>`;
    });
  });
  document.getElementById('intel-vector-search-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    action(e.submitter, '/api/intelligence/vector-search', { method: 'POST', body: JSON.stringify({ query: String(form.get('query')) }) }, 'vector-search-result', res => {
      const results = res.results || [];
      if (!results.length) { document.getElementById('vector-search-result').innerHTML = empty('No dense vector matches found.'); return; }
      document.getElementById('vector-search-result').innerHTML = `<div class="stack" style="margin-top:10px">` +
        `<div class="muted">Top ${results.length} Matches (384-Dim Local Cosine Vector Similarity):</div>` +
        results.map(m => `<div class="issue"><div class="split"><strong>${escapeHtml(m.title)} (${escapeHtml(m.year || '----')})</strong><span class="status ok">${escapeHtml((Number(m.cosine_similarity||0)*100).toFixed(1))}% match</span></div><div class="muted">${escapeHtml(m.duration_mins||90)} mins · ${escapeHtml(m.genres||'')}</div><div style="font-size:0.82rem;margin-top:4px">${escapeHtml((m.overview||'').slice(0, 140))}${m.overview && m.overview.length > 140 ? '...' : ''}</div></div>`).join('') + `</div>`;
    });
  });
  document.getElementById('intel-consensus-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    action(e.submitter, '/api/intelligence/consensus-deliberate', { method: 'POST', body: JSON.stringify({ title: String(form.get('title')), size_gb: Number(form.get('size_gb')) }) }, 'consensus-result', res => {
      const votes = res.node_votes || {};
      const approved = res.consensus_approved;
      document.getElementById('consensus-result').innerHTML = `<div class="stack" style="margin-top:10px">` +
        `<div class="split"><strong>${escapeHtml(res.candidate_title)}</strong>${badge(approved ? 'consensus approved' : 'consensus rejected')}</div>` +
        `<div class="muted">${escapeHtml(res.summary)}</div>` +
        `<div class="grid two" style="margin-top:4px">` +
        Object.entries(votes).map(([node, info]) => `<div class="service-row"><div><strong style="text-transform:capitalize">${escapeHtml(node)} Node</strong><div class="muted" style="font-size:0.75rem">${escapeHtml(info.reason)}</div></div>${badge(info.vote)}</div>`).join('') +
        `</div></div>`;
    });
  });
  document.getElementById('intel-double-feature-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    action(e.submitter, '/api/intelligence/double-feature', { method: 'POST', body: JSON.stringify({ theme: String(form.get('theme')) }) }, 'intel-result', res => {
      const df = res.double_feature || {};
      const pairing = res.pairing || (df.feature_1 ? [df.feature_1, df.feature_2] : []);
      if (pairing.length >= 2) {
        const m1 = pairing[0], m2 = pairing[1];
        document.getElementById('intel-result').innerHTML = `<div class="stack" style="margin-top:10px">` +
          `<div class="split"><strong>🎬 ${escapeHtml(res.explanation || df.event_title || 'Autonomous Double Feature')}</strong>${badge('published to plex')}</div>` +
          `<div class="issue"><strong>Feature 1: ${escapeHtml(m1.title)} (${escapeHtml(m1.year || '----')})</strong><div class="muted">${escapeHtml(m1.duration_mins || 90)} mins · ${escapeHtml(m1.genres || '')}</div><div style="margin-top:4px">${escapeHtml(m1.overview || m1.pitch || '')}</div></div>` +
          `<div class="issue"><strong>Feature 2: ${escapeHtml(m2.title)} (${escapeHtml(m2.year || '----')})</strong><div class="muted">${escapeHtml(m2.duration_mins || 90)} mins · ${escapeHtml(m2.genres || '')}</div><div style="margin-top:4px">${escapeHtml(m2.overview || m2.pitch || '')}</div></div>` +
          `</div>`;
      } else {
        document.getElementById('intel-result').innerHTML = empty('Could not generate double feature pairing.');
      }
      loadPlaylists();
    });
  });


});


async function loadIntelligence() {
  const display = document.getElementById('intel-status-display');
  if (!display) return;
  display.className = 'loading'; display.textContent = 'Loading Swarm Intelligence status';
  try {
    const data = await apiFetch('/api/intelligence/status');
    const qg = data.quality_guard || {}, rf = data.reinforcement || {}, wo = rf.weight_offsets || {};
    display.className = '';
    display.innerHTML = `<div class="muted">Version: <strong>v${escapeHtml(data.intelligence_version || '2.0.0')}</strong><br>` +
      `Quality-Guard Ceiling: <strong>${escapeHtml(qg.max_size_gb_ceiling)} GB</strong> | Blocked Codecs: <strong>${escapeHtml((qg.blocked_codecs||[]).join(', '))}</strong><br>` +
      `Reinforcement Feedback: 👍 <strong>${rf.good||0}</strong> | 👎 <strong>${rf.bad||0}</strong><br>` +
      `Scoring Multipliers: Watch Affinity x${wo.watch_affinity_multiplier||1.0}, Rarity x${wo.rarity_preservation_multiplier||1.0}, Storage Penalty x${wo.storage_cost_penalty_multiplier||1.0}</div>`;
  } catch(err) { display.className = ''; display.innerHTML = errorState(err); }
}

function loadView(name,force=false){if(name==='overview')return loadOverview(force);if(name==='catalog')return loadCatalog(1);if(name==='operations')return loadOperations(force);if(name==='discovery')return loadDiscovery();if(name==='curation'){loadPlaylists();loadIntelligence();return;}if(name==='preservation')return loadPreservation(force);if(name==='analytics')return loadAnalytics();}

async function action(button,url,options,noticeId,after){const notice=document.getElementById(noticeId);button.disabled=true;notice.textContent='Request in progress...';try{const result=await apiFetch(url,options);notice.textContent=result.message||result.status||'Request completed.';if(after)await after(result);}catch(error){notice.innerHTML=errorState(error);}finally{button.disabled=false;}}
document.querySelectorAll('[data-view]').forEach(button=>button.addEventListener('click',()=>setView(button.dataset.view)));document.querySelectorAll('[data-view-jump]').forEach(button=>button.addEventListener('click',()=>setView(button.dataset.viewJump)));document.getElementById('menu-toggle').addEventListener('click',event=>{const open=document.getElementById('sidebar').classList.toggle('open');event.currentTarget.setAttribute('aria-expanded',String(open));});document.getElementById('refresh-current').addEventListener('click',()=>{const current=document.querySelector('[data-view][aria-current="page"]')?.dataset.view||'overview';loadView(current,true);});
document.getElementById('op-refresh-services').addEventListener('click',event=>action(event.currentTarget,'/api/refresh',{method:'POST'},'operations-notice',()=>loadOperations(true)));document.getElementById('op-reconcile').addEventListener('click',event=>action(event.currentTarget,'/api/reconcile',{method:'POST'},'operations-notice',()=>loadOperations(true)));document.getElementById('op-plex-refresh').addEventListener('click',event=>action(event.currentTarget,'/api/proposals',{method:'POST',body:JSON.stringify({task_type:'plex_library_refresh',reason:'Dashboard operator request'})},'operations-notice',()=>loadOperations(true)));document.getElementById('emergency-stop').addEventListener('click',event=>{const enabled=event.currentTarget.dataset.enabled==='true';action(event.currentTarget,'/api/autonomous/emergency-stop',{method:'POST',body:JSON.stringify({enabled:!enabled})},'emergency-notice',async()=>{state.autonomy=await apiFetch('/api/autonomous/status');renderAutonomy();});});
document.addEventListener('click',async event=>{const approve=event.target.closest('[data-approve-task]');if(approve)await action(approve,`/api/tasks/${encodeURIComponent(approve.dataset.approveTask)}/approve`,{method:'POST'},'operations-notice',()=>loadOperations(true));const discovery=event.target.closest('[data-discovery-action]');if(discovery)await action(discovery,'/api/discovery/action',{method:'POST',body:JSON.stringify({candidate_id:Number(discovery.dataset.candidateId),action:discovery.dataset.discoveryAction})},'discovery-notice',loadDiscovery);const playlist=event.target.closest('[data-delete-playlist]');if(playlist)await action(playlist,'/api/playlists/delete',{method:'POST',body:JSON.stringify({ratingKey:playlist.dataset.deletePlaylist})},'curation-result',loadPlaylists);const feedback=event.target.closest('[data-decision-feedback]');if(feedback)await action(feedback,'/api/decisions/feedback',{method:'POST',body:JSON.stringify({decision_id:feedback.dataset.decisionId,sentiment:feedback.dataset.decisionFeedback,note:''})},'operations-notice',()=>loadOverview(true));const acquire=event.target.closest('[data-acquire-index]');if(acquire){const plan=state.acquisitionPlan,candidate=plan?.candidates?.[Number(acquire.dataset.acquireIndex)],root=plan?.root_folders?.[Number(document.getElementById('acquisition-root').value)],profile=plan?.quality_profiles?.[Number(document.getElementById('acquisition-profile').value)];if(candidate&&root&&profile)await action(acquire,'/api/acquisition/propose',{method:'POST',body:JSON.stringify({media_type:plan.media_type,candidate,root_folder_path:root.path,quality_profile_id:profile.id})},'acquisition-result',()=>loadOperations(true));}});
document.getElementById('discovery-generate').addEventListener('click',event=>action(event.currentTarget,'/api/discovery/generate',{method:'POST'},'discovery-notice',loadDiscovery));document.getElementById('discovery-franchise').addEventListener('click',event=>action(event.currentTarget,'/api/discovery/franchise',{method:'POST'},'discovery-notice',loadDiscovery));document.getElementById('playlists-refresh').addEventListener('click',loadPlaylists);
document.getElementById('acquisition-form').addEventListener('submit',async event=>{event.preventDefault();const form=new FormData(event.currentTarget),button=event.submitter,result=document.getElementById('acquisition-result');button.disabled=true;result.textContent='Building a read-only acquisition plan...';try{const plan=await apiFetch('/api/acquisition/plan',{method:'POST',body:JSON.stringify({media_type:String(form.get('media_type')),term:String(form.get('term'))})});state.acquisitionPlan=plan;const candidates=plan.candidates||[],roots=plan.root_folders||[],profiles=plan.quality_profiles||[];if(!candidates.length){result.innerHTML=empty('No acquisition candidates found.');return;}result.innerHTML=`<div class="stack"><label class="field">Storage root<select id="acquisition-root">${roots.map((r,i)=>`<option value="${i}">${escapeHtml(r.path||r.name||`Root ${i+1}`)}</option>`).join('')}</select></label><label class="field">Quality profile<select id="acquisition-profile">${profiles.map((p,i)=>`<option value="${i}">${escapeHtml(p.name||`Profile ${i+1}`)}</option>`).join('')}</select></label>${candidates.map((c,i)=>`<div class="service-row"><span>${escapeHtml(c.title||'Untitled')} ${c.year?`(${escapeHtml(c.year)})`:''}</span><button type="button" data-acquire-index="${i}" ${!roots.length||!profiles.length?'disabled':''}>Propose acquisition</button></div>`).join('')}</div>`;}catch(error){result.innerHTML=errorState(error);}finally{button.disabled=false;}});for(const [id,type] of [['quality-movies','movie'],['quality-series','series']])document.getElementById(id).addEventListener('click',event=>action(event.currentTarget,'/api/quality/analyze',{method:'POST',body:JSON.stringify({media_type:type})},'analysis-result',result=>{document.getElementById('analysis-result').innerHTML=`<div class="result">${escapeHtml(JSON.stringify(result,null,2))}</div>`;}));document.getElementById('health-scan').addEventListener('click',event=>action(event.currentTarget,'/api/health/scan',{method:'POST',body:JSON.stringify({limit:30})},'analysis-result',result=>{document.getElementById('analysis-result').innerHTML=`<div class="result">${escapeHtml(JSON.stringify(result,null,2))}</div>`;}));
document.getElementById('watch-form').addEventListener('submit',event=>{event.preventDefault();const form=new FormData(event.currentTarget);action(event.submitter,'/api/watch',{method:'POST',body:JSON.stringify({max_minutes:Number(form.get('max_minutes')),genre:String(form.get('genre'))})},'watch-result',res=>{const recs=res.recommendations||[];if(!recs.length){document.getElementById('watch-result').innerHTML=empty('No recommended titles found under duration limit.');return;}document.getElementById('watch-result').innerHTML=`<div class="stack" style="margin-top:10px">`+recs.map(m=>`<div class="decision"><div class="split"><strong>${escapeHtml(m.title)} (${escapeHtml(m.year||'----')})</strong><span class="status ok">${escapeHtml(m.duration_mins||90)} mins</span></div><div class="muted">${escapeHtml(m.genres||'')}</div><div style="margin-top:4px">${escapeHtml(m.reason||m.overview||'Concierge pick for tonight')}</div></div>`).join('')+`</div>`;});});document.getElementById('filmography-form').addEventListener('submit',event=>{event.preventDefault();const form=new FormData(event.currentTarget);action(event.submitter,'/api/filmography',{method:'POST',body:JSON.stringify({person:String(form.get('person')),role:String(form.get('role'))})},'filmography-result',result=>{document.getElementById('filmography-result').innerHTML=`<div class="result">${escapeHtml(JSON.stringify(result,null,2))}</div>`;});});document.getElementById('terminal-form').addEventListener('submit',async event=>{event.preventDefault();const input=document.getElementById('terminal-input'),prompt=input.value.trim();if(!prompt)return;const output=document.getElementById('terminal-output');output.insertAdjacentHTML('beforeend',`<div class="terminal-entry"><span class="prompt">operator&gt;</span> ${escapeHtml(prompt)}</div>`);input.value='';event.submitter.disabled=true;try{const result=await apiFetch('/api/chat',{method:'POST',body:JSON.stringify({prompt})});output.insertAdjacentHTML('beforeend',`<div class="terminal-entry"><span class="prompt">cineswarm&gt;</span> ${escapeHtml(typeof result==='string'?result:JSON.stringify(result,null,2))}</div>`);}catch(error){output.insertAdjacentHTML('beforeend',`<div class="terminal-entry error">${escapeHtml(error.message)}</div>`);}finally{event.submitter.disabled=false;output.scrollTop=output.scrollHeight;input.focus();}});

const initial=location.hash.slice(1);setView(['overview','catalog','operations','discovery','curation','preservation','analytics','terminal'].includes(initial)?initial:'overview');
</script>
</body></html>"""

