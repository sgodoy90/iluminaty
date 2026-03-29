"""
ILUMINATY - Dashboard Web
===========================
UI en vivo para ver el stream, stats, y dibujar anotaciones.
Se sirve desde el mismo servidor FastAPI — zero dependencias extra.
"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ILUMINATY - Live Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    background: #0a0a0f;
    color: #e0e0e0;
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    overflow: hidden;
    height: 100vh;
  }

  /* Header */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 16px;
    background: #111118;
    border-bottom: 1px solid #222;
    height: 48px;
  }
  .header h1 {
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 2px;
    color: #00ff88;
  }
  .header .status {
    display: flex;
    gap: 16px;
    font-size: 12px;
    color: #888;
  }
  .header .status .live {
    color: #ff3333;
    font-weight: bold;
    animation: pulse 1.5s infinite;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  /* Main layout */
  .main {
    display: flex;
    height: calc(100vh - 48px);
  }

  /* Canvas area */
  .canvas-area {
    flex: 1;
    position: relative;
    background: #000;
    display: flex;
    align-items: center;
    justify-content: center;
    overflow: hidden;
  }
  #streamCanvas {
    max-width: 100%;
    max-height: 100%;
    cursor: crosshair;
  }
  .no-signal {
    color: #333;
    font-size: 24px;
    letter-spacing: 4px;
    position: absolute;
  }

  /* Sidebar */
  .sidebar {
    width: 280px;
    background: #111118;
    border-left: 1px solid #222;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
  }
  .panel {
    padding: 12px;
    border-bottom: 1px solid #1a1a22;
  }
  .panel h3 {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 1.5px;
    color: #00ff88;
    margin-bottom: 8px;
  }

  /* Stats */
  .stat-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
  }
  .stat {
    background: #0d0d14;
    padding: 8px;
    border-radius: 4px;
    border: 1px solid #1a1a22;
  }
  .stat .label { font-size: 10px; color: #666; text-transform: uppercase; }
  .stat .value { font-size: 16px; font-weight: 700; color: #fff; font-variant-numeric: tabular-nums; }
  .stat .value.green { color: #00ff88; }
  .stat .value.yellow { color: #ffcc00; }
  .stat .value.red { color: #ff3333; }

  /* Tools */
  .tools {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
  }
  .tool-btn {
    padding: 6px 10px;
    background: #1a1a24;
    border: 1px solid #333;
    border-radius: 4px;
    color: #ccc;
    cursor: pointer;
    font-size: 12px;
    transition: all 0.15s;
  }
  .tool-btn:hover { background: #252530; border-color: #00ff88; }
  .tool-btn.active { background: #00ff8822; border-color: #00ff88; color: #00ff88; }

  /* Color picker */
  .color-row {
    display: flex;
    gap: 4px;
    margin-top: 6px;
  }
  .color-dot {
    width: 20px;
    height: 20px;
    border-radius: 50%;
    cursor: pointer;
    border: 2px solid transparent;
    transition: border-color 0.15s;
  }
  .color-dot.active, .color-dot:hover { border-color: #fff; }

  /* Annotations list */
  .ann-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 4px 6px;
    background: #0d0d14;
    border-radius: 3px;
    margin-bottom: 3px;
    font-size: 11px;
  }
  .ann-item .del {
    color: #ff3333;
    cursor: pointer;
    font-weight: bold;
    padding: 0 4px;
  }

  /* Controls */
  .controls {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .ctrl-btn {
    padding: 6px 12px;
    background: #1a1a24;
    border: 1px solid #333;
    border-radius: 4px;
    color: #ccc;
    cursor: pointer;
    font-size: 11px;
    transition: all 0.15s;
  }
  .ctrl-btn:hover { background: #252530; }
  .ctrl-btn.danger { border-color: #ff3333; color: #ff3333; }
  .ctrl-btn.danger:hover { background: #ff333322; }
  .ctrl-btn.primary { border-color: #00ff88; color: #00ff88; }
  .ctrl-btn.primary:hover { background: #00ff8822; }

  /* Config */
  .config-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
    font-size: 12px;
  }
  .config-row input, .config-row select {
    background: #0d0d14;
    border: 1px solid #333;
    color: #fff;
    padding: 3px 6px;
    border-radius: 3px;
    width: 80px;
    font-size: 12px;
  }

  /* Window info */
  .window-info {
    font-size: 11px;
    color: #888;
    word-break: break-all;
  }
  .window-info strong { color: #ccc; }
</style>
</head>
<body>

<div class="header">
  <h1>ILUMINATY</h1>
  <div class="status">
    <span class="live" id="liveIndicator">* LIVE</span>
    <span id="fpsDisplay">-- fps</span>
    <span id="ramDisplay">-- MB</span>
    <span id="formatDisplay">--</span>
    <span id="resDisplay">--</span>
  </div>
</div>

<div class="main">
  <!-- Stream view -->
  <div class="canvas-area">
    <canvas id="streamCanvas"></canvas>
    <div class="no-signal" id="noSignal">NO SIGNAL</div>
  </div>

  <!-- Sidebar -->
  <div class="sidebar">

    <!-- Stats -->
    <div class="panel">
      <h3>Buffer Stats</h3>
      <div class="stat-grid">
        <div class="stat">
          <div class="label">Slots</div>
          <div class="value" id="statSlots">--</div>
        </div>
        <div class="stat">
          <div class="label">RAM</div>
          <div class="value green" id="statRam">--</div>
        </div>
        <div class="stat">
          <div class="label">FPS</div>
          <div class="value yellow" id="statFps">--</div>
        </div>
        <div class="stat">
          <div class="label">Frame</div>
          <div class="value" id="statFrameSize">--</div>
        </div>
        <div class="stat">
          <div class="label">Captured</div>
          <div class="value" id="statTotal">--</div>
        </div>
        <div class="stat">
          <div class="label">Dropped</div>
          <div class="value green" id="statDropped">--</div>
        </div>
      </div>
    </div>

    <!-- Active window -->
    <div class="panel">
      <h3>Active Window</h3>
      <div class="window-info" id="windowInfo">--</div>
    </div>

    <!-- Drawing tools -->
    <div class="panel">
      <h3>Annotation Tools</h3>
      <div class="tools">
        <button class="tool-btn active" data-tool="rect">[ ] Rect</button>
        <button class="tool-btn" data-tool="circle">O Circle</button>
        <button class="tool-btn" data-tool="arrow">> Arrow</button>
        <button class="tool-btn" data-tool="text">T Text</button>
        <button class="tool-btn" data-tool="freehand">~ Free</button>
      </div>
      <div class="color-row">
        <div class="color-dot active" style="background:#FF0000" data-color="#FF0000"></div>
        <div class="color-dot" style="background:#FFFF00" data-color="#FFFF00"></div>
        <div class="color-dot" style="background:#00FF88" data-color="#00FF88"></div>
        <div class="color-dot" style="background:#00AAFF" data-color="#00AAFF"></div>
        <div class="color-dot" style="background:#FF00FF" data-color="#FF00FF"></div>
        <div class="color-dot" style="background:#FFFFFF" data-color="#FFFFFF"></div>
      </div>
    </div>

    <!-- Annotations list -->
    <div class="panel">
      <h3>Active Annotations</h3>
      <div id="annList"><span style="font-size:11px;color:#555">None</span></div>
      <div style="margin-top:8px">
        <button class="ctrl-btn danger" onclick="clearAnnotations()">Clear All</button>
      </div>
    </div>

    <!-- Config -->
    <div class="panel">
      <h3>Config</h3>
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
               style="width:100px" oninput="document.getElementById('cfgQualityVal').textContent=this.value"
               onchange="updateConfig()">
        <span id="cfgQualityVal">80</span>
      </div>
      <div class="config-row">
        <span>FPS</span>
        <input type="number" id="cfgFps" value="1" min="0.2" max="10" step="0.5" onchange="updateConfig()">
      </div>
      <div class="config-row">
        <span>Max Width</span>
        <input type="number" id="cfgWidth" value="1280" min="640" max="3840" step="320" onchange="updateConfig()">
      </div>
    </div>

    <!-- Controls -->
    <div class="panel">
      <h3>Controls</h3>
      <div class="controls">
        <button class="ctrl-btn primary" onclick="toggleCapture()">Start/Stop</button>
        <button class="ctrl-btn danger" onclick="flushBuffer()">Flush Buffer</button>
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
let currentColor = '#FF0000';
let isDrawing = false;
let drawStart = null;
let annotations = [];
let lastFrameUrl = null;
let streamActive = true;

// ─── Stream Loop ───
async function streamLoop() {
  while (true) {
    if (!streamActive) { await sleep(500); continue; }
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
          drawAnnotationsOverlay();
          URL.revokeObjectURL(url);
          noSignal.style.display = 'none';
        };
        img.src = url;
      }
    } catch(e) {}
    await sleep(300);
  }
}

// ─── Stats Loop ───
async function statsLoop() {
  while (true) {
    try {
      const res = await fetch(API + '/buffer/stats');
      if (res.ok) {
        const d = await res.json();
        document.getElementById('statSlots').textContent = d.slots_used + '/' + d.slots_max;
        document.getElementById('statRam').textContent = d.memory_mb + ' MB';
        document.getElementById('statFps').textContent = d.current_fps.toFixed(1);
        document.getElementById('statTotal').textContent = d.total_frames_captured;
        document.getElementById('statDropped').textContent = d.frames_dropped_no_change;
        document.getElementById('fpsDisplay').textContent = d.current_fps.toFixed(1) + ' fps';
        document.getElementById('ramDisplay').textContent = d.memory_mb + ' MB';
      }

      // Active window
      const wres = await fetch(API + '/vision/window');
      if (wres.ok) {
        const w = await wres.json();
        document.getElementById('windowInfo').innerHTML =
          '<strong>' + escapeHtml(w.title || 'unknown') + '</strong><br>PID: ' + w.pid;
      }

      // Frame info
      const fres = await fetch(API + '/frame/latest?base64=true');
      if (fres.ok) {
        const f = await fres.json();
        document.getElementById('statFrameSize').textContent = (f.size_bytes / 1024).toFixed(0) + ' KB';
        document.getElementById('formatDisplay').textContent = f.format.split('/')[1].toUpperCase();
        document.getElementById('resDisplay').textContent = f.width + 'x' + f.height;
      }
    } catch(e) {}
    await sleep(1000);
  }
}

// ─── Annotations on Canvas ───
function drawAnnotationsOverlay() {
  // Draw current annotations as overlay on canvas
  for (const ann of annotations) {
    ctx.strokeStyle = ann.color;
    ctx.lineWidth = 3;
    ctx.fillStyle = ann.color;

    if (ann.type === 'rect') {
      ctx.strokeRect(ann.x, ann.y, ann.w, ann.h);
    } else if (ann.type === 'circle') {
      ctx.beginPath();
      const r = Math.max(Math.abs(ann.w), Math.abs(ann.h)) / 2;
      ctx.arc(ann.x + ann.w/2, ann.y + ann.h/2, r, 0, Math.PI * 2);
      ctx.stroke();
    } else if (ann.type === 'arrow') {
      ctx.beginPath();
      ctx.moveTo(ann.x, ann.y);
      ctx.lineTo(ann.x + ann.w, ann.y + ann.h);
      ctx.stroke();
      // arrowhead
      const angle = Math.atan2(ann.h, ann.w);
      const headLen = 15;
      ctx.beginPath();
      ctx.moveTo(ann.x + ann.w, ann.y + ann.h);
      ctx.lineTo(ann.x + ann.w - headLen * Math.cos(angle - 0.4), ann.y + ann.h - headLen * Math.sin(angle - 0.4));
      ctx.lineTo(ann.x + ann.w - headLen * Math.cos(angle + 0.4), ann.y + ann.h - headLen * Math.sin(angle + 0.4));
      ctx.closePath();
      ctx.fill();
    } else if (ann.type === 'text') {
      ctx.font = 'bold 18px sans-serif';
      ctx.fillText(ann.label || 'Note', ann.x, ann.y + 18);
    }
  }
}

// ─── Canvas Drawing ───
canvas.addEventListener('mousedown', (e) => {
  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  drawStart = {
    x: Math.round((e.clientX - rect.left) * scaleX),
    y: Math.round((e.clientY - rect.top) * scaleY)
  };
  isDrawing = true;
});

canvas.addEventListener('mouseup', async (e) => {
  if (!isDrawing || !drawStart) return;
  isDrawing = false;

  const rect = canvas.getBoundingClientRect();
  const scaleX = canvas.width / rect.width;
  const scaleY = canvas.height / rect.height;
  const endX = Math.round((e.clientX - rect.left) * scaleX);
  const endY = Math.round((e.clientY - rect.top) * scaleY);

  const w = endX - drawStart.x;
  const h = endY - drawStart.y;

  let textLabel = '';
  if (currentTool === 'text') {
    textLabel = prompt('Enter annotation text:', 'Look here') || 'Note';
  }

  // Send to server
  const params = new URLSearchParams({
    type: currentTool,
    x: Math.min(drawStart.x, endX),
    y: Math.min(drawStart.y, endY),
    width: Math.abs(w) || 50,
    height: Math.abs(h) || 50,
    color: encodeURIComponent(currentColor),
    text: textLabel
  });

  try {
    const res = await fetch(API + '/annotations/add?' + params, { method: 'POST' });
    if (res.ok) {
      const data = await res.json();
      annotations.push({
        id: data.id,
        type: currentTool,
        x: Math.min(drawStart.x, endX),
        y: Math.min(drawStart.y, endY),
        w: Math.abs(w) || 50,
        h: Math.abs(h) || 50,
        color: currentColor,
        label: textLabel
      });
      refreshAnnList();
    }
  } catch(e) { console.error(e); }

  drawStart = null;
});

// Live preview while drawing
canvas.addEventListener('mousemove', (e) => {
  if (!isDrawing || !drawStart) return;
  // Redraw happens on next stream frame
});

// ─── Tool selection ───
document.querySelectorAll('.tool-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tool-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    currentTool = btn.dataset.tool;
  });
});

document.querySelectorAll('.color-dot').forEach(dot => {
  dot.addEventListener('click', () => {
    document.querySelectorAll('.color-dot').forEach(d => d.classList.remove('active'));
    dot.classList.add('active');
    currentColor = dot.dataset.color;
  });
});

// ─── Annotations List ───
function refreshAnnList() {
  const list = document.getElementById('annList');
  if (annotations.length === 0) {
    list.innerHTML = '<span style="font-size:11px;color:#555">None</span>';
    return;
  }
  list.innerHTML = annotations.map(a => {
    const delBtn = '<span class="del" onclick="removeAnnotation(' + "'" + a.id + "'" + ')">x</span>';
    return '<div class="ann-item"><span>' + a.type + ' at (' + a.x + ',' + a.y + ')</span>' + delBtn + '</div>';
  }).join('');
}

async function removeAnnotation(id) {
  await fetch(API + '/annotations/' + id, { method: 'DELETE' });
  annotations = annotations.filter(a => a.id !== id);
  refreshAnnList();
}

async function clearAnnotations() {
  await fetch(API + '/annotations/clear', { method: 'POST' });
  annotations = [];
  refreshAnnList();
}

// ─── Config ───
async function updateConfig() {
  const params = new URLSearchParams({
    image_format: document.getElementById('cfgFormat').value,
    quality: document.getElementById('cfgQuality').value,
    fps: document.getElementById('cfgFps').value,
    max_width: document.getElementById('cfgWidth').value,
  });
  await fetch(API + '/config?' + params, { method: 'POST' });
}

// ─── Controls ───
let captureRunning = true;
async function toggleCapture() {
  const endpoint = captureRunning ? '/capture/stop' : '/capture/start';
  await fetch(API + endpoint, { method: 'POST' });
  captureRunning = !captureRunning;
  document.getElementById('liveIndicator').textContent = captureRunning ? '* LIVE' : 'o PAUSED';
  document.getElementById('liveIndicator').style.color = captureRunning ? '#ff3333' : '#555';
}

async function flushBuffer() {
  if (!confirm('Destroy all visual data in buffer?')) return;
  await fetch(API + '/buffer/flush', { method: 'POST' });
}

// ─── Utils ───
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function escapeHtml(s) { return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ─── Start ───
streamLoop();
statsLoop();
</script>
</body>
</html>"""
