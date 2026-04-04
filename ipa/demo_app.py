"""IPA v3 — Real Eyes Demo App

Web dashboard: screen capture left, AI descriptions right.
IPA captures frames, compresses keyframes, serves via SSE.
Connected AI (via MCP) describes what it sees.

Usage:
    python -m ipa.demo_app                    # Start on port 8450
    python -m ipa.demo_app --port 8450 --fps 3

Then open http://localhost:8450 in your browser.
"""
from __future__ import annotations
import argparse
import asyncio
import base64
import io
import json
import sys
import threading
import time
from collections import deque
from datetime import datetime

import numpy as np
from PIL import Image

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# ── IPA Capture Thread ──────────────────────────────────────────────────────

class LiveCapture:
    """Captures screen frames, compresses, tracks motion, accumulates OCR."""

    def __init__(self, fps: int = 3, device: str = "cuda", target_window: str = ""):
        self.fps = fps
        self.device = device
        self.target_window = target_window
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

        # State
        self.current_thumbnail_b64: str = ""
        self.current_motion: str = "initializing"
        self.current_patches: int = 0
        self.frame_count: int = 0
        self.fps_actual: float = 0.0
        self.latency_ms: float = 0.0
        self.memory_kb: float = 0.0
        self.descriptions: deque = deque(maxlen=50)
        self.events: deque = deque(maxlen=100)  # SSE events

        # IPA engine
        self._engine = None

    def __init_target(self):
        self.target_window: str = ""  # Set via --window arg or auto-detect

    def _find_target_window(self) -> int:
        """Find target window handle by title substring."""
        import ctypes, ctypes.wintypes

        target = self.target_window.lower()
        if not target:
            return 0

        result = [0]
        def callback(hwnd, _):
            if ctypes.windll.user32.IsWindowVisible(hwnd):
                length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
                if length > 0:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    ctypes.windll.user32.GetWindowTextW(hwnd, buf, length + 1)
                    title = buf.value.lower()
                    if target in title and "real eyes" not in title:
                        result[0] = hwnd
                        return False  # Stop enumeration
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.c_void_p)
        ctypes.windll.user32.EnumWindows(WNDENUMPROC(callback), 0)
        return result[0]

    def _capture_window(self, hwnd: int) -> Image.Image | None:
        """Capture a specific window by handle using Win32 PrintWindow."""
        if not hwnd:
            return None
        try:
            import ctypes
            from ctypes import wintypes

            # Get window rect
            rect = wintypes.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            w = rect.right - rect.left
            h = rect.bottom - rect.top
            if w <= 0 or h <= 0:
                return None

            # Use mss to capture that specific region
            import mss
            with mss.mss() as sct:
                region = {"top": rect.top, "left": rect.left, "width": w, "height": h}
                shot = sct.grab(region)
                return Image.frombytes("RGB", shot.size, shot.rgb)
        except Exception:
            return None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def add_description(self, text: str):
        """Called by the AI via MCP when it describes a frame."""
        with self._lock:
            self.descriptions.append({
                "time": datetime.now().strftime("%H:%M:%S"),
                "text": text,
            })
            self._push_event("description", {"text": text})

    def _push_event(self, event_type: str, data: dict):
        data["type"] = event_type
        data["time"] = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.events.append(data)

    def _loop(self):
        import mss
        from ipa.engine import IPAEngine

        self._engine = IPAEngine(config={"device": self.device, "int8": False})
        interval = 1.0 / self.fps
        start = time.time()
        last_motion = ""

        # Window capture via Win32 (captures specific window, not monitor)
        capture_hwnd = self._find_target_window()

        with mss.mss() as sct:
            mon = sct.monitors[1]
            while self._running:
                t0 = time.perf_counter()

                # Capture target window (or fallback to monitor)
                img = self._capture_window(capture_hwnd)
                if img is None:
                    # Retry finding window
                    capture_hwnd = self._find_target_window()
                    shot = sct.grab(mon)
                    img = Image.frombytes("RGB", shot.size, shot.rgb)

                # IPA process
                frame = self._engine.feed(img, metadata={"window_name": "live"})
                motion = self._engine.motion(seconds=3)
                pipeline_ms = (time.perf_counter() - t0) * 1000

                # Compress thumbnail
                ratio = 480 / max(img.size)
                thumb = img.resize((int(img.size[0]*ratio), int(img.size[1]*ratio)), Image.LANCZOS)
                buf = io.BytesIO()
                thumb.save(buf, format="WEBP", quality=60)
                thumb_b64 = base64.b64encode(buf.getvalue()).decode()

                elapsed = time.time() - start

                with self._lock:
                    self.current_thumbnail_b64 = thumb_b64
                    self.current_motion = motion.motion_type
                    self.current_patches = frame.n_changed
                    self.frame_count += 1
                    self.latency_ms = pipeline_ms
                    self.fps_actual = self.frame_count / max(elapsed, 0.1)
                    self.memory_kb = self._engine.status()["stream"]["memory_kb"]

                    # Push events
                    self._push_event("frame", {
                        "motion": motion.motion_type,
                        "speed": round(motion.speed, 2),
                        "patches": frame.n_changed,
                        "frame_type": frame.frame_type,
                        "latency_ms": round(pipeline_ms),
                        "detail": motion.detail,
                    })

                    # Scene change event
                    if motion.motion_type != last_motion and last_motion:
                        self._push_event("scene_change", {
                            "from": last_motion,
                            "to": motion.motion_type,
                        })
                    last_motion = motion.motion_type

                # Sleep to maintain FPS
                elapsed_frame = time.perf_counter() - t0
                sleep_time = interval - elapsed_frame
                if sleep_time > 0:
                    time.sleep(sleep_time)

    def get_keyframe_b64(self) -> str:
        """Get current compressed keyframe for AI vision."""
        with self._lock:
            return self.current_thumbnail_b64

    def get_state(self) -> dict:
        with self._lock:
            return {
                "motion": self.current_motion,
                "patches": self.current_patches,
                "frame_count": self.frame_count,
                "fps": round(self.fps_actual, 1),
                "latency_ms": round(self.latency_ms),
                "memory_kb": round(self.memory_kb),
                "descriptions": list(self.descriptions),
            }


