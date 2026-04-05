"""
ILUMINATY - Dashboard v3
===========================
Live dashboard — real-time vision, perception state, OCR.
No VLM dependency. Clean sidebar.
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
  --bg:#06060b; --surface:#0a0a12; --card:#0f0f1a; --hover:#1a1a2e;
  --border:#1e1e35; --text:#e8e8f0; --muted:#8888a8; --dim:#555570;
  --accent:#00ff88; --purple:#7b61ff; --danger:#ff4466; --warn:#ffaa22;
  --mono:'JetBrains Mono',monospace; --sans:'Inter',sans-serif;
}
*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:14px;overflow-x:hidden;}

/* ── Header ── */
.header{display:flex;align-items:center;justify-content:space-between;padding:10px 20px;background:var(--surface);border-bottom:1px solid var(--border);height:44px;}
.logo{display:flex;align-items:center;gap:10px;font-family:var(--mono);font-weight:700;font-size:15px;letter-spacing:2px;}
.dot{width:8px;height:8px;border-radius:50%;background:var(--danger);transition:background 0.3s;}
.dot.on{background:var(--accent);box-shadow:0 0 8px var(--accent);}
.sub{font-size:11px;color:var(--muted);font-weight:400;}
.hstats{display:flex;gap:20px;font-size:12px;color:var(--muted);}
.hstats .v{color:var(--text);font-family:var(--mono);}
.mode-badge{padding:2px 8px;border-radius:4px;font-size:10px;font-weight:700;font-family:var(--mono);background:rgba(0,255,136,0.1);color:var(--accent);}

/* ── Layout ── */
.main{display:grid;grid-template-columns:1fr 320px;height:calc(100vh - 44px);}

/* ── Vision panel ── */
.vision{background:#000;position:relative;display:flex;align-items:center;justify-content:center;overflow:hidden;}
#vision-img{max-width:100%;max-height:100%;object-fit:contain;}
.v-overlay{position:absolute;bottom:0;left:0;right:0;background:linear-gradient(transparent,rgba(0,0,0,0.8));padding:10px 14px;font-size:11px;font-family:var(--mono);color:var(--accent);display:none;}
.v-empty{color:var(--dim);font-size:16px;}

/* ── Sidebar ── */
.sidebar{background:var(--surface);border-left:1px solid var(--border);overflow-y:auto;padding:10px;display:flex;flex-direction:column;gap:8px;}
.panel{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:10px 12px;}
.ptitle{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:1.5px;color:var(--muted);margin-bottom:8px;}
.row{display:flex;justify-content:space-between;align-items:center;font-size:12px;padding:3px 0;}
.row .lbl{color:var(--muted);}
.row .val{font-family:var(--mono);color:var(--text);text-align:right;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.val.green{color:var(--accent);}
.val.warn{color:var(--warn);}
.val.red{color:var(--danger);}

/* ── Scene badge ── */
.scene-badge{display:inline-block;padding:3px 10px;border-radius:20px;font-size:11px;font-weight:700;font-family:var(--mono);background:rgba(123,97,255,0.15);color:var(--purple);margin-bottom:6px;}

/* ── OCR ── */
.ocr-tabs{display:flex;gap:3px;margin-bottom:6px;}
.ocr-tab{background:var(--bg);border:1px solid var(--border);border-radius:4px 4px 0 0;padding:3px 10px;font-size:10px;font-family:var(--mono);color:var(--muted);cursor:pointer;transition:all 0.15s;}
.ocr-tab.active{background:var(--card);border-bottom-color:var(--card);color:var(--accent);font-weight:700;}
.ocr-tab:hover:not(.active){color:var(--text);}
.ocr-pane{display:none;}
.ocr-pane.active{display:block;}
.ocr-box{background:var(--bg);border:1px solid var(--border);border-radius:0 6px 6px 6px;padding:8px;font-family:var(--mono);font-size:10px;color:var(--muted);max-height:160px;overflow-y:auto;white-space:pre-wrap;word-break:break-all;line-height:1.5;}
.ocr-label{font-size:9px;color:var(--dim);margin-bottom:3px;font-family:var(--mono);}

/* ── Monitor mini-map ── */
.monitor-grid{display:flex;flex-direction:column;gap:4px;}
.monitor-item{background:var(--bg);border:1px solid var(--border);border-radius:5px;padding:5px 8px;font-size:11px;font-family:var(--mono);}
.monitor-item.active{border-color:var(--accent);}
.monitor-label{color:var(--accent);font-weight:700;margin-right:6px;}
.monitor-win{color:var(--muted);font-size:10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}

/* ── Controls ── */
.btn{background:var(--card);border:1px solid var(--border);color:var(--text);padding:6px 14px;border-radius:6px;cursor:pointer;font-size:12px;font-family:var(--sans);transition:all 0.15s;}
.btn:hover{background:var(--hover);border-color:var(--accent);}
.btn-row{display:flex;gap:6px;}

/* ── Agents panel ── */
.agent-list{display:flex;flex-direction:column;gap:6px;}
.agent-row{background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:7px 10px;display:flex;flex-direction:column;gap:5px;}
.agent-row.role-executor{border-color:#00ff8844;}
.agent-row.role-planner{border-color:#7b61ff44;}
.agent-row.role-observer{border-color:#4499ff44;}
.agent-row.role-verifier{border-color:#ffaa2244;}
.agent-row.role-any{border-color:#ffffff22;}
.agent-hdr{display:flex;align-items:center;gap:8px;}
.agent-name{font-family:var(--mono);font-size:12px;font-weight:600;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.agent-id{font-family:var(--mono);font-size:9px;color:var(--dim);}
.role-badge{font-size:9px;font-weight:700;font-family:var(--mono);padding:2px 7px;border-radius:3px;text-transform:uppercase;}
.rb-executor{background:rgba(0,255,136,0.1);color:var(--accent);}
.rb-planner{background:rgba(123,97,255,0.1);color:var(--purple);}
.rb-observer{background:rgba(68,153,255,0.1);color:#4499ff;}
.rb-verifier{background:rgba(255,170,34,0.1);color:var(--warn);}
.rb-any{background:rgba(255,255,255,0.06);color:var(--muted);}
.agent-controls{display:flex;gap:5px;align-items:center;flex-wrap:wrap;}
.role-select{background:var(--card);border:1px solid var(--border);color:var(--text);padding:3px 7px;border-radius:4px;font-size:11px;font-family:var(--mono);cursor:pointer;}
.role-select:focus{outline:none;border-color:var(--accent);}
.btn-sm{background:var(--card);border:1px solid var(--border);color:var(--text);padding:3px 10px;border-radius:4px;cursor:pointer;font-size:11px;font-family:var(--sans);transition:all 0.15s;}
.btn-sm:hover{background:var(--hover);border-color:var(--accent);}
.btn-sm.danger:hover{border-color:var(--danger);color:var(--danger);}
.agent-meta{font-size:10px;color:var(--dim);font-family:var(--mono);}
.no-agents{color:var(--dim);font-size:11px;text-align:center;padding:12px 0;}

/* Scrollbar */
::-webkit-scrollbar{width:4px;} ::-webkit-scrollbar-track{background:transparent;} ::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px;}
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="logo">
    <span class="dot" id="status-dot"></span>
    ILUMINATY
    <span class="sub">v1.0 &middot; IPA v3</span>
  </div>
  <div class="hstats">
    <span>FPS <span class="v" id="h-fps">--</span></span>
    <span>RAM <span class="v" id="h-ram">--</span></span>
    <span>Frames <span class="v" id="h-frames">--</span></span>
    <span class="mode-badge" id="h-mode">SAFE</span>
  </div>
</div>

<div class="main">

  <!-- Live Vision -->
  <div class="vision">
    <img id="vision-img" style="display:none"/>
    <div class="v-empty" id="vision-empty">Awaiting vision...</div>
    <div class="v-overlay" id="v-overlay">
      <span id="v-res">--</span> &nbsp;&middot;&nbsp;
      Mon <span id="v-mon">--</span> &nbsp;&middot;&nbsp;
      <span id="v-win">--</span>
    </div>
  </div>

  <!-- Sidebar -->
  <div class="sidebar">

    <!-- 1. Active Context (most useful at top) -->
    <div class="panel">
      <div class="ptitle">Active Context</div>
      <div class="row"><span class="lbl">Surface</span><span class="val" id="ctx-surface">--</span></div>
      <div class="row"><span class="lbl">Phase</span><span class="val" id="ctx-phase">--</span></div>
      <div class="row"><span class="lbl">Domain</span><span class="val" id="ctx-domain">--</span></div>
      <div class="row"><span class="lbl">Active window</span><span class="val" id="ctx-window">--</span></div>
    </div>

    <!-- 2. Scene & Perception -->
    <div class="panel">
      <div class="ptitle">Perception</div>
      <div id="p-scene-badge" class="scene-badge">--</div>
      <div class="row"><span class="lbl">Attention</span><span class="val" id="p-attention">--</span></div>
      <div class="row"><span class="lbl">Events buffered</span><span class="val" id="p-events">--</span></div>
      <div class="row"><span class="lbl">Readiness</span><span class="val" id="p-ready">--</span></div>
      <div class="row"><span class="lbl">Uncertainty</span><span class="val" id="p-uncertainty">--</span></div>
    </div>

    <!-- 3. Monitors -->
    <div class="panel">
      <div class="ptitle">Monitors (<span id="mon-count">--</span>)</div>
      <div class="monitor-grid" id="mon-grid">
        <div class="monitor-item"><span class="monitor-label">--</span></div>
      </div>
    </div>

    <!-- 4. OCR Text per monitor -->
    <div class="panel">
      <div class="ptitle" style="display:flex;justify-content:space-between;align-items:center;">
        <span>Screen Text (OCR)</span>
        <span style="font-size:9px;color:var(--dim);font-weight:400;text-transform:none;letter-spacing:0" id="ocr-latency"></span>
      </div>
      <div class="ocr-tabs" id="ocr-tabs">
        <div class="ocr-tab active" data-mon="active" onclick="switchOcrTab(this,'active')">Active</div>
      </div>
      <div id="ocr-panes">
        <div class="ocr-pane active" id="ocr-pane-active">
          <div class="ocr-box" id="ocr-text-active">Waiting for capture...</div>
        </div>
      </div>
    </div>

    <!-- 5. System (technical, at bottom) -->
    <div class="panel">
      <div class="ptitle">System</div>
      <div class="row"><span class="lbl">Buffer</span><span class="val" id="s-buffer">--</span></div>
      <div class="row"><span class="lbl">Capture FPS</span><span class="val" id="s-fps">--</span></div>
      <div class="row"><span class="lbl">OCR</span><span class="val" id="s-ocr">--</span></div>
      <div class="row"><span class="lbl">GPU</span><span class="val" id="s-gpu">CPU</span></div>
    </div>

    <!-- 6. Agents -->
    <div class="panel">
      <div class="ptitle" style="display:flex;justify-content:space-between;align-items:center;">
        <span>Agents (<span id="agent-count">0</span>)</span>
        <span style="font-size:9px;color:var(--dim);font-weight:400;text-transform:none;letter-spacing:0">live</span>
      </div>
      <div class="agent-list" id="agent-list">
        <div class="no-agents">No agents registered</div>
      </div>
    </div>

    <!-- 7. Command Bar -->
    <div class="panel">
      <div class="ptitle">Command</div>
      <div style="display:flex;gap:6px;margin-bottom:6px;">
        <select class="role-select" id="cmd-type" style="flex:0 0 90px;" onchange="updateCmdHint()">
          <option value="act">act</option>
          <option value="key">key</option>
          <option value="type">type</option>
          <option value="run">run</option>
        </select>
        <input id="cmd-input" type="text" placeholder='target or text...'
          style="flex:1;background:var(--bg);border:1px solid var(--border);border-radius:4px;padding:4px 8px;color:var(--text);font-family:var(--mono);font-size:12px;outline:none;"
          onkeydown="if(event.key==='Enter')sendCmd()"
          onfocus="this.style.borderColor='var(--accent)'"
          onblur="this.style.borderColor='var(--border)'"
        />
        <button class="btn-sm" onclick="sendCmd()">Run</button>
      </div>
      <div id="cmd-hint" style="font-size:10px;color:var(--dim);font-family:var(--mono);margin-bottom:4px;">click &quot;Save button&quot;</div>
      <div id="cmd-result" style="font-size:10px;color:var(--muted);font-family:var(--mono);min-height:16px;"></div>
    </div>

    <!-- 8. Recording -->
    <div class="panel">
      <div class="ptitle" style="display:flex;justify-content:space-between;align-items:center;">
        <span>Recording</span>
        <span id="rec-status-badge" style="font-size:9px;color:var(--dim);font-family:var(--mono);">off</span>
      </div>
      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;">
        <select class="role-select" id="rec-fmt">
          <option value="gif">GIF</option>
          <option value="webm">WebM</option>
          <option value="mp4">MP4</option>
        </select>
        <select class="role-select" id="rec-duration">
          <option value="30">30s</option>
          <option value="60">1min</option>
          <option value="120">2min</option>
          <option value="300">5min</option>
        </select>
        <button class="btn-sm" id="rec-btn" onclick="toggleRecording()">Start</button>
      </div>
      <div id="rec-info" style="font-size:10px;color:var(--dim);font-family:var(--mono);margin-top:4px;"></div>
    </div>

    <!-- 9. Controls -->
    <div class="panel">
      <div class="ptitle">Controls</div>
      <div class="btn-row">
        <button class="btn" onclick="refreshAll()">Refresh</button>
        <button class="btn" onclick="copyApiUrl()">Copy API URL</button>
      </div>
    </div>

  </div>
</div>

<script>
const API = location.origin;
const TOKEN = new URLSearchParams(location.search).get('token') || '';
const HEADERS = TOKEN ? {'X-API-Key': TOKEN} : {};

async function get(path) {
  try { const r = await fetch(API+path,{headers:HEADERS}); return r.ok ? await r.json() : null; } catch { return null; }
}

function $(id) { return document.getElementById(id); }
function setText(id, v) { const el=$(id); if(el) el.textContent=v; }
function setClass(id, cls) { const el=$(id); if(el) el.className='val '+cls; }

// ── Vision ────────────────────────────────────────────────────────────────────
async function pollVision() {
  const d = await get('/vision/snapshot?ocr=true&include_image=true');
  if (!d) return;
  if (d.image_base64) {
    const img = $('vision-img');
    img.src = 'data:'+(d.mime_type||'image/webp')+';base64,'+d.image_base64;
    img.style.display = 'block';
    $('vision-empty').style.display = 'none';
    $('v-overlay').style.display = 'block';
    setText('v-res', (d.width||'?')+'x'+(d.height||'?'));
    setText('v-mon', d.monitor_id||'?');
    const win = (d.active_window?.title || d.active_window?.name || '').substring(0,45);
    setText('v-win', win);
    // Also update active window in ctx panel
    if (win) setText('ctx-window', win);
  }
  // OCR for active monitor lives in pollOcr — skip here
}

// ── World State ───────────────────────────────────────────────────────────────
async function pollWorld() {
  const d = await get('/perception/world');
  if (!d) return;

  // Surface — strip full exe paths
  const rawSurf = (d.active_surface||'');
  const surfParts = rawSurf.split(/[\\/]/);
  const surface = (surfParts[surfParts.length-1]||rawSurf).replace(/\.exe.*/i,'');
  setText('ctx-surface', surface.substring(0,40) || '--');
  setText('ctx-phase', d.task_phase || '--');
  setText('ctx-domain', d.domain_pack || '--');

  setText('p-ready', d.readiness ? 'YES' : 'NO');
  setClass('p-ready', d.readiness ? 'green' : 'red');
  const unc = ((d.uncertainty||0)*100).toFixed(0)+'%';
  setText('p-uncertainty', unc);
  setClass('p-uncertainty', (d.uncertainty||0) > 0.6 ? 'warn' : 'green');
}

// ── Perception ────────────────────────────────────────────────────────────────
async function pollPerception() {
  const d = await get('/perception/state');
  if (!d) return;

  const scene = (d.scene_state||'unknown').toUpperCase();
  const conf = ((d.scene_confidence||0)*100).toFixed(0);
  const badge = $('p-scene-badge');
  if (badge) { badge.textContent = scene+' ('+conf+'%)'; }

  setText('p-attention', d.attention_focus || '--');
  setText('p-events', d.event_count || 0);
  setText('h-mode', (d.world?.risk_mode||'SAFE').toUpperCase());
}

// ── Spatial / Monitors ────────────────────────────────────────────────────────
async function pollSpatial() {
  const d = await get('/spatial/state?include_windows=true');
  if (!d) return;

  const monitors = d.monitors || [];
  const activeId = d.active_monitor_id;
  const wins = d.windows || [];

  setText('mon-count', monitors.length);

  // Rebuild OCR tabs if monitor list changed
  const monIds = monitors.map(m => m.id);
  if (JSON.stringify(monIds) !== JSON.stringify(_ocrMonitors)) {
    _rebuildOcrTabs(monIds);
  }

  const grid = $('mon-grid');
  if (!grid || !monitors.length) return;

  // Group windows by monitor
  const byMon = {};
  for (const w of wins) {
    const mid = w.monitor_id || 0;
    if (!byMon[mid]) byMon[mid] = [];
    byMon[mid].push((w.title||'').substring(0,35));
  }

  grid.innerHTML = monitors.map(m => {
    const active = m.id === activeId;
    const winList = (byMon[m.id]||[]).slice(0,2).join(', ') || 'no windows';
    return '<div class="monitor-item'+(active?' active':'')+'">'+
      '<span class="monitor-label">M'+m.id+'</span>'+
      '<span style="color:var(--muted);font-size:10px">'+m.width+'x'+m.height+'</span>'+
      '<div class="monitor-win">'+winList+'</div>'+
    '</div>';
  }).join('');

  // Active window context
  if (d.active_window) {
    const title = (d.active_window.title||'').substring(0,45);
    if (title) setText('ctx-window', title);
  }
}

// ── System ────────────────────────────────────────────────────────────────────
async function pollSystem() {
  const b = await get('/buffer/stats');
  if (b) {
    setText('s-buffer', (b.slots_used||0)+'/'+(b.slots_max||0));
    setText('s-fps', (b.current_fps||b.capture_fps||0).toFixed(1));
    setText('h-fps', (b.current_fps||b.capture_fps||0).toFixed(1));
    setText('h-ram', ((b.memory_mb||0)).toFixed(1)+'MB');
    setText('h-frames', b.total_frames_captured||b.total_frames||0);
    setText('s-ocr', b.ocr_available ? 'active' : 'off');
  }
  const ipa = await get('/ipa/status');
  if (ipa) {
    const hasGpu = ipa.engine?.encoder?.backend && ipa.engine.encoder.backend !== 'cpu';
    setText('s-gpu', hasGpu ? 'GPU (DirectML)' : 'CPU');
  }
}

// ── Health ────────────────────────────────────────────────────────────────────
async function checkHealth() {
  const d = await get('/health');
  const dot = $('status-dot');
  if (d?.status === 'alive') dot.classList.add('on');
  else dot.classList.remove('on');
}

// ── OCR per monitor ──────────────────────────────────────────────────────────
let _ocrMonitors = [];   // [1,2,3] — built from spatial poll
let _activeOcrTab = 'active';

function switchOcrTab(el, monKey) {
  _activeOcrTab = monKey;
  document.querySelectorAll('.ocr-tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.ocr-pane').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  const pane = $('ocr-pane-'+monKey);
  if (pane) pane.classList.add('active');
  // Immediately load if not yet fetched
  if (monKey !== 'active') fetchOcrForMonitor(parseInt(monKey));
}

function _rebuildOcrTabs(monitors) {
  const tabs = $('ocr-tabs');
  const panes = $('ocr-panes');
  if (!tabs || !panes) return;

  // Add tabs/panes for new monitors
  monitors.forEach(mid => {
    if (!$('ocr-tab-'+mid)) {
      const tab = document.createElement('div');
      tab.className = 'ocr-tab';
      tab.id = 'ocr-tab-'+mid;
      tab.dataset.mon = mid;
      tab.textContent = 'M'+mid;
      tab.onclick = function() { switchOcrTab(this, String(mid)); };
      tabs.appendChild(tab);

      const pane = document.createElement('div');
      pane.className = 'ocr-pane';
      pane.id = 'ocr-pane-'+mid;
      pane.innerHTML = '<div class="ocr-label" id="ocr-meta-'+mid+'"></div><div class="ocr-box" id="ocr-text-'+mid+'">–</div>';
      panes.appendChild(pane);
    }
  });
  _ocrMonitors = monitors;
}

async function fetchOcrForMonitor(mid) {
  const t0 = performance.now();
  const d = await get('/vision/ocr?monitor_id='+mid);
  const ms = (performance.now()-t0).toFixed(0);
  const box = $('ocr-text-'+mid);
  const meta = $('ocr-meta-'+mid);
  if (!box) return;
  if (!d) { box.textContent = 'unavailable'; return; }
  const txt = (d.text || d.ocr_text || '').trim();
  box.textContent = txt || '(no text detected)';
  if (meta) meta.textContent = (d.width||'?')+'x'+(d.height||'?')+' · '+ms+'ms';
}

async function fetchOcrActive() {
  const t0 = performance.now();
  const d = await get('/vision/ocr');
  const ms = (performance.now()-t0).toFixed(0);
  const box = $('ocr-text-active');
  if (!box) return;
  if (!d) { box.textContent = 'unavailable'; return; }
  const txt = (d.text || d.ocr_text || '').trim();
  box.textContent = txt || '(no text detected)';
  setText('ocr-latency', ms+'ms');
}

async function pollOcr() {
  // Always refresh active tab
  if (_activeOcrTab === 'active') {
    await fetchOcrActive();
  } else {
    const mid = parseInt(_activeOcrTab);
    if (!isNaN(mid)) await fetchOcrForMonitor(mid);
  }
}

// ── Agents ────────────────────────────────────────────────────────────────────
const ROLES = ['observer','planner','executor','verifier','any'];
const ROLE_COLORS = {observer:'rb-observer',planner:'rb-planner',executor:'rb-executor',verifier:'rb-verifier',any:'rb-any'};

async function pollAgents() {
  const d = await get('/agents');
  if (!d) return;
  const agents = d.agents || [];
  setText('agent-count', agents.length);
  const list = $('agent-list');
  if (!list) return;

  if (!agents.length) {
    list.innerHTML = '<div class="no-agents">No agents registered</div>';
    return;
  }

  list.innerHTML = agents.map(a => {
    const role = a.role || 'observer';
    const rc = ROLE_COLORS[role] || 'rb-observer';
    const uptime = a.uptime_s != null ? (a.uptime_s < 60 ? a.uptime_s.toFixed(0)+'s' : (a.uptime_s/60).toFixed(1)+'m') : '--';
    const monStr = a.monitors && a.monitors.length ? 'M'+a.monitors.join('+M') : 'all';
    const opts = ROLES.map(r => `<option value="${r}"${r===role?' selected':''}>${r}</option>`).join('');
    return `<div class="agent-row role-${role}" id="ar-${a.agent_id}">
      <div class="agent-hdr">
        <span class="agent-name" title="${a.agent_id}">${a.name || 'unnamed'}</span>
        <span class="role-badge ${rc}">${role}</span>
      </div>
      <div class="agent-meta">${a.agent_id.substring(0,20)} · ${monStr} · ${uptime}</div>
      <div class="agent-controls">
        <select class="role-select" id="rs-${a.agent_id}" onchange="previewRole('${a.agent_id}')">
          ${opts}
        </select>
        <button class="btn-sm" onclick="applyRole('${a.agent_id}')">Apply</button>
        <button class="btn-sm danger" onclick="removeAgent('${a.agent_id}')">×</button>
      </div>
    </div>`;
  }).join('');
}

function previewRole(agentId) {
  // Visual preview before applying
  const sel = $('rs-'+agentId);
  const row = $('ar-'+agentId);
  if (!sel || !row) return;
  const newRole = sel.value;
  row.className = 'agent-row role-'+newRole;
  const badge = row.querySelector('.role-badge');
  if (badge) {
    badge.className = 'role-badge '+(ROLE_COLORS[newRole]||'rb-observer');
    badge.textContent = newRole+' *';
  }
}

async function applyRole(agentId) {
  const sel = $('rs-'+agentId);
  if (!sel) return;
  const newRole = sel.value;
  try {
    const r = await fetch(API+'/agents/'+agentId, {
      method: 'PATCH',
      headers: {...HEADERS, 'Content-Type': 'application/json'},
      body: JSON.stringify({role: newRole}),
    });
    if (r.ok) {
      pollAgents();
    } else {
      const err = await r.json().catch(() => ({}));
      alert('Failed: '+(err.detail||r.status));
    }
  } catch(e) { alert('Error: '+e); }
}

async function removeAgent(agentId) {
  if (!confirm('Remove agent '+agentId+'?')) return;
  try {
    await fetch(API+'/agents/'+agentId, {method:'DELETE', headers:HEADERS});
    pollAgents();
  } catch(e) { alert('Error: '+e); }
}

// ── Command bar ───────────────────────────────────────────────────────────────
const CMD_HINTS = {
  act:  'click "Save button"',
  key:  'ctrl+s',
  type: 'hello world',
  run:  'echo test',
};
function updateCmdHint() {
  const t = $('cmd-type')?.value || 'act';
  setText('cmd-hint', CMD_HINTS[t] || '');
}

async function sendCmd() {
  const type  = $('cmd-type')?.value || 'act';
  const input = ($('cmd-input')?.value || '').trim();
  if (!input) return;
  const res = $('cmd-result');
  if (res) { res.textContent = 'running...'; res.style.color = 'var(--muted)'; }

  let ok = false, msg = '';
  try {
    if (type === 'run') {
      const r = await fetch(API+`/terminal/exec?cmd=${encodeURIComponent(input)}&timeout=15`,
        {method:'POST', headers:HEADERS});
      const d = await r.json();
      ok = d.success !== false;
      msg = (d.stdout || d.stderr || '').substring(0,200) || (ok ? 'done' : 'failed');
    } else if (type === 'key') {
      const r = await fetch(API+`/actions/act`,
        {method:'POST', headers:{...HEADERS,'Content-Type':'application/json'},
         body:JSON.stringify({action:'key', key:input})});
      const d = await r.json(); ok = d.success !== false; msg = d.message || (ok?'sent':'failed');
    } else if (type === 'type') {
      const r = await fetch(API+`/actions/act`,
        {method:'POST', headers:{...HEADERS,'Content-Type':'application/json'},
         body:JSON.stringify({action:'type', text:input})});
      const d = await r.json(); ok = d.success !== false; msg = d.message || (ok?'typed':'failed');
    } else {
      // act — treat input as target for click
      const r = await fetch(API+`/actions/act`,
        {method:'POST', headers:{...HEADERS,'Content-Type':'application/json'},
         body:JSON.stringify({action:'click', target:input})});
      const d = await r.json(); ok = d.success !== false; msg = d.message || (ok?'clicked':'failed');
    }
  } catch(e) { msg = String(e); }

  if (res) {
    res.textContent = msg;
    res.style.color = ok ? 'var(--accent)' : 'var(--danger)';
  }
}

// ── Recording ─────────────────────────────────────────────────────────────────
let _activeRecId = null;

async function toggleRecording() {
  if (_activeRecId) {
    // Stop
    try {
      const r = await fetch(API+`/recording/stop/${_activeRecId}`,{method:'POST',headers:HEADERS});
      const d = await r.json();
      _activeRecId = null;
      $('rec-btn').textContent = 'Start';
      $('rec-status-badge').textContent = 'off';
      $('rec-status-badge').style.color = 'var(--dim)';
      const paths = Object.values(d.paths||{});
      const recPath = paths.length ? paths[0] : '';
      const recFile = recPath ? recPath.split(/[\\/]/).pop() : '';
      setText('rec-info', recFile ? recFile + ` (${d.size_mb}MB)` : 'saved');
    } catch(e) { setText('rec-info', 'Stop failed: '+e); }
  } else {
    // Start
    const fmt = $('rec-fmt')?.value || 'gif';
    const dur = parseInt($('rec-duration')?.value || '30');
    try {
      const r = await fetch(API+'/recording/start', {
        method:'POST', headers:{...HEADERS,'Content-Type':'application/json'},
        body: JSON.stringify({format:fmt, max_seconds:dur, fps:2}),
      });
      if (!r.ok) { const e=await r.json(); setText('rec-info','Error: '+(e.detail||r.status)); return; }
      const d = await r.json();
      _activeRecId = d.id;
      $('rec-btn').textContent = 'Stop';
      $('rec-status-badge').textContent = 'REC';
      $('rec-status-badge').style.color = 'var(--danger)';
      setText('rec-info', `${d.id.substring(0,8)} · ${fmt.toUpperCase()} · max ${dur}s`);
    } catch(e) { setText('rec-info', 'Start failed: '+e); }
  }
}

// ── Controls ─────────────────────────────────────────────────────────────────
function refreshAll() {
  checkHealth(); pollVision(); pollWorld(); pollPerception(); pollSpatial(); pollSystem(); pollAgents(); pollOcr();
}

function copyApiUrl() {
  navigator.clipboard.writeText(API).then(() => {
    const btn = document.querySelector('.btn-row .btn');
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    setTimeout(() => btn.textContent = orig, 1500);
  });
}

// ── Start ─────────────────────────────────────────────────────────────────────
refreshAll();
setInterval(pollVision, 500);
setInterval(() => { pollWorld(); pollPerception(); pollSystem(); checkHealth(); }, 2000);
setInterval(pollSpatial, 5000);
setInterval(pollAgents, 4000);
setInterval(pollOcr, 3000);   // OCR refreshes on active tab every 3s
</script>
</body>
</html>'''
