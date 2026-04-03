"""
ILUMINATY - Dashboard v3
===========================
Modern web dashboard aligned with desktop app + current architecture.
Live Vision, IPA WorldState, act tool, VLM on-demand.
"""

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ILUMINATY</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #06060b; --surface: #0a0a12; --card: #0f0f1a; --hover: #1a1a2e;
  --border: #1e1e35; --text: #e8e8f0; --muted: #8888a8; --dim: #555570;
  --accent: #00ff88; --purple: #7b61ff; --danger: #ff4466; --warn: #ffaa22;
  --mono: 'JetBrains Mono', monospace; --sans: 'Inter', sans-serif;
}
* { margin:0; padding:0; box-sizing:border-box; }
body { background:var(--bg); color:var(--text); font-family:var(--sans); font-size:14px; overflow-x:hidden; }
a { color:var(--accent); text-decoration:none; }

.header { display:flex; align-items:center; justify-content:space-between; padding:12px 20px; background:var(--surface); border-bottom:1px solid var(--border); }
.logo { display:flex; align-items:center; gap:10px; font-family:var(--mono); font-weight:700; font-size:16px; letter-spacing:2px; }
.logo .dot { width:8px; height:8px; border-radius:50%; background:var(--danger); }
.logo .dot.on { background:var(--accent); box-shadow:0 0 8px var(--accent); }
.stats-bar { display:flex; gap:16px; font-size:12px; color:var(--muted); }
.stats-bar .val { color:var(--text); font-family:var(--mono); }