# ── HTML Dashboard ──────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>IPA v3 — Real Eyes</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { background: #0a0a0f; color: #e0e0e0; font-family: 'Segoe UI', system-ui, sans-serif; height: 100vh; overflow: hidden; }

  .header {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
    border-bottom: 1px solid #21ff5e33;
    padding: 12px 24px;
    display: flex; align-items: center; justify-content: space-between;
  }
  .header h1 { font-size: 18px; color: #21ff5e; font-weight: 600; letter-spacing: 1px; }
  .header h1 span { color: #e0e0e0; }
  .stats { display: flex; gap: 20px; font-size: 12px; color: #888; }
  .stats .val { color: #21ff5e; font-weight: 600; }

  .main { display: flex; height: calc(100vh - 52px); }

  .left {
    flex: 1; display: flex; flex-direction: column; border-right: 1px solid #21ff5e22;
  }
  .left .screen-container {
    flex: 1; display: flex; align-items: center; justify-content: center; padding: 8px;
    background: #000;
  }
  .left img { max-width: 100%; max-height: 100%; border-radius: 4px; }

  .left .motion-bar {
    padding: 10px 16px; background: #0d1117; border-top: 1px solid #21ff5e22;
    font-size: 13px; display: flex; gap: 16px; align-items: center;
  }
  .motion-type {
    padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600;
    text-transform: uppercase; letter-spacing: 0.5px;
  }
  .motion-type.video { background: #9333ea33; color: #c084fc; }
  .motion-type.scroll_down, .motion-type.scroll_up { background: #0891b233; color: #67e8f9; }
  .motion-type.typing { background: #ca8a0433; color: #fbbf24; }
  .motion-type.cursor { background: #ca8a0433; color: #fbbf24; }
  .motion-type.static { background: #33333366; color: #666; }
  .motion-type.loading { background: #06b6d433; color: #22d3ee; }
  .motion-type.interaction { background: #3b82f633; color: #60a5fa; }

  .right {
    width: 420px; display: flex; flex-direction: column; background: #0d1117;
  }
  .right h2 {
    padding: 12px 16px; font-size: 14px; color: #21ff5e; border-bottom: 1px solid #21ff5e22;
    font-weight: 600; letter-spacing: 0.5px;
  }
  .descriptions {
    flex: 1; overflow-y: auto; padding: 8px;
  }
  .desc-item {
    padding: 10px 12px; margin-bottom: 6px; border-radius: 8px;
    background: #161b22; border-left: 3px solid #21ff5e;
    animation: fadeIn 0.3s ease;
  }
  .desc-item .time { font-size: 11px; color: #21ff5e88; margin-bottom: 4px; }
  .desc-item .text { font-size: 13px; line-height: 1.5; color: #d0d0d0; }

  .event-item {
    padding: 6px 12px; margin-bottom: 3px; border-radius: 6px;
    background: #1a1a2e; font-size: 12px; color: #888;
    border-left: 2px solid #333;
  }
  .event-item.scene_change { border-left-color: #c084fc; color: #c084fc; }
  .event-item .time { color: #555; }

  .events-section {
    border-top: 1px solid #21ff5e22; max-height: 200px; overflow-y: auto; padding: 8px;
  }
  .events-section h3 { font-size: 12px; color: #666; padding: 4px 8px; text-transform: uppercase; letter-spacing: 1px; }

  @keyframes fadeIn { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }

  .instructions {
    padding: 16px; background: #161b22; border-radius: 8px; margin: 8px;
    border: 1px solid #21ff5e22; font-size: 12px; color: #888; line-height: 1.6;
  }
  .instructions code { background: #0d1117; padding: 2px 6px; border-radius: 3px; color: #21ff5e; font-size: 11px; }
</style>
</head>
<body>

<div class="header">
  <h1>IPA <span>v3</span> — <span style="color:#21ff5e">Real Eyes</span></h1>
  <div class="stats">
    <span>FPS: <span class="val" id="stat-fps">0</span></span>
    <span>Latency: <span class="val" id="stat-latency">0</span>ms</span>
    <span>Patches: <span class="val" id="stat-patches">0</span></span>
    <span>Frames: <span class="val" id="stat-frames">0</span></span>
    <span>RAM: <span class="val" id="stat-memory">0</span>KB</span>
  </div>
</div>

<div class="main">
  <div class="left">
    <div class="screen-container">
      <img id="screen-img" src="" alt="Waiting for capture...">
    </div>
    <div class="motion-bar">
      <span class="motion-type static" id="motion-badge">STARTING</span>
      <span id="motion-detail" style="color:#666; font-size:12px;">Initializing IPA...</span>
    </div>
  </div>

  <div class="right">
    <h2>AI DESCRIPTIONS</h2>
    <div class="descriptions" id="descriptions">
      <div class="instructions">
        <strong>How it works:</strong><br>
        1. IPA captures your screen at 3fps<br>
        2. Connect an AI via MCP<br>
        3. AI calls <code>GET /ipa/keyframe</code> to see<br>
        4. AI posts description to <code>POST /ipa/describe</code><br>
        5. Descriptions appear here in real-time<br><br>
        Or use: <code>curl localhost:8450/ipa/keyframe</code> to get the current frame as base64 image
      </div>
    </div>
    <div class="events-section">
      <h3>Events</h3>
      <div id="events"></div>
    </div>
  </div>
</div>

<script>
const img = document.getElementById('screen-img');
const descDiv = document.getElementById('descriptions');
const eventsDiv = document.getElementById('events');
const motionBadge = document.getElementById('motion-badge');
const motionDetail = document.getElementById('motion-detail');
let lastDescCount = 0;
let hasInstructions = true;

async function poll() {
  try {
    const res = await fetch('/ipa/state');
    const data = await res.json();

    // Update stats
    document.getElementById('stat-fps').textContent = data.fps;
    document.getElementById('stat-latency').textContent = data.latency_ms;
    document.getElementById('stat-patches').textContent = data.patches;
    document.getElementById('stat-frames').textContent = data.frame_count;
    document.getElementById('stat-memory').textContent = data.memory_kb;

    // Update motion badge
    motionBadge.textContent = data.motion.toUpperCase();
    motionBadge.className = 'motion-type ' + data.motion;

    // Update descriptions
    if (data.descriptions.length > lastDescCount) {
      if (hasInstructions) {
        descDiv.innerHTML = '';
        hasInstructions = false;
      }
      for (let i = lastDescCount; i < data.descriptions.length; i++) {
        const d = data.descriptions[i];
        const el = document.createElement('div');
        el.className = 'desc-item';
        el.innerHTML = '<div class="time">' + d.time + '</div><div class="text">' + d.text + '</div>';
        descDiv.appendChild(el);
        descDiv.scrollTop = descDiv.scrollHeight;
      }
      lastDescCount = data.descriptions.length;
    }
  } catch(e) {}
}

async function pollImage() {
  try {
    const res = await fetch('/ipa/thumbnail');
    const data = await res.json();
    if (data.image) {
      img.src = 'data:image/webp;base64,' + data.image;
    }
  } catch(e) {}
}

// SSE for events
const evtSource = new EventSource('/ipa/events');
evtSource.onmessage = (e) => {
  try {
    const data = JSON.parse(e.data);
    const el = document.createElement('div');
    el.className = 'event-item' + (data.type === 'scene_change' ? ' scene_change' : '');

    if (data.type === 'frame') {
      el.innerHTML = '<span class="time">' + data.time + '</span> ' +
        data.motion + ' speed=' + data.speed + ' patches=' + data.patches + ' ' + data.frame_type;
    } else if (data.type === 'scene_change') {
      el.innerHTML = '<span class="time">' + data.time + '</span> SCENE: ' + data.from + ' → ' + data.to;
    } else if (data.type === 'description') {
      el.innerHTML = '<span class="time">' + data.time + '</span> AI: ' + data.text.substring(0, 60) + '...';
    }
    eventsDiv.insertBefore(el, eventsDiv.firstChild);
    if (eventsDiv.children.length > 50) eventsDiv.removeChild(eventsDiv.lastChild);
  } catch(e) {}
};

setInterval(poll, 1000);
setInterval(pollImage, 500);
poll();
pollImage();
</script>
</body>
</html>"""


# ── FastAPI Server ──────────────────────────────────────────────────────────

def create_app(capture: LiveCapture):
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse

    app = FastAPI(title="IPA v3 Real Eyes Demo")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return DASHBOARD_HTML

    @app.get("/ipa/state")
    async def get_state():
        return JSONResponse(capture.get_state())

    @app.get("/ipa/thumbnail")
    async def get_thumbnail():
        return JSONResponse({"image": capture.get_keyframe_b64()})

    @app.get("/ipa/keyframe")
    async def get_keyframe():
        """Returns current keyframe as base64 — for AI vision consumption."""
        b64 = capture.get_keyframe_b64()
        state = capture.get_state()
        return JSONResponse({
            "image_base64": b64,
            "motion": state["motion"],
            "patches": state["patches"],
            "fps": state["fps"],
            "latency_ms": state["latency_ms"],
            "frame_count": state["frame_count"],
            "mime_type": "image/webp",
        })

    @app.post("/ipa/describe")
    async def post_description(body: dict):
        """AI posts its description of what it sees."""
        text = body.get("text", "")
        if text:
            capture.add_description(text)
        return JSONResponse({"ok": True})

    @app.get("/ipa/events")
    async def sse_events():
        """Server-Sent Events stream for real-time updates."""
        async def event_stream():
            last_idx = 0
            while True:
                events = list(capture.events)
                if len(events) > last_idx:
                    for evt in events[last_idx:]:
                        yield f"data: {json.dumps(evt)}\n\n"
                    last_idx = len(events)
                await asyncio.sleep(0.3)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return app


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="IPA v3 — Real Eyes Demo App")
    parser.add_argument("--port", type=int, default=8450)
    parser.add_argument("--fps", type=int, default=3)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--window", default="youtube", help="Window title to capture (e.g. 'youtube', 'vlc')")
    args = parser.parse_args()

    print(f"\n  IPA v3 — Real Eyes Demo")
    print(f"  Device: {args.device} | FPS: {args.fps} | Port: {args.port}")
    print(f"  Target window: '{args.window}'")
    print(f"  Dashboard: http://localhost:{args.port}")
    print(f"  Keyframe API: http://localhost:{args.port}/ipa/keyframe")
    print(f"  Describe API: POST http://localhost:{args.port}/ipa/describe")
    print()

    capture = LiveCapture(fps=args.fps, device=args.device, target_window=args.window)
    capture.start()

    app = create_app(capture)

    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
