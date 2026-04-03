"""
ILUMINATY - Dashboard v2
===========================
Professional dark UI with cyberpunk-meets-minimal aesthetic.
Eye of Providence (all-seeing eye) brand identity.
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
    --bg-deep: #06060b;
    --bg-primary: #0a0a12;
    --bg-surface: #0f0f1a;
    --bg-elevated: #141422;
    --bg-hover: #1a1a2e;
    --border: #1e1e35;
    --border-active: #2a2a4a;
    --text-primary: #e8e8f0;
    --text-secondary: #8888a8;
    --text-muted: #555570;
    --accent: #00ff88;
    --accent-dim: #00cc6a;
    --accent-glow: rgba(0,255,136,0.15);
    --accent-purple: #7b61ff;
    --accent-blue: #4488ff;
    --danger: #ff4466;
    --warning: #ffaa22;
    --mono: 'JetBrains Mono', monospace;
    --sans: 'Inter', -apple-system, sans-serif;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: var(--bg-deep);
    color: var(--text-primary);
    font-family: var(--sans);
    height: 100vh;
    overflow: hidden;
    -webkit-font-smoothing: antialiased;
  }

  /* ─── Header ─── */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 20px;
    height: 52px;
    background: var(--bg-primary);
    border-bottom: 1px solid var(--border);
    position: relative;
    z-index: 10;
  }

  .header::after {
    content: '';
    position: absolute;
    bottom: -1px;
    left: 0;
    right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent 0%, var(--accent) 50%, transparent 100%);
    opacity: 0.3;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .logo-eye {
    width: 32px;
    height: 32px;
    position: relative;
  }

  .logo-eye svg {
    width: 100%;
    height: 100%;
  }

  .brand-name {
    font-family: var(--mono);
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 3px;
    color: var(--accent);
    text-shadow: 0 0 20px var(--accent-glow);
  }

  .brand-version {
    font-size: 10px;
    color: var(--text-muted);
    font-family: var(--mono);
    padding: 2px 6px;
    border: 1px solid var(--border);
    border-radius: 3px;
  }

  .header-status {
    display: flex;
    align-items: center;
    gap: 20px;
    font-family: var(--mono);
    font-size: 12px;
    color: var(--text-secondary);
  }

  .status-dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent);
    box-shadow: 0 0 8px var(--accent);
    animation: pulse-dot 2s infinite;
    margin-right: 6px;
  }

  @keyframes pulse-dot {
    0%, 100% { opacity: 1; box-shadow: 0 0 8px var(--accent); }
    50% { opacity: 0.5; box-shadow: 0 0 4px var(--accent); }
  }

  .status-item { display: flex; align-items: center; gap: 4px; }
  .status-value { color: var(--text-primary); font-weight: 600; }

  /* ─── Main Layout ─── */
  .main {
    display: grid;
    grid-template-columns: 1fr 300px;
    height: calc(100vh - 52px);
  }

  /* ─── Stream View ─── */
  .stream-area {
    position: relative;
    background: var(--bg-deep);
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
    border-right: 1px solid var(--border);
  }

  #streamCanvas {
    max-width: 100%;
    max-height: 100%;
    border-radius: 2px;
    cursor: crosshair;
  }

  .no-signal {
    position: absolute;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
    color: var(--text-muted);
  }

  .no-signal .eye-icon { opacity: 0.15; }

  .no-signal-text {
    font-family: var(--mono);
    font-size: 13px;
    letter-spacing: 4px;
    text-transform: uppercase;
  }

  /* ─── Sidebar ─── */
  .sidebar {
    background: var(--bg-primary);
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--border) transparent;
  }

  .sidebar::-webkit-scrollbar { width: 4px; }
  .sidebar::-webkit-scrollbar-track { background: transparent; }
  .sidebar::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }

  .panel {
    padding: 16px;
    border-bottom: 1px solid var(--border);
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
  }

  .panel-title {
    font-family: var(--mono);
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 2px;
    text-transform: uppercase;
    color: var(--text-muted);
  }

  .panel-badge {
    font-family: var(--mono);
    font-size: 9px;
    padding: 2px 6px;
    border-radius: 3px;
    background: var(--accent-glow);
    color: var(--accent);
    border: 1px solid rgba(0,255,136,0.2);
  }

  /* ─── Stats Grid ─── */
  .stats-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }

  .stat-card {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
    transition: border-color 0.2s;
  }

  .stat-card:hover { border-color: var(--border-active); }

  .stat-label {
    font-size: 10px;
    color: var(--text-muted);
    font-family: var(--mono);
    letter-spacing: 0.5px;
    text-transform: uppercase;
    margin-bottom: 4px;
  }

  .stat-value {
    font-family: var(--mono);
    font-size: 18px;
    font-weight: 700;
    color: var(--text-primary);
    font-variant-numeric: tabular-nums;
  }

  .stat-value.accent { color: var(--accent); }
  .stat-value.warning { color: var(--warning); }

  /* ─── Window Info ─── */
  .window-info {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 10px 12px;
    font-size: 12px;
    color: var(--text-secondary);
    font-family: var(--mono);
    word-break: break-all;
    line-height: 1.5;
  }

  .window-info strong { color: var(--text-primary); }

  /* ─── Tool Buttons ─── */
  .tools-row {
    display: flex;
    gap: 4px;
    flex-wrap: wrap;
  }

  .tool-btn {
    padding: 7px 12px;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 5px;
    color: var(--text-secondary);
    cursor: pointer;
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 500;
    transition: all 0.15s;
    user-select: none;
  }

  .tool-btn:hover {
    background: var(--bg-hover);
    border-color: var(--border-active);
    color: var(--text-primary);
  }

  .tool-btn.active {
    background: var(--accent-glow);
    border-color: var(--accent);
    color: var(--accent);
  }

  /* ─── Color Dots ─── */
  .color-row {
    display: flex;
    gap: 6px;
    margin-top: 10px;
  }

  .color-dot {
    width: 18px;
    height: 18px;
    border-radius: 50%;
    cursor: pointer;
    border: 2px solid transparent;
    transition: all 0.15s;
    box-shadow: inset 0 0 0 1px rgba(255,255,255,0.1);
  }

  .color-dot:hover { transform: scale(1.15); }
  .color-dot.active { border-color: #fff; box-shadow: 0 0 8px rgba(255,255,255,0.3); }

  /* ─── Annotations List ─── */
  .ann-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 8px;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    margin-bottom: 4px;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-secondary);
  }

  .ann-del {
    color: var(--danger);
    cursor: pointer;
    font-weight: bold;
    padding: 0 4px;
    opacity: 0.6;
    transition: opacity 0.15s;
  }

  .ann-del:hover { opacity: 1; }

  /* ─── Control Buttons ─── */
  .ctrl-btn {
    padding: 7px 14px;
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 5px;
    color: var(--text-secondary);
    cursor: pointer;
    font-family: var(--mono);
    font-size: 11px;
    font-weight: 500;
    transition: all 0.15s;
  }

  .ctrl-btn:hover { background: var(--bg-hover); border-color: var(--border-active); }
  .ctrl-btn.primary { border-color: var(--accent); color: var(--accent); }
  .ctrl-btn.primary:hover { background: var(--accent-glow); }
  .ctrl-btn.danger { border-color: var(--danger); color: var(--danger); }
  .ctrl-btn.danger:hover { background: rgba(255,68,102,0.1); }

  /* ─── Config Controls ─── */
  .config-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
    font-size: 12px;
    color: var(--text-secondary);
  }

  .config-row select, .config-row input[type="number"] {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    color: var(--text-primary);
    padding: 4px 8px;
    border-radius: 4px;
    width: 80px;
    font-family: var(--mono);
    font-size: 12px;
    outline: none;
  }

  .config-row select:focus, .config-row input:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent-glow);
  }

  .config-row input[type="range"] {
    -webkit-appearance: none;
    width: 100px;
    height: 4px;
    background: var(--border);
    border-radius: 2px;
    outline: none;
  }

  .config-row input[type="range"]::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--accent);
    cursor: pointer;
    box-shadow: 0 0 6px var(--accent-glow);
  }

  /* ─── VU Meter ─── */
  .vu-meter {
    height: 4px;
    background: var(--bg-surface);
    border-radius: 2px;
    overflow: hidden;
    margin-top: 8px;
  }

  .vu-meter-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--warning), var(--danger));
    border-radius: 2px;
    transition: width 0.15s;
    width: 0%;
  }

  /* ─── Footer overlay ─── */
  .stream-overlay {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    padding: 12px 16px;
    background: linear-gradient(transparent, rgba(6,6,11,0.9));
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    pointer-events: none;
  }

  .stream-overlay .tag {
    font-family: var(--mono);
    font-size: 10px;
    padding: 3px 8px;
    border-radius: 3px;
    background: rgba(0,0,0,0.5);
    color: var(--text-secondary);
    backdrop-filter: blur(4px);
  }

  .stream-overlay .tag.live {
    color: var(--danger);
    border: 1px solid rgba(255,68,102,0.3);
  }
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div class="brand">
    <div class="logo-eye">
      <svg viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
        <!-- Outer eye shape -->
        <path d="M50 20 Q15 50 50 80 Q85 50 50 20 Z" fill="none" stroke="#00ff88" stroke-width="2.5" opacity="0.8"/>
        <!-- Inner circle (iris) -->
        <circle cx="50" cy="50" r="16" fill="none" stroke="#00ff88" stroke-width="2" opacity="0.9"/>
        <!-- Pupil -->
        <circle cx="50" cy="50" r="7" fill="#00ff88" opacity="0.8"/>
        <!-- Light reflection -->
        <circle cx="45" cy="45" r="3" fill="#fff" opacity="0.6"/>
        <!-- Radiance lines -->
        <line x1="50" y1="5" x2="50" y2="15" stroke="#00ff88" stroke-width="1" opacity="0.3"/>
        <line x1="20" y1="15" x2="28" y2="23" stroke="#00ff88" stroke-width="1" opacity="0.2"/>
        <line x1="80" y1="15" x2="72" y2="23" stroke="#00ff88" stroke-width="1" opacity="0.2"/>
      </svg>
    </div>
    <span class="brand-name">ILUMINATY</span>
    <span class="brand-version">v1.0.0</span>
  </div>
  <div class="header-status">
    <div class="status-item"><span class="status-dot"></span><span id="statusText">LIVE</span></div>
    <div class="status-item"><span class="status-value" id="hFps">--</span> fps</div>
    <div class="status-item"><span class="status-value" id="hRam">--</span> MB</div>
    <div class="status-item"><span class="status-value" id="hFormat">--</span></div>
    <div class="status-item"><span class="status-value" id="hRes">--</span></div>
  </div>
</div>

<!-- Main -->
<div class="main">
  <!-- Stream -->
  <div class="stream-area">
    <canvas id="streamCanvas"></canvas>
    <div class="no-signal" id="noSignal">
      <div class="eye-icon">
        <svg width="80" height="80" viewBox="0 0 100 100" xmlns="http://www.w3.org/2000/svg">
          <path d="M50 20 Q15 50 50 80 Q85 50 50 20 Z" fill="none" stroke="#555" stroke-width="2"/>
          <circle cx="50" cy="50" r="16" fill="none" stroke="#555" stroke-width="1.5"/>
          <circle cx="50" cy="50" r="7" fill="#555" opacity="0.5"/>
        </svg>
      </div>
      <span class="no-signal-text">Awaiting vision</span>
    </div>
    <div class="stream-overlay">
      <span class="tag live" id="tagLive">REC</span>
      <span class="tag" id="tagWindow">--</span>
    </div>
  </div>

  <!-- Sidebar -->
  <div class="sidebar">

    <!-- Stats -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Buffer</span>
        <span class="panel-badge" id="efficiency">--% saved</span>
      </div>
      <div class="stats-grid">
        <div class="stat-card">
          <div class="stat-label">Slots</div>
          <div class="stat-value" id="sSlots">--</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">RAM</div>
          <div class="stat-value accent" id="sRam">--</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">FPS</div>
          <div class="stat-value warning" id="sFps">--</div>
        </div>
        <div class="stat-card">
          <div class="stat-label">Frame</div>
          <div class="stat-value" id="sFrame">--</div>
        </div>
      </div>
    </div>

    <!-- Window -->
    <div class="panel">
      <div class="panel-header"><span class="panel-title">Active Window</span></div>
      <div class="window-info" id="windowInfo">--</div>
    </div>

    <!-- Audio -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Audio</span>
        <span class="panel-badge" id="speechBadge">silent</span>
      </div>
      <div class="vu-meter"><div class="vu-meter-fill" id="vuFill"></div></div>
    </div>

    <!-- Tools -->
    <div class="panel">
      <div class="panel-header"><span class="panel-title">Annotate</span></div>
      <div class="tools-row">
        <button class="tool-btn active" data-tool="rect">Rect</button>
        <button class="tool-btn" data-tool="circle">Circle</button>
        <button class="tool-btn" data-tool="arrow">Arrow</button>
        <button class="tool-btn" data-tool="text">Text</button>
      </div>
      <div class="color-row">
        <div class="color-dot active" style="background:#ff4466" data-color="#ff4466"></div>
        <div class="color-dot" style="background:#ffaa22" data-color="#ffaa22"></div>
        <div class="color-dot" style="background:#00ff88" data-color="#00ff88"></div>
        <div class="color-dot" style="background:#4488ff" data-color="#4488ff"></div>
        <div class="color-dot" style="background:#7b61ff" data-color="#7b61ff"></div>
        <div class="color-dot" style="background:#ffffff" data-color="#ffffff"></div>
      </div>
    </div>

    <!-- Annotations -->
    <div class="panel">
      <div class="panel-header">
        <span class="panel-title">Marks</span>
        <button class="ctrl-btn danger" style="padding:3px 8px;font-size:10px" onclick="clearAnnotations()">Clear</button>
      </div>
      <div id="annList"><span style="font-size:11px;color:var(--text-muted)">No annotations</span></div>
    </div>

    <!-- Config -->
    <div class="panel">
      <div class="panel-header"><span class="panel-title">Config</span></div>
      <div class="config-row">
        <span>Format</span>
        <select id="cfgFormat" onchange="updateConfig()">
          <option value="webp">WebP</option>
          <option value="jpeg">JPEG</option>
          <option value="png">PNG</option>
        </select>
      </div>
      <div class="config-row">
        <span>Quality</span>
        <input type="range" id="cfgQuality" min="10" max="95" value="80"
               style="width:100px" oninput="document.getElementById('cfgQVal').textContent=this.value"
               onchange="updateConfig()">
        <span id="cfgQVal" style="font-family:var(--mono);width:24px;text-align:right">80</span>
      </div>
      <div class="config-row">
        <span>FPS</span>
        <input type="number" id="cfgFps" value="1" min="0.2" max="10" step="0.5" onchange="updateConfig()">
      </div>
      <div class="config-row" style="border-top:1px solid var(--border);margin-top:6px;padding-top:6px">
        <span>VLM Device</span>
        <select id="cfgVlmDevice" onchange="updateVlmConfig()">
          <option value="auto">Auto (GPU si hay)</option>
          <option value="cuda">GPU (CUDA)</option>
          <option value="cpu">CPU (sin GPU)</option>
        </select>
      </div>
      <div class="config-row">
        <span>VLM</span>
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="checkbox" id="cfgVlmEnabled" onchange="updateVlmConfig()" checked>
          Activado
        </label>
        <span id="vlmRestartHint" style="font-size:10px;color:#f0a;display:none">Reiniciar para aplicar</span>
      </div>
    </div>

    <!-- Controls -->
    <div class="panel">
      <div style="display:flex;gap:6px">
        <button class="ctrl-btn primary" onclick="toggleCapture()" style="flex:1">Start / Stop</button>
        <button class="ctrl-btn danger" onclick="flushBuffer()">Flush</button>
      </div>
    </div>
  </div>
</div>

<script>
const API = window.location.origin;
const canvas = document.getElementById('streamCanvas');
const ctx = canvas.getContext('2d');
const noSignal = document.getElementById('noSignal');
let currentTool = 'rect';
let currentColor = '#ff4466';
let isDrawing = false;
let drawStart = null;
let annotations = [];
let captureRunning = true;

// ─── Stream ───
async function streamLoop() {
  while (true) {
    try {
      const res = await fetch(API + '/frame/latest');
      if (res.ok) {
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const img = new Image();
        img.onload = () => {
          canvas.width = img.width;
          canvas.height = img.height;
          ctx.drawImage(img, 0, 0);
          drawOverlay();
          URL.revokeObjectURL(url);
          noSignal.style.display = 'none';
        };
        img.src = url;
      }
    } catch(e) {}
    await sleep(250);
  }
}

// ─── Stats ───
async function statsLoop() {
  while (true) {
    try {
      const s = await (await fetch(API + '/buffer/stats')).json();
      document.getElementById('sSlots').textContent = s.slots_used + '/' + s.slots_max;
      document.getElementById('sRam').textContent = s.memory_mb + ' MB';
      document.getElementById('sFps').textContent = s.current_fps.toFixed(1);
      document.getElementById('hFps').textContent = s.current_fps.toFixed(1);
      document.getElementById('hRam').textContent = s.memory_mb;
      document.getElementById('efficiency').textContent = s.efficiency_pct + '% saved';

      const f = await (await fetch(API + '/frame/latest?base64=true')).json();
      document.getElementById('sFrame').textContent = (f.size_bytes/1024).toFixed(0) + 'KB';
      document.getElementById('hFormat').textContent = (f.format||'').split('/')[1]||'--';
      document.getElementById('hRes').textContent = f.width + 'x' + f.height;

      const w = await (await fetch(API + '/vision/window')).json();
      document.getElementById('windowInfo').innerHTML = '<strong>' + esc(w.title||'--') + '</strong>';
      document.getElementById('tagWindow').textContent = (w.title||'').substring(0,40);

      // Audio
      try {
        const a = await (await fetch(API + '/audio/level')).json();
        document.getElementById('vuFill').style.width = Math.min(a.level * 500, 100) + '%';
        document.getElementById('speechBadge').textContent = a.is_speech ? 'speaking' : 'silent';
      } catch(e) {}
    } catch(e) {}
    await sleep(1000);
  }
}

// ─── Canvas Drawing ───
function drawOverlay() {
  for (const a of annotations) {
    ctx.strokeStyle = a.color;
    ctx.fillStyle = a.color;
    ctx.lineWidth = 2.5;
    if (a.type === 'rect') {
      ctx.strokeRect(a.x, a.y, a.w, a.h);
    } else if (a.type === 'circle') {
      const r = Math.max(Math.abs(a.w), Math.abs(a.h)) / 2;
      ctx.beginPath();
      ctx.arc(a.x+a.w/2, a.y+a.h/2, r, 0, Math.PI*2);
      ctx.stroke();
    } else if (a.type === 'arrow') {
      ctx.beginPath();
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(a.x+a.w, a.y+a.h);
      ctx.stroke();
      const ang = Math.atan2(a.h, a.w);
      ctx.beginPath();
      ctx.moveTo(a.x+a.w, a.y+a.h);
      ctx.lineTo(a.x+a.w-12*Math.cos(ang-0.4), a.y+a.h-12*Math.sin(ang-0.4));
      ctx.lineTo(a.x+a.w-12*Math.cos(ang+0.4), a.y+a.h-12*Math.sin(ang+0.4));
      ctx.closePath();
      ctx.fill();
    } else if (a.type === 'text') {
      ctx.font = 'bold 16px Inter, sans-serif';
      ctx.fillText(a.label||'Note', a.x, a.y+16);
    }
  }
}

canvas.addEventListener('mousedown', e => {
  const r = canvas.getBoundingClientRect();
  const sx = canvas.width / r.width, sy = canvas.height / r.height;
  drawStart = { x: Math.round((e.clientX-r.left)*sx), y: Math.round((e.clientY-r.top)*sy) };
  isDrawing = true;
});

canvas.addEventListener('mouseup', async e => {
  if (!isDrawing || !drawStart) return;
  isDrawing = false;
  const r = canvas.getBoundingClientRect();
  const sx = canvas.width/r.width, sy = canvas.height/r.height;
  const ex = Math.round((e.clientX-r.left)*sx), ey = Math.round((e.clientY-r.top)*sy);
  const w = ex-drawStart.x, h = ey-drawStart.y;
  let label = '';
  if (currentTool === 'text') label = prompt('Annotation text:', 'Look here') || 'Note';

  const p = new URLSearchParams({
    type: currentTool, x: Math.min(drawStart.x,ex), y: Math.min(drawStart.y,ey),
    width: Math.abs(w)||50, height: Math.abs(h)||50,
    color: currentColor, text: label
  });
  try {
    const res = await fetch(API+'/annotations/add?'+p, {method:'POST'});
    if (res.ok) {
      const d = await res.json();
      annotations.push({
        id:d.id, type:currentTool,
        x:Math.min(drawStart.x,ex), y:Math.min(drawStart.y,ey),
        w:Math.abs(w)||50, h:Math.abs(h)||50,
        color:currentColor, label
      });
      refreshAnnList();
    }
  } catch(e) {}
  drawStart = null;
});

// ─── Tools ───
document.querySelectorAll('.tool-btn').forEach(b => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.tool-btn').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    currentTool = b.dataset.tool;
  });
});

document.querySelectorAll('.color-dot').forEach(d => {
  d.addEventListener('click', () => {
    document.querySelectorAll('.color-dot').forEach(x => x.classList.remove('active'));
    d.classList.add('active');
    currentColor = d.dataset.color;
  });
});

// ─── Annotations List ───
function refreshAnnList() {
  const el = document.getElementById('annList');
  if (!annotations.length) { el.innerHTML = '<span style="font-size:11px;color:var(--text-muted)">No annotations</span>'; return; }
  el.innerHTML = annotations.map(a => {
    const del = '<span class="ann-del" onclick="removeAnn(' + "'" + a.id + "'" + ')">x</span>';
    return '<div class="ann-item"><span>' + a.type + ' (' + a.x + ',' + a.y + ')</span>' + del + '</div>';
  }).join('');
}

async function removeAnn(id) {
  await fetch(API+'/annotations/'+id, {method:'DELETE'});
  annotations = annotations.filter(a => a.id !== id);
  refreshAnnList();
}

async function clearAnnotations() {
  await fetch(API+'/annotations/clear', {method:'POST'});
  annotations = [];
  refreshAnnList();
}

// ─── Config ───
async function updateConfig() {
  const p = new URLSearchParams({
    image_format: document.getElementById('cfgFormat').value,
    quality: document.getElementById('cfgQuality').value,
    fps: document.getElementById('cfgFps').value,
  });
  await fetch(API+'/config?'+p, {method:'POST'});
}

async function updateVlmConfig() {
  const device = document.getElementById('cfgVlmDevice').value;
  const enabled = document.getElementById('cfgVlmEnabled').checked;
  const p = new URLSearchParams({ vlm_device: device, vlm_enabled: enabled });
  const res = await fetch(API+'/config/vlm?'+p, {method:'POST'});
  const d = await res.json();
  if (d.ok) {
    document.getElementById('vlmRestartHint').style.display = 'inline';
  }
}

async function loadVlmConfig() {
  try {
    const res = await fetch(API+'/config/vlm');
    const d = await res.json();
    if (d.vlm_device) document.getElementById('cfgVlmDevice').value = d.vlm_device;
    if (d.vlm_enabled !== undefined) document.getElementById('cfgVlmEnabled').checked = d.vlm_enabled;
  } catch {}
}
loadVlmConfig();

async function toggleCapture() {
  const ep = captureRunning ? '/capture/stop' : '/capture/start';
  await fetch(API+ep, {method:'POST'});
  captureRunning = !captureRunning;
  document.getElementById('statusText').textContent = captureRunning ? 'LIVE' : 'PAUSED';
  document.getElementById('tagLive').textContent = captureRunning ? 'REC' : 'OFF';
}

async function flushBuffer() {
  if (!confirm('Destroy all visual data?')) return;
  await fetch(API+'/buffer/flush', {method:'POST'});
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function esc(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

streamLoop();
statsLoop();
</script>
</body>
</html>'''
