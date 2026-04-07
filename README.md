<p align="center">
  <img src="logo.svg" alt="ILUMINATY" width="180"/>
</p>

<h1 align="center">ILUMINATY</h1>

<p align="center">
  <strong>Real-time visual perception + PC control for AI agents.</strong><br/>
  Local MCP server. Zero cloud. Zero disk. AI sees your screen — all monitors — live.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-00ff88?style=flat-square&labelColor=0a0a12" alt="Version"/>
  <img src="https://img.shields.io/badge/IPA-v3-00ff88?style=flat-square&labelColor=0a0a12" alt="IPA v3"/>
  <img src="https://img.shields.io/badge/MCP_tools-22-00ff88?style=flat-square&labelColor=0a0a12" alt="MCP Tools"/>
  <img src="https://img.shields.io/badge/multi--monitor-3%2B-00ff88?style=flat-square&labelColor=0a0a12" alt="Multi-Monitor"/>
  <img src="https://github.com/sgodoy90/iluminaty/actions/workflows/tests.yml/badge.svg" alt="Tests"/>
  <img src="https://img.shields.io/badge/license-MIT-00ff88?style=flat-square&labelColor=0a0a12" alt="License"/>
</p>

---

## What Is This?

ILUMINATY is a local MCP server that gives any AI (Claude, GPT-4o, Cursor, etc.) **real-time visual perception and OS-level control** of your desktop — without sending screenshots to the cloud.

The AI doesn't guess coordinates. It reads structured data from a continuously running perception engine that watches your screen and turns raw pixels into semantic events, spatial context, OCR text, and OS state. When the AI needs to *see* something specific, it gets a real screen image with exact dimensions so coordinates scale correctly. When it needs to *act*, it resolves element names through the OS accessibility tree — no coordinate estimation.

---

## Quick Start

```bash
pip install iluminaty[ocr]
iluminaty start --api-key my_key
```

Server starts on `:8420`, auto-detects all monitors:

```
  Profile:   balanced (CPU only)
  API:       http://127.0.0.1:8420
  FPS:       2 active | 0.3 inactive (adaptive: True)
  Buffer:    30s (60 slots)
  Monitors:  AUTO (3 monitors, per-monitor capture)
  Auth:      enabled
  Disk:      ZERO (RAM-only ring buffer)
```

**Connect to Claude Code** — add to `.mcp.json`:

```json
{
  "mcpServers": {
    "iluminaty": {
      "command": "python",
      "args": ["-m", "iluminaty.mcp_server"],
      "env": {
        "ILUMINATY_API_URL": "http://127.0.0.1:8420",
        "ILUMINATY_KEY": "my_key"
      }
    }
  }
}
```

Or run `iluminaty mcp-config` to write the config automatically.

> **No registration. No cloud account. No tiers.**  
> All 22 MCP tools work immediately after `pip install`.

---

## Install from Source

```bash
git clone https://github.com/sgodoy90/iluminaty
cd iluminaty
pip install -e ".[ocr]"
iluminaty start --api-key my_key
```

**Optional flags:**

| Flag | Default | Description |
|---|---|---|
| `--port` | `8420` | HTTP port |
| `--fps` | `5.0` | Capture rate per monitor |
| `--api-key` | _(none)_ | Auth token — required unless `ILUMINATY_NO_AUTH=1` |
| `--audio` | `off` | Audio capture: `off`, `system`, `mic`, `all` |
| `--profile` | `balanced` | Resource profile: `low_power`, `balanced`, `performance` |
| `--monitor` | `0` | `0` = all monitors (auto), `N` = pin to monitor N |
| `--max-width` | `1280` | Max frame width in pixels |

**Resource profiles:**

| Profile | FPS | fast_loop | OCR interval | Typical CPU | Use when |
|---|---|---|---|---|---|
| `low_power` | 1 active / 0.1 idle | 3 Hz | 60s | ~5% | Laptop, no GPU, 5+ monitors |
| `balanced` | 2 active / 0.3 idle | 5 Hz | 30s | ~15% | Most desktops (default) |
| `performance` | 5 active / 0.5 idle | 10 Hz | 10s | ~30% | GPU available |

---

## Architecture