.main { display:grid; grid-template-columns:1fr 340px; gap:0; height:calc(100vh - 50px); }
.vision { background:#000; position:relative; display:flex; align-items:center; justify-content:center; overflow:hidden; }
.vision img { max-width:100%; max-height:100%; object-fit:contain; }
.vision .overlay { position:absolute; top:12px; left:12px; background:rgba(0,0,0,0.7); padding:6px 12px; border-radius:6px; font-size:11px; font-family:var(--mono); color:var(--accent); }
.vision .empty { color:var(--dim); font-size:18px; }

.sidebar { background:var(--surface); border-left:1px solid var(--border); overflow-y:auto; padding:12px; display:flex; flex-direction:column; gap:10px; }
.panel { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:12px; }
.panel-title { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:1.5px; color:var(--muted); margin-bottom:8px; }
.panel-row { display:flex; justify-content:space-between; font-size:12px; padding:3px 0; }
.panel-row .label { color:var(--muted); }
.panel-row .value { font-family:var(--mono); color:var(--text); }
.panel-row .value.green { color:var(--accent); }
.panel-row .value.warn { color:var(--warn); }
.panel-row .value.red { color:var(--danger); }

.badge { display:inline-block; padding:2px 8px; border-radius:4px; font-size:10px; font-weight:700; font-family:var(--mono); }
.badge.safe { background:rgba(0,255,136,0.1); color:var(--accent); }
.badge.hybrid { background:rgba(255,170,34,0.1); color:var(--warn); }
.badge.raw { background:rgba(255,68,102,0.1); color:var(--danger); }

.ocr-box { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:8px; font-family:var(--mono); font-size:11px; color:var(--muted); max-height:200px; overflow-y:auto; white-space:pre-wrap; word-break:break-all; }
.facts-list { font-size:11px; }
.facts-list .fact { padding:4px 0; border-bottom:1px solid var(--border); }
.facts-list .fact:last-child { border:none; }
.fact-kind { color:var(--purple); font-family:var(--mono); font-size:10px; }

.btn { background:var(--card); border:1px solid var(--border); color:var(--text); padding:6px 14px; border-radius:6px; cursor:pointer; font-size:12px; font-family:var(--sans); transition:all 0.15s; }
.btn:hover { background:var(--hover); border-color:var(--accent); }
.btn.accent { background:rgba(0,255,136,0.1); border-color:var(--accent); color:var(--accent); }
.btn-row { display:flex; gap:6px; flex-wrap:wrap; }
</style>
</head>
<body>

<div class="header">
  <div class="logo">
    <span class="dot" id="status-dot"></span>
    ILUMINATY
    <span style="font-size:11px;color:var(--muted);font-weight:400">v1.0 &middot; IPA v2.1</span>
  </div>
  <div class="stats-bar">
    <span>FPS <span class="val" id="h-fps">--</span></span>
    <span>RAM <span class="val" id="h-ram">--</span></span>
    <span>Frames <span class="val" id="h-frames">--</span></span>
    <span>Mode <span class="val" id="h-mode">SAFE</span></span>
  </div>
</div>

<div class="main">
  <div class="vision" id="vision-area">
    <img id="vision-img" style="display:none" />
    <div class="empty" id="vision-empty">Awaiting vision...</div>
    <div class="overlay" id="vision-overlay" style="display:none">
      <span id="v-res">--</span> &middot; <span id="v-monitor">--</span> &middot; <span id="v-window">--</span>
    </div>
  </div>

  <div class="sidebar">
    <!-- IPA WorldState -->
    <div class="panel">
      <div class="panel-title">IPA WorldState</div>
      <div class="panel-row"><span class="label">Phase</span><span class="value" id="ws-phase">--</span></div>
      <div class="panel-row"><span class="label">Surface</span><span class="value" id="ws-surface">--</span></div>
      <div class="panel-row"><span class="label">Readiness</span><span class="value" id="ws-ready">--</span></div>
      <div class="panel-row"><span class="label">Uncertainty</span><span class="value" id="ws-uncertainty">--</span></div>
      <div class="panel-row"><span class="label">Staleness</span><span class="value" id="ws-staleness">--</span></div>
      <div class="panel-row"><span class="label">Domain</span><span class="value" id="ws-domain">--</span></div>
      <div class="panel-row"><span class="label">Tick</span><span class="value" id="ws-tick">--</span></div>
    </div>

    <!-- Scene + Perception -->
    <div class="panel">
      <div class="panel-title">Perception</div>
      <div class="panel-row"><span class="label">Scene</span><span class="value" id="p-scene">--</span></div>
      <div class="panel-row"><span class="label">Attention</span><span class="value" id="p-attention">--</span></div>
      <div class="panel-row"><span class="label">Events</span><span class="value" id="p-events">--</span></div>
      <div class="panel-row"><span class="label">FPS Advice</span><span class="value" id="p-fps-advice">--</span></div>
    </div>

    <!-- Visual Facts -->
    <div class="panel">
      <div class="panel-title">Visual Facts</div>
      <div class="facts-list" id="facts-list"><span style="color:var(--dim)">No facts yet</span></div>
    </div>

    <!-- OCR Text -->
    <div class="panel">
      <div class="panel-title">OCR Text</div>
      <div class="ocr-box" id="ocr-text">Waiting for capture...</div>
    </div>

    <!-- System -->
    <div class="panel">
      <div class="panel-title">System</div>
      <div class="panel-row"><span class="label">Buffer</span><span class="value" id="s-buffer">--</span></div>
      <div class="panel-row"><span class="label">Capture FPS</span><span class="value" id="s-fps">--</span></div>
      <div class="panel-row"><span class="label">CPU</span><span class="value" id="s-cpu">--</span></div>
      <div class="panel-row"><span class="label">GPU</span><span class="value" id="s-gpu">--</span></div>
      <div class="panel-row"><span class="label">VLM</span><span class="value" id="s-vlm">--</span></div>
    </div>

    <!-- Controls -->
    <div class="panel">
      <div class="panel-title">Controls</div>
      <div class="btn-row">
        <button class="btn accent" onclick="describeScreen()">Describe (VLM)</button>
        <button class="btn" onclick="refreshAll()">Refresh</button>
      </div>
    </div>
  </div>
</div>

<script>
const API = location.origin;
let pollId = null;
let visionId = null;

async function get(path) {
  try { const r = await fetch(API + path); return r.ok ? await r.json() : null; } catch { return null; }
}
async function post(path, body) {
  try { const r = await fetch(API + path, {method:'POST',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined}); return r.ok ? await r.json() : null; } catch { return null; }
}

function $(id) { return document.getElementById(id); }
function setText(id, v) { const el = $(id); if(el) el.textContent = v; }

// ─── Live Vision ───
async function pollVision() {
  const d = await get("/vision/snapshot?ocr=true&include_image=true");
  if (!d) return;
  const img = $("vision-img");
  const empty = $("vision-empty");
  const overlay = $("vision-overlay");
  if (d.image_base64) {
    img.src = "data:" + (d.mime_type||"image/webp") + ";base64," + d.image_base64;
    img.style.display = "block";
    empty.style.display = "none";
    overlay.style.display = "block";
    setText("v-res", (d.width||"?") + "x" + (d.height||"?"));
    setText("v-monitor", "Mon " + (d.monitor_id||"?"));
    setText("v-window", (d.active_window?.title || d.active_window?.name || "").substring(0,40));
  }
  if (d.ocr_text) setText("ocr-text", d.ocr_text.substring(0,2000));
}

// ─── IPA WorldState ───
async function pollWorld() {
  const d = await get("/perception/world");
  if (!d) return;
  setText("ws-phase", d.task_phase || "--");
  setText("ws-surface", (d.active_surface || "--").substring(0,50));
  setText("ws-ready", d.readiness ? "YES" : "NO");
  $("ws-ready").className = "value " + (d.readiness ? "green" : "red");
  setText("ws-uncertainty", ((d.uncertainty||0)*100).toFixed(0) + "%");
  $("ws-uncertainty").className = "value " + ((d.uncertainty||0) > 0.5 ? "warn" : "green");
  setText("ws-staleness", (d.staleness_ms||0) + "ms");
  setText("ws-domain", d.domain_pack || d.entities?.find(e=>e.startsWith("workflow:"))?.replace("workflow:","") || "--");
  setText("ws-tick", d.tick_id || "--");

  // Visual facts
  const facts = d.visual_facts || [];
  const fl = $("facts-list");
  if (facts.length) {
    fl.innerHTML = facts.map(f =>
      '<div class="fact"><span class="fact-kind">' + (f.kind||"?") + '</span> ' + (f.text||"").substring(0,120) + '</div>'
    ).join("");
  } else {
    fl.innerHTML = '<span style="color:var(--dim)">No facts</span>';
  }
}

// ─── Perception ───
async function pollPerception() {
  const d = await get("/perception/state");
  if (!d) return;
  setText("p-scene", (d.scene_state||"--").toUpperCase() + " (" + ((d.scene_confidence||0)*100).toFixed(0) + "%)");
  setText("p-attention", d.attention_focus || "--");
  setText("p-events", d.event_count || 0);
  setText("p-fps-advice", d.fps_advice?.toFixed(1) || "--");
  setText("h-mode", d.world?.risk_mode?.toUpperCase() || "SAFE");
}

// ─── System ───
async function pollSystem() {
  const b = await get("/buffer/stats");
  if (b) {
    setText("s-buffer", (b.slot_count||0) + "/" + (b.max_slots||0));
    setText("s-fps", (b.capture_fps||0).toFixed(1));
    setText("h-fps", (b.capture_fps||0).toFixed(1));
    setText("h-ram", ((b.memory_mb||0)).toFixed(1) + "MB");
    setText("h-frames", b.total_frames || 0);
  }
  const p = await get("/perception/state");
  if (p?.visual) {
    const v = p.visual;
    const status = v.provider_status || {};
    setText("s-vlm", v.provider + " (" + (status.status||"?") + ") " + (status.runtime_device||""));
    setText("s-gpu", status.cuda_available ? status.runtime_device : "CPU");
  }
}

// ─── Describe Screen (VLM on-demand) ───
async function describeScreen() {
  setText("ocr-text", "Running VLM inference...");
  const d = await post("/vision/describe");
  if (!d) { setText("ocr-text", "VLM failed"); return; }
  const vlm = (d.facts||[]).find(f => f.kind === "image_caption");
  setText("ocr-text", vlm ? vlm.text : (d.summary || "No description"));
}

// ─── Health ───
async function checkHealth() {
  const d = await get("/health");
  const dot = $("status-dot");
  if (d && d.status === "alive") {
    dot.classList.add("on");
  } else {
    dot.classList.remove("on");
  }
}

// ─── Refresh All ───
function refreshAll() {
  checkHealth();
  pollVision();
  pollWorld();
  pollPerception();
  pollSystem();
}

// ─── Start Polling ───
function start() {
  refreshAll();
  visionId = setInterval(pollVision, 1500);
  pollId = setInterval(() => { pollWorld(); pollPerception(); pollSystem(); checkHealth(); }, 3000);
}

function stop() {
  if (visionId) clearInterval(visionId);
  if (pollId) clearInterval(pollId);
}

start();
</script>
</body>
</html>'''