```
Physical Screens (1–N monitors)
         │
         ▼  adaptive FPS per monitor
[MultiMonitorCapture]
  mss screenshot per monitor
  active monitor: full FPS | inactive: 0.3fps (sublinear scaling)
  WM_DISPLAYCHANGE → zero-poll monitor hot-plug (Windows)
         │
         ▼
[RingBuffer]  ←── RAM only, zero disk
  ~60 slots  ─── ~30s of history
  per-monitor frame tagging (monitor_id)
  MD5 fast-path + histogram change_score (0.0–1.0)
         │
         ├───────────────────────────────────────┐
         ▼                                       ▼
[IPABridge]                           [PerceptionEngine]
  VisualEncoder (imagehash)             Fast loop (8–12 Hz): semantic updates
  DeltaCompressor (int8 numpy)          Deep loop (0.5–2 Hz): VLM scheduling
  VisualStream (patch timeline)         7 IPA classes per monitor:
  gate events: motion_start,              SceneStateMachine
    motion_end, content_loaded            AttentionMap (8×6 grid)
                                          ROITracker
                                          KeyframeDetector
                                          TemporalEventFuser
                                          CapturePredictor
                                          MonitorPerceptionState
         │                                       │
         └───────────────┬───────────────────────┘
                         ▼
[FastAPI :8420]  +  [routes/]
  152 endpoints across 20 route modules
  /vision/*   /action/*   /perception/*
  /grounding/*  /workers/*  /monitors/*
  WebSocket /ws/stream
         │
         ▼
[MCP stdio — mcp_server.py]
  22 tools | persistent HTTP keep-alive (30s TTL)
         │
         ▼
   Claude / GPT-4o / Cursor / any MCP client
```

**Key design choices:**

- **RAM-only ring buffer**: `collections.deque` with per-monitor frame tagging. No temp files. Zero disk I/O on the hot path. Everything disappears when the process dies.
- **Coordinate precision**: `vision/smart` always returns `width`/`height`. `act()` with `monitor=` routes through `/vision/click_at` which scales image-space coords → native monitor coords correctly. `act_on(target=)` uses the OS accessibility tree — pixel-perfect, no coordinate math needed.
- **Persistent HTTP connection pool**: `mcp_server.py` reuses one TCP connection across all MCP tool calls. Eliminates 1–3ms TCP handshake per call.
- **CPU throttle**: background thread monitors system CPU every 10s. When CPU > threshold (default 80%), the fast perception loop slows automatically without stopping.
- **Monitor hot-plug**: `WM_DISPLAYCHANGE` daemon thread (Windows) — zero polling. Auto-triggers `reinitialize_monitors()` on plug/unplug/resolution change. `/monitors/refresh` for Linux/Mac.
- **fast_ocr**: `fast_ocr.py` replaces the old subprocess OCR worker. OCR runs in-process via RapidOCR — no subprocess spawn, no IPC overhead.

---

## IPA v3 — Intelligent Perception Algorithm

IPA runs continuously in background, processing your screen through a **4-gate pipeline**:

```
Frame
  │
  ├─ Gate 0: Window change detection (ctypes)               < 0.1ms
  ├─ Gate 1: Histogram change_score 0.0–1.0 (per monitor)  < 0.5ms
  ├─ Gate 2: Perceptual hash (imagehash phash)              < 1ms
  ├─ Gate 3: Optical flow (Farneback 480p) + SmartDiff      5–25ms
  └─ Gate 4: OCR diff (RapidOCR, throttled)                 50–200ms
```

**7 IPA classes running independently per monitor:**

| Class | Role |
|---|---|
| `SceneStateMachine` | IDLE / TYPING / SCROLLING / LOADING / VIDEO / TRANSITION / INTERACTION — with evidence accumulation and dwell-time hysteresis |
| `AttentionMap` | 8×6 spatial heatmap with 0.92/frame decay — tracks where screen activity concentrates |
| `ROITracker` | Up to 6 active regions of interest — created after 3 consecutive active frames, expire after 10s idle |
| `KeyframeDetector` | Marks scene boundaries (change_score > 0.40, window changes, loading_complete transitions) |
| `TemporalEventFuser` | Merges raw events into composite narratives: navigation, rapid switching, scroll+settle, editing |
| `CapturePredictor` | Autocorrelation FPS advisor — max during LOADING, min during IDLE |
| `MonitorPerceptionState` | Independent state machine per physical monitor — active monitors analyzed every frame, inactive sampled with bounded staleness |

**Two independent loops:**
- **Fast loop** (8–12 Hz): low-latency semantic updates, never waits for VLM
- **Deep loop** (0.5–2 Hz): enqueues prioritized visual tasks for local VLM, active monitor gets priority

---

## Coordinate Precision

**The problem**: when the AI receives a downscaled image (e.g. 768px wide) from a 1920px monitor, pixel coordinates in the image are NOT the same as desktop coordinates.

**The fix**: ILUMINATY enforces a precision hierarchy:

```
1. BEST  → act_on(target='button name', action='click')
           OS accessibility tree — pixel-perfect, never misses, any app

2. GOOD  → click_at(x, y, monitor_id, image_w, image_h)
           Explicit image→native scaling: 768px→1920px = 2.5× auto-applied

3. OK    → act(action='click', x, y, monitor=N, image_w=W, image_h=H)
           Internally routes to click_at — correct scaling

4. AVOID → act(action='click', x, y, monitor=N)  WITHOUT image_w/image_h
           Raw coordinates passed as-is — causes 400px misses on scaled images
```

`vision/smart` always returns `width` and `height` in the response so the AI can pass them. Every `see_now` call includes an `[IMAGE COORDS]` block reminding the AI which method to use.

---

## MCP Tools (22 active)

All 22 tools available to everyone. No registration required.

### 👁 Vision — seeing the screen

| Tool | Params | Description |
|---|---|---|
| `see_now` | `mode`, `monitor` | **Primary vision tool.** Current screen image + IPA scene context. `mode`: `low_res` (320px), `medium_res` (768px, default), `full_res` (native). Always returns `width`/`height` for coordinate scaling. |
| `see_region` | `x`, `y`, `width`, `height`, `monitor`, `scale` | Full-resolution crop of any screen region at 1–4× upscale. Read tooltips, menus, small text. ~500–1,500 tokens vs ~15K for full frame. |
| `what_changed` | `seconds`, `monitor` | What changed in the last N seconds. IPA gate events (`motion_start`, `content_loaded`) + image of the most significant moment. |
| `zoom` | `x1`, `y1`, `x2`, `y2`, `monitor_id`, `image_w`, `image_h` | Zoom into a region with a pixel coordinate grid overlay. Use after `see_now` to identify exact pixel positions before clicking. |
| `click_at` | `x`, `y`, `monitor_id`, `image_w`, `image_h`, `button`, `double` | Click at image-space coords from `see_now`. Pass `image_w`/`image_h` — auto-scales to native monitor resolution (e.g. 768px→1920px = 2.5×). |

### 🗺 Spatial & Perception

| Tool | Params | Description |
|---|---|---|
| `get_spatial_context` | _(none)_ | **Call at session start.** Full workspace map: physical monitor layout with positions/resolutions, all visible windows per monitor, cursor position, active app, user activity phase. |
| `map_environment` | `monitor`, `scale`, `grid` | Visual grounding snapshot — monitor layout + active windows annotated with grid overlay. Use before acting on multi-monitor setups. |
| `watch_and_notify` | `condition`, `timeout`, `text`, `element`, `window_title`, `idle_seconds`, `monitor` | Wait for a screen condition without consuming tokens. Returns when triggered. Conditions: `text_appeared`, `text_disappeared`, `window_opened`, `window_closed`, `motion_stopped`, `motion_started`, `build_passed`, `build_failed`, `element_visible`, `idle`, `page_loaded`. |
| `verify_action` | `action_description`, `monitor_id`, `wait_ms` | Verify a recent action had a visual effect. Returns `success`, `confidence`, and path to an evidence screenshot. Call after every action. |
| `screen_status` | _(none)_ | Buffer stats: FPS, slots used, memory MB, frames captured, capture running state, active window. |

### 🎯 OS-Native UI Automation — zero-coordinate targeting

Works in any Windows app with accessibility support (Win32, WPF, WinForms, Electron, Chrome, Office).

| Tool | Params | Description |
|---|---|---|
| `act_on` | `target`, `action`, `text`, `option`, `window_title`, `nth`, `submit` | **Best for UI interaction.** Click, type, check, uncheck, or select an element **by name** — no coordinates. OS finds it, verifies focus after click, retries if autocomplete delays. Actions: `click`, `type`, `check`, `uncheck`, `select`. |
| `uia_find_all` | `window_title`, `monitor` | List all interactive elements (buttons, inputs, checkboxes, combos) in the active window with OS-verified coords. Maps an entire form in one call. |
| `uia_focused` | _(none)_ | Ask the OS which element has keyboard focus right now. Use before typing to confirm the correct field. ~3–5ms. |
| `find_on_screen` | `query`, `monitor` | Locate an element by text description via UIAutomation + OCR. Returns global `(x, y)` ready to pass to `act`. |

**Example — fill a form without touching a single coordinate:**
```
act_on(target="Customer name",   action="type",  text="ILUMINATY Agent")
act_on(target="Email",           action="type",  text="agent@iluminaty.dev")
act_on(target="Small",           action="check")
act_on(target="Bacon",           action="check")
act_on(target="Submit order",    action="click")
```

### ⚡ Actions — direct OS control

| Tool | Params | Description |
|---|---|---|
| `act` | `action`, `target`, `x`, `y`, `image_w`, `image_h`, `text`, `keys`, `button`, `amount`, `duration`, `direction`, `role`, `monitor` | Direct mouse + keyboard executor. Actions: `click`, `double_click`, `triple_click`, `right_click`, `middle_click`, `mouse_down`, `mouse_up`, `type`, `key`, `hold_key`, `scroll`, `move_mouse`, `focus`, `wait`. Pass `target=` for UITree+OCR locate, or `x,y,monitor,image_w,image_h` for image-coord click (auto-scaled). |

### 🪟 Windows & System

| Tool | Params | Description |
|---|---|---|
| `list_windows` | `monitor`, `title_contains`, `exclude_minimized` | All visible windows with handle, title, position, size, monitor ID. Use for handle lookup — always cross-check with `see_now` for visual reality. |
| `focus_window` | `title`, `handle`, `prefer_active_monitor` | Bring a window to front by handle or title substring. |
| `open_path` | `path`, `monitor` | Open a file or folder via Win+R → type → Enter → verify. Use this instead of `run_command` for opening files. |
| `run_command` | `command`, `timeout` | Execute a shell command. Returns stdout/stderr/exit code. 38+ destructive patterns blocked. For terminal commands only — not for opening files. |
| `read_file` | `path` | Read file contents. Sandboxed to `~/Documents`, `~/Desktop`, `~/iluminaty-workspace`. |
| `write_file` | `path`, `content` | Write file with auto-backup. Same sandbox as `read_file`. |
| `os_dialog_resolve` | `strategy` | Dismiss or confirm a blocking system dialog (save/open/confirm) by strategy name. |

---

## Smart Locate

When the AI calls `act(action="click", target="Save button")`, ILUMINATY resolves coordinates without pixel-guessing:

```
Resolution hierarchy (fastest first):

1. OCR cache          (~0ms)   — pre-computed text blocks from last fast_ocr pass
2. UIAutomation tree  (~5ms)   — Windows Accessibility API (COM native, not PowerShell)
3. returns None                — caller falls back to visual estimate

Confidence threshold: 0.65 (OCR) / 0.55 (UITree)
Returns: LocateResult { x, y, w, h, source, confidence, monitor_id }
```

---

## Security

- **Auth**: `--api-key <token>` at startup. All HTTP requests and WebSocket connections require `X-API-Key` header or `ILUMINATY_KEY` env var.
- **Shell sandbox**: `run_command` blocks 38+ destructive patterns — `rm -rf`, `format`, registry deletes, PowerShell download cradles, WMI execution, fork bombs.
- **File sandbox**: `read_file`/`write_file` restricted to `~/Documents`, `~/Desktop`, `~/iluminaty-workspace`. Claude/Cursor config dirs blocked from writes.
- **Prompt injection guard**: every `see_now` OCR read scans for 20+ injection patterns. HIGH severity blocks execution and warns the agent.
- **WebSocket auth**: `/ws/stream` authenticates before `accept()` — unauthorized connections close with code `4401`.
- **Audit log**: records every action with timestamp and tool name. No raw frames stored.
- **`ILUMINATY_NO_AUTH=1`**: disables all auth — only for local dev, never on shared machines.

---

## Domain Packs

Specialize ILUMINATY's semantic interpretation for specific apps via `.toml` files in `domain_packs/`:

```toml
[pack]
name = "tradingview"

[detection]
url_keywords = ["tradingview.com"]
text_keywords = ["btcusd", "rsi", "macd"]

[semantics]
task_context = "financial-trading"
readiness_signals = ["price updated", "chart loaded"]
```

Example packs included: `tradingview.toml.example`, `vscode.toml.example`.

---

## Audio (Optional)

```bash
iluminaty start --audio system   # system audio
iluminaty start --audio all      # system + microphone
```

Audio is stored in a RAM ring buffer (same zero-disk model as video). VAD marks chunks with `is_speech=true`. The `AudioInterruptDetector` blocks AI typing actions when the user is speaking.

---

## Testing

```bash
pip install -e ".[ocr]"
pytest          # 97 passed
pytest -v       # verbose
```

CI: GitHub Actions on Windows with Python 3.11 and 3.12.

Four test files are excluded from the default run (require a live server or real screen):
- `test_perception_deep_loop_focus.py`
- `test_server_precheck.py`
- `test_server_stability.py`
- `test_watch_memory_integration.py`

---

## Requirements

| | Minimum | Recommended |
|---|---|---|
| OS | Windows 10 | Windows 11 |
| Python | 3.10 | 3.12 |
| RAM | 2 GB | 8 GB (3 monitors) |
| CPU | Any | 4+ cores |
| GPU | Not required | NVIDIA/AMD for local VLM |
| Network | None | None — fully local |

macOS and Linux: screen capture + perception work; window management (`user32.dll`) is Windows-only.

---

## Project Structure

```
iluminaty/
├── main.py              # Entry point — argparse, profiles, server boot
├── server.py            # FastAPI app, _ServerState, init_server, core endpoints
├── mcp_server.py        # MCP stdio server — 22 tool handlers, HTTP keep-alive pool
├── routes/              # 20 route modules (split from server.py)
│   ├── actions.py       audio.py        agent.py
│   ├── annotations.py   clipboard.py    files.py
│   ├── grounding.py     ipa.py          monitors.py
│   ├── os_surface.py    perception.py   process.py
│   ├── safety.py        system.py       tokens.py
│   ├── ui.py            watch.py        watchdog.py
│   ├── windows.py       workers.py
├── uia_backend.py       # Cross-platform UI Automation (Windows UIA / macOS AX / Linux AT-SPI2)
├── perception.py        # IPA 4-gate pipeline — 7 classes, fast+deep loops
├── vision.py            # EnrichedFrame builder + OCR proxy
├── fast_ocr.py          # In-process RapidOCR (replaces subprocess worker)
├── ocr_worker.py        # Legacy subprocess OCR — still used by vision.py
├── ring_buffer.py       # RAM-only circular frame buffer with per-monitor isolation
├── multi_capture.py     # Multi-monitor capture orchestrator
├── capture.py           # Single-monitor screen capture (mss) + adaptive FPS + burst
├── workers.py           # In-process worker system (MonitorWorker, SpatialWorker, etc.)
├── watch_engine.py      # Event-driven wait conditions — push-based, zero polling
├── domain_packs.py      # TOML-based app specialization
├── smart_locate.py      # OCR + UIAutomation coordinate resolver
├── grounding.py         # Hybrid grounding engine (UITree + OCR + visual)
├── actions.py           # OS action bridge (pyautogui) — click, type, drag, hotkey
├── windows.py           # Window management (user32.dll)
├── ui_tree.py           # Accessibility tree walker
├── world_state.py       # WorldState engine (task phase, affordances, readiness)
├── temporal_store.py    # Temporal visual store (frame refs + semantic transitions)
├── visual_engine.py     # VLM task queue (on_demand / continuous mode)
├── ipa_bridge.py        # IPA v3 bridge — connects ring buffer to VisualEncoder
├── security.py          # Auth, rate limiting, sensitive content detection
├── audio.py             # Audio ring buffer + VAD + interrupt detector
├── host_telemetry.py    # CPU/GPU/memory monitoring + policy checks
├── app_behavior_cache.py # Per-app action outcome learning (SQLite)
├── verifier.py          # Post-action verification
├── recording.py         # Opt-in session recording (disabled by default)
├── safety.py            # Safety system — kill switch, whitelist, rate limiting
├── audit.py             # Audit log — timestamps, actions, no frames stored
├── monitors.py          # MonitorManager — layout, active detection, hot-plug
└── dashboard.py         # Web dashboard HTML

ipa/
├── engine.py     # IPAEngine — main orchestrator
├── encoder.py    # VisualEncoder — imagehash patch encoding
├── compressor.py # DeltaCompressor — I/P frame compression (int8 numpy)
├── stream.py     # VisualStream — temporal patch buffer
└── types.py      # PatchFrame, MotionField, VisualContext

domain_packs/
├── tradingview.toml.example
└── vscode.toml.example

tests/           # 97 tests across 36 files
ipa/tests/       # 39 tests (compressor, engine, stream)
```

---

## License

MIT — free for personal and commercial use.

Built by [@sgodoy90](https://github.com/sgodoy90)
